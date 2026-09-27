# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Critical image contracts: ownership, zero-call rejection, history and failover."""
import io

import pytest
import pytest_asyncio
from fastapi import FastAPI, File, Form, UploadFile as APIUploadFile
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from pydantic_ai import BinaryContent
from starlette.datastructures import UploadFile

from app.atlasclaw.agent.compaction import CompactionPipeline, CompactionConfig
from app.atlasclaw.agent.history_memory import HistoryMemoryCoordinator
from app.atlasclaw.agent.model_capabilities import image_capabilities
from app.atlasclaw.agent.stream import StreamEvent
from app.atlasclaw.agent.token_policy import DynamicTokenPolicy
from app.atlasclaw.api.routes import APIContext, install_request_validation_logging
from app.atlasclaw.api.services.run_service import execute_agent_run, init_run
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.chat_attachments import ChatAttachments, ImageInputError, image_prompt
from app.atlasclaw.core.token_pool import TokenEntry, TokenPool
from app.atlasclaw.core.trace import sanitize_log_value
from app.atlasclaw.db.database import DatabaseConfig, init_database
from app.atlasclaw.db.orm.model_config import ModelConfigService
from app.atlasclaw.db.schemas import ModelConfigCreate, ModelConfigUpdate
from app.atlasclaw.session.context import SessionKey
from app.atlasclaw.session.manager import SessionManager
from app.atlasclaw.session.queue import SessionQueue
from app.atlasclaw.skills.registry import SkillRegistry


@pytest_asyncio.fixture
async def db(tmp_path):
    manager = await init_database(DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "images.db")))
    await manager.create_tables()
    yield manager
    await manager.close()


def png():
    file = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(file, format="PNG")
    return UploadFile(io.BytesIO(file.getvalue()), filename="image.png")


def test_image_request_validation_returns_422():
    app = FastAPI()
    install_request_validation_logging(app)

    @app.post("/images")
    async def upload(session_key: str = Form(...), files: list[APIUploadFile] = File(...)):
        return {}

    @app.post("/model")
    async def update_model(config: ModelConfigUpdate):
        return {}

    with TestClient(app) as client:
        response = client.post("/images", files={"files": ("image.png", b"image", "image/png")})
        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "session_key"]
        for invalid in ({"capabilities": {"vision": "false"}}, {"max_tokens": "invalid"}):
            response = client.post("/model", json=invalid)
            assert response.status_code == 422
            assert response.json()["detail"]


@pytest.mark.asyncio
async def test_attachment_validation_ownership_and_lifecycle(db, tmp_path):
    store = ChatAttachments(tmp_path, "alice")
    refs = await store.upload("session-a", [png()])
    identifier = refs[0]["id"]
    with pytest.raises(ImageInputError, match="attachment_not_found"):
        await ChatAttachments(tmp_path, "bob").content(identifier)
    with pytest.raises(ImageInputError, match="attachment_not_found"):
        await store.resolve("session-b", [identifier])
    with pytest.raises(ImageInputError, match="invalid_image"):
        await store.upload("session-a", [UploadFile(io.BytesIO(b"not a png"), filename="fake.png")])
    with pytest.raises(ImageInputError, match="too_many_images"):
        await store.upload("session-a", [png() for _ in range(5)])
    await store.resolve("session-a", [identifier], bind=True)
    await store.delete(identifier=identifier)  # Bound history cannot be deleted as a draft.
    assert (await store.content(identifier))[1] == "image/png"
    await store.delete(session_key="session-a")
    with pytest.raises(ImageInputError, match="attachment_not_found"):
        await store.content(identifier)


@pytest.mark.asyncio
async def test_runtime_declared_capability_rejects_without_model_call_and_refreshes(db, tmp_path):
    async with db.get_session() as session:
        row = await ModelConfigService.create(session, ModelConfigCreate(
            name="vision", provider="custom", model_id="model", base_url="http://test.invalid", capabilities={"vision": False},
        ))
        row_id = row.id
    token = TokenEntry("vision", "custom", "model", "http://test.invalid", "", source_model_config_id=row_id)
    pool = TokenPool(); pool.register_token(token)

    class Runner:
        token_policy = DynamicTokenPolicy(pool, primary_token_id="vision")
        calls = 0
        async def run(self, **kwargs):
            self.calls += 1
            assert isinstance(next(iter(kwargs["deps"].extra["_chat_image_inputs"].values())), BinaryContent)
            yield StreamEvent.assistant_delta("image seen")
            yield StreamEvent.runtime_update("answered", "Final answer ready.")
            yield StreamEvent.lifecycle_end()

    runner = Runner()
    ctx = APIContext(session_manager=SessionManager(workspace_path=str(tmp_path)),
                     session_queue=SessionQueue(), skill_registry=SkillRegistry(), agent_runner=runner)
    key = SessionKey(user_id="alice").to_string()
    manager = ctx.session_manager_router.for_user("alice")
    await manager.get_or_create(key)
    refs = await ChatAttachments(tmp_path, "alice").upload(key, [png()])
    for enabled in (False, True, False):
        async with db.get_session() as session:
            await ModelConfigService.update(session, row_id, ModelConfigUpdate(capabilities={"vision": enabled}))
        assert (await image_capabilities(runner))["vision"] is enabled
        run_id = f"{enabled}-{runner.calls}"
        init_run(ctx, run_id, key, "", 30)
        ctx.active_runs[run_id]["attachments"] = refs
        before = runner.calls
        await execute_agent_run(ctx, run_id, key, "", 30, user_info=UserInfo(user_id="alice"))
        assert runner.calls == before + int(enabled)
        assert ctx.active_runs[run_id]["status"] == "completed"
    with pytest.raises(ValidationError):
        ModelConfigUpdate(capabilities={"vision": "false"})


def test_native_image_history_roundtrip_and_trace_redaction():
    ref = {"id": "image-1", "name": "sample.png", "size": 10}
    image = BinaryContent(data=b"private-image-bytes", media_type="image/png", identifier=ref["id"],
                          vendor_metadata={"chat_attachment": ref})
    history = HistoryMemoryCoordinator(None, CompactionPipeline(CompactionConfig()))
    messages = [{"role": "user", "content": "", "metadata": {"attachments": [ref]}}]
    native = history.to_model_message_history(messages, image_inputs={ref["id"]: image})
    restored = history.normalize_messages(native)
    assert restored == messages
    extra = {"_chat_attachments": [ref], "_chat_image_inputs": {ref["id"]: image}}
    assert image_prompt("", extra)[1] is image
    assert image_prompt("follow up", extra, native) == "follow up"
    assert "private-image-bytes" not in str(restored)
    assert "secret-base64" not in str(sanitize_log_value({"url": "data:image/png;base64,secret-base64"}))
    assert "private-image-bytes" not in str(sanitize_log_value(image))


def test_failover_never_selects_text_only_candidate():
    pool = TokenPool()
    for name, priority in [("primary", 10), ("text-only", 100), ("image-fallback", 1)]:
        pool.register_token(TokenEntry(name, "custom", name, "", "", priority=priority))
    policy = DynamicTokenPolicy(pool, primary_token_id="primary")
    assert policy.get_or_select_session_token("session").token_id == "primary"
    assert policy.mark_session_token_unhealthy(
        "session", eligible_token_ids={"image-fallback"},
    ).token_id == "image-fallback"
    assert policy.mark_session_token_unhealthy("session", eligible_token_ids=set()) is None
