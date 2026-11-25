# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Agent invocation host for provider backend extensions (Standard v1, section 6.5)."""
from __future__ import annotations

import asyncio
import logging
import mimetypes
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Sequence

from app.atlasclaw.api.services.run_service import execute_agent_run, init_run
from app.atlasclaw.auth.guards import has_skill_access
from app.atlasclaw.auth.models import UserInfo
from app.atlasclaw.core.chat_documents import document_prompt, sanitize_filename
from app.atlasclaw.core.provider_host_services import (
    ProviderArtifact,
    ProviderFileRef,
    ProviderSessionSpec,
    ProviderUser,
)
from app.atlasclaw.core.security_guard import ensure_user_work_dir
from app.atlasclaw.core.user_paths import normalize_runtime_user_id
from app.atlasclaw.core.workspace_downloads import (
    collect_workspace_download_references_from_payloads,
)
from app.atlasclaw.core.provider_http_runtime import ProviderHttpRuntimeError

logger = logging.getLogger(__name__)

MAX_CONCURRENT_RUNS_PER_PROVIDER_USER = 2
DEFAULT_RUN_TIMEOUT_SECONDS = 900
MAX_RUN_TIMEOUT_SECONDS = 3600

_RUNS: dict[str, "ProviderRunState"] = {}
_TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})


@dataclass
class ProviderRunState:
    """Mutable state for one provider-initiated agent run."""

    run_id: str
    provider_type: str
    user_id: str
    session_key: str
    skills: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"
    phase: str = "planning"
    progress: int = 0
    message: str = ""
    final_answer: str = ""
    error: Optional[dict[str, Any]] = None
    artifacts: list[ProviderArtifact] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None
    retain: bool = False
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    finished_at: str = ""

    def snapshot(self) -> dict[str, Any]:
        """Return the stable run snapshot shape."""
        return {
            "run_id": self.run_id,
            "status": self.status,
            "phase": self.phase,
            "progress": self.progress,
            "message": self.message,
            "final_answer": self.final_answer,
            "error": self.error,
            "skill_ids": list(self.skills),
            "artifacts": [
                {
                    "name": artifact.name,
                    "path": str(artifact.path),
                    "mime": artifact.mime,
                    "size": artifact.size,
                    "source": artifact.source,
                }
                for artifact in self.artifacts
            ],
            "session_key": self.session_key,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": dict(self.metadata or {}),
        }


@dataclass
class ProviderRunHandle:
    """Handle returned to a provider after starting or running a skill."""

    run_id: str
    session_key: str
    _host: "ProviderAgentHost"

    async def wait(self, timeout: Optional[float] = None) -> dict[str, Any]:
        """Wait for the run and return its final snapshot."""
        return await self._host.wait_run(self.run_id, timeout)

    def events(self) -> AsyncIterator[dict[str, Any]]:
        """Iterate replayable run events."""
        return self._host.stream_run(self.run_id)


class ProviderAgentHost:
    """Start and observe agent runs on behalf of one authenticated provider caller."""

    api_version = "1.0"

    def __init__(
        self,
        api_context: Any,
        *,
        authz: Any,
        provider_type: str,
        user: ProviderUser,
        data_dir: Path,
        provider_config: Optional[dict[str, Any]] = None,
        request_cookies: Optional[dict[str, str]] = None,
    ) -> None:
        self.api_context = api_context
        self.authz = authz
        self.provider_type = str(provider_type or "").strip().lower()
        self.user = user
        self.data_dir = Path(data_dir)
        self.provider_config = dict(provider_config or {})
        self.request_cookies = dict(request_cookies or {})
        self.workspace_path = Path(
            api_context.session_manager_router.for_user(user.user_id).workspace_path
        ).resolve()

    async def run_skill(
        self,
        *,
        skill_id: str,
        prompt: str,
        files: Sequence[ProviderFileRef] = (),
        session: Optional[ProviderSessionSpec] = None,
        timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
        max_tool_calls: int = 0,
        max_model_requests: int = 0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProviderRunHandle:
        """Run one explicitly pinned skill with provider-sized execution limits."""
        return await self.start_run(
            prompt=prompt,
            skills=(skill_id,),
            files=files,
            session=session,
            timeout_seconds=timeout_seconds,
            max_tool_calls=max_tool_calls,
            max_model_requests=max_model_requests,
            metadata=metadata,
        )

    async def start_run(
        self,
        *,
        prompt: str,
        skills: Sequence[str] = (),
        tools: Sequence[str] = (),
        files: Sequence[ProviderFileRef] = (),
        session: Optional[ProviderSessionSpec] = None,
        model_token_id: Optional[str] = None,
        timeout_seconds: int = DEFAULT_RUN_TIMEOUT_SECONDS,
        max_tool_calls: int = 0,
        max_model_requests: int = 0,
        metadata: Optional[dict[str, Any]] = None,
    ) -> ProviderRunHandle:
        """Start a provider-initiated agent run with explicit skill pinning."""
        _ = model_token_id
        pinned_skills = tuple(
            str(item).strip() for item in skills if str(item or "").strip()
        )
        if pinned_skills:
            pinned_skills = self._resolve_skills(pinned_skills)
        self._ensure_capacity()

        run_id = uuid.uuid4().hex
        session_spec = session or ProviderSessionSpec()
        session_key = self._build_session_key(run_id, session_spec)
        session_manager = self.api_context.session_manager_router.for_session_key(session_key)
        await session_manager.get_or_create(session_key)

        timeout = max(30, min(int(timeout_seconds or DEFAULT_RUN_TIMEOUT_SECONDS), MAX_RUN_TIMEOUT_SECONDS))
        work_dir = ensure_user_work_dir(self.workspace_path, self.user.user_id)
        prepared = await asyncio.to_thread(self._copy_files, files, work_dir, run_id)
        message = self._compose_prompt(prompt, prepared)

        registry = getattr(self.api_context, "service_provider_registry", None)
        run_provider_config = (
            registry.get_all_instance_configs()
            if registry is not None and callable(getattr(registry, "get_all_instance_configs", None))
            else self.provider_config
        )

        request_context: dict[str, Any] = {"ui_locale": self.user.locale or "zh-CN"}
        if pinned_skills:
            request_context["_selected_capability"] = self._canonical_capability(
                pinned_skills, run_provider_config,
            )
            if tools:
                request_context["_selected_capability"]["target_tool_names"] = [
                    str(item) for item in tools if str(item or "").strip()
                ]
        user_info = UserInfo(
            user_id=self.user.user_id,
            display_name=self.user.display_name,
            tenant_id=self.user.tenant_id,
            roles=list(self.user.roles),
        )

        init_run(self.api_context, run_id, session_key, message, timeout)
        run = ProviderRunState(
            run_id=run_id,
            provider_type=self.provider_type,
            user_id=self.user.user_id,
            session_key=session_key,
            skills=pinned_skills,
            metadata=dict(metadata or {}),
            retain=bool(session_spec.retain),
            status="running",
        )
        _RUNS[run_id] = run
        queue = self.api_context.sse_manager.subscribe(run_id)
        run.task = asyncio.create_task(
            execute_agent_run(
                self.api_context,
                run_id,
                session_key,
                message,
                timeout,
                user_info,
                self.request_cookies,
                run_provider_config,
                request_context,
                {
                    "max_tool_calls": int(max_tool_calls or 0),
                    "max_model_requests": int(max_model_requests or 0),
                },
            )
        )
        asyncio.create_task(self._consume_run(run, queue, session_manager))
        return ProviderRunHandle(run_id=run_id, session_key=session_key, _host=self)

    async def get_run(self, run_id: str) -> dict[str, Any]:
        """Return a run snapshot, or a stable failure when the host restarted."""
        run = _RUNS.get(run_id)
        if run is None:
            return {
                "run_id": run_id,
                "status": "failed",
                "phase": "finalizing",
                "progress": 100,
                "message": "Provider run is no longer available",
                "final_answer": "",
                "error": {"code": "host_restarted", "message": "Provider run is no longer available"},
                "skill_ids": [],
                "artifacts": [],
                "session_key": "",
                "started_at": "",
                "finished_at": "",
                "metadata": {},
            }
        return run.snapshot()

    async def wait_run(self, run_id: str, timeout: Optional[float] = None) -> dict[str, Any]:
        """Wait for one run to reach a terminal status."""
        run = _RUNS.get(run_id)
        if run is None:
            return await self.get_run(run_id)
        try:
            if timeout is None:
                await run.done.wait()
            else:
                await asyncio.wait_for(run.done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return run.snapshot()

    def stream_run(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        """Return an async iterator over replayable events for one run."""

        async def _iterate() -> AsyncIterator[dict[str, Any]]:
            run = _RUNS.get(run_id)
            if run is None:
                yield {
                    "type": "run_failed",
                    "run_id": run_id,
                    "seq": 0,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "data": {"code": "host_restarted", "message": "Provider run is no longer available"},
                }
                return
            index = 0
            while True:
                while index < len(run.events):
                    yield run.events[index]
                    index += 1
                if run.done.is_set():
                    break
                await asyncio.sleep(0.05)

        return _iterate()

    async def cancel_run(self, run_id: str, reason: str = "") -> None:
        """Request cooperative cancellation of one run."""
        run = _RUNS.get(run_id)
        if run is None or run.status in _TERMINAL_STATUSES:
            return
        run_info = self.api_context.active_runs.get(run_id)
        if isinstance(run_info, dict):
            abort_signal = run_info.get("abort_signal")
            if isinstance(abort_signal, asyncio.Event):
                abort_signal.set()
        run.message = reason or "Cancellation requested"

    async def list_artifacts(self, run_id: str) -> list[ProviderArtifact]:
        """Return the artifacts collected for a finished run."""
        run = _RUNS.get(run_id)
        return list(run.artifacts) if run else []

    async def collect(self, paths: Sequence[Path | str]) -> list[ProviderArtifact]:
        """Copy explicit provider-known files into provider data storage."""
        work_dir = ensure_user_work_dir(self.workspace_path, self.user.user_id)
        target_dir = self.data_dir / "collected" / uuid.uuid4().hex
        target_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[ProviderArtifact] = []
        for raw in paths:
            resolved = self._resolve_work_file(work_dir, raw)
            if resolved is None:
                continue
            destination = target_dir / sanitize_filename(resolved.name, "artifact")
            await asyncio.to_thread(shutil.copy2, resolved, destination)
            mime, _ = mimetypes.guess_type(destination.name)
            artifacts.append(ProviderArtifact(
                name=destination.name,
                path=destination,
                mime=mime or "application/octet-stream",
                size=destination.stat().st_size,
                source="explicit",
            ))
        return artifacts

    def _canonical_capability(
        self,
        skills: Sequence[str],
        provider_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the catalog capability entry used to pin provider-requested skills."""
        from app.atlasclaw.api.agent_capabilities import build_agent_capabilities

        catalog = build_agent_capabilities(
            ctx=self.api_context,
            authz=self.authz,
            provider_instances=provider_config or {},
        )
        wanted = {str(skill or "").strip().lower() for skill in skills if str(skill or "").strip()}
        for item in catalog.get("capabilities", []):
            if not isinstance(item, dict):
                continue
            targets = {
                str(target or "").strip().lower()
                for target in item.get("target_skill_names") or []
                if str(target or "").strip()
            }
            names = {
                str(item.get("skill_name") or "").strip().lower(),
                str(item.get("qualified_skill_name") or "").strip().lower(),
            }
            if targets & wanted or names & wanted:
                return dict(item)
        raise ProviderHttpRuntimeError(
            f"Skill is unavailable: {skills[0]}",
            status_code=409,
            code="skill_unavailable",
        )

    def _resolve_skills(self, skills: Sequence[str]) -> tuple[str, ...]:
        """Resolve bare skill ids to their registry-qualified names and check access."""
        registry = getattr(self.api_context, "skill_registry", None)
        by_key: dict[str, str] = {}
        if registry is not None:
            for entry in registry.md_snapshot():
                name = str(entry.get("name") or "").strip()
                qualified = str(entry.get("qualified_name") or name).strip() or name
                for key in (name, qualified):
                    if key:
                        by_key.setdefault(key.lower(), qualified)
        resolved: list[str] = []
        for skill in skills:
            requested = str(skill or "").strip()
            canonical = by_key.get(requested.lower())
            if not canonical:
                raise ProviderHttpRuntimeError(
                    f"Skill is unavailable: {requested}",
                    status_code=409,
                    code="skill_unavailable",
                )
            if not (
                has_skill_access(self.authz, canonical)
                or has_skill_access(self.authz, requested)
            ):
                raise ProviderHttpRuntimeError(
                    f"Skill permission denied: {requested}",
                    status_code=403,
                    code="skill_permission_denied",
                )
            resolved.append(canonical)
        return tuple(resolved)

    def _ensure_capacity(self) -> None:
        active = sum(
            1
            for run in _RUNS.values()
            if run.provider_type == self.provider_type
            and run.user_id == self.user.user_id
            and run.status not in _TERMINAL_STATUSES
        )
        if active >= MAX_CONCURRENT_RUNS_PER_PROVIDER_USER:
            raise ProviderHttpRuntimeError(
                "Too many concurrent provider runs",
                status_code=429,
                code="provider_run_concurrency_limit",
            )

    def _build_session_key(self, run_id: str, session: ProviderSessionSpec) -> str:
        user_segment = normalize_runtime_user_id(self.user.user_id)
        if session.kind == "persistent":
            raw_key = str(session.key or "").strip()
            if not raw_key:
                raise ProviderHttpRuntimeError(
                    "Persistent provider session requires a key",
                    status_code=422,
                    code="provider_session_key_required",
                )
            scope = f"provider-{self.provider_type}-{sanitize_filename(raw_key, 'session')}"
        else:
            scope = f"provider-{self.provider_type}-{run_id[:12]}"
        return f"agent:main:user:{user_segment}:{scope}"

    def _copy_files(
        self,
        files: Sequence[ProviderFileRef],
        work_dir: Path,
        run_id: str,
    ) -> list[dict[str, Any]]:
        prepared: list[dict[str, Any]] = []
        if not files:
            return prepared
        target_dir = work_dir / "uploads" / "provider" / run_id
        target_dir.mkdir(parents=True, exist_ok=True)
        workspace_root = self.workspace_path
        for item in files:
            source = Path(item.path).expanduser()
            try:
                resolved = source.resolve()
                resolved.relative_to(workspace_root)
            except (OSError, ValueError):
                raise ProviderHttpRuntimeError(
                    f"Provider file is outside the workspace: {item.name}",
                    status_code=403,
                    code="provider_file_forbidden",
                ) from None
            if not resolved.is_file():
                raise ProviderHttpRuntimeError(
                    f"Provider file not found: {item.name}",
                    status_code=404,
                    code="provider_file_not_found",
                )
            destination = target_dir / sanitize_filename(item.name, resolved.name)
            shutil.copy2(resolved, destination)
            prepared.append({
                "name": destination.name,
                "path": str(destination.relative_to(work_dir)).replace("\\", "/"),
                "size": destination.stat().st_size,
                "metadata": {},
            })
        return prepared

    def _compose_prompt(self, prompt: str, prepared: Sequence[dict[str, Any]]) -> str:
        block = document_prompt(list(prepared))
        base = str(prompt or "").strip()
        return f"{base}\n\n{block}".strip() if block else base

    def _resolve_work_file(self, work_dir: Path, raw: Path | str) -> Optional[Path]:
        candidate = Path(raw)
        resolved = (candidate if candidate.is_absolute() else work_dir / candidate).resolve()
        try:
            resolved.relative_to(work_dir.resolve())
        except ValueError:
            return None
        return resolved if resolved.is_file() else None

    async def _consume_run(
        self,
        run: ProviderRunState,
        queue: asyncio.Queue,
        session_manager: Any,
    ) -> None:
        download_paths: list[str] = []
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                self._handle_event(run, event, download_paths)
            if run.task is not None:
                try:
                    await run.task
                except Exception as exc:  # noqa: BLE001 - run failures become run state
                    logger.warning("Provider agent run failed: %s", exc)
            info = self.api_context.active_runs.get(run.run_id, {})
            raw_status = str(info.get("status") or "").strip().lower()
            if raw_status == "completed":
                run.status = "succeeded"
            elif raw_status == "aborted":
                run.status = "cancelled"
                run.error = {"code": "run_cancelled", "message": "Provider run was cancelled"}
            elif raw_status == "timeout":
                run.status = "failed"
                run.error = {"code": "run_timeout", "message": "Provider run timed out"}
            else:
                run.status = "failed"
                run.error = run.error or {
                    "code": "run_failed",
                    "message": str(info.get("error") or "Provider run failed"),
                }
            run.phase = "finalizing"
            run.progress = max(run.progress, 95)
            run.artifacts = await self._materialize_artifacts(run.run_id, download_paths)
            run.finished_at = datetime.now(timezone.utc).isoformat()
            run.progress = 100
            self._append_event(run, "run_finished" if run.status == "succeeded" else "run_failed", {
                "status": run.status,
                "final_answer": run.final_answer,
                "artifacts": [{"name": a.name, "path": str(a.path), "mime": a.mime, "size": a.size} for a in run.artifacts],
                "error": run.error,
            })
        finally:
            self.api_context.sse_manager.remove_stream(run.run_id)
            if session_manager is not None and not run.retain:
                try:
                    await session_manager.delete_session(run.session_key)
                except Exception as exc:  # noqa: BLE001 - cleanup must not fail runs
                    logger.debug("Provider session cleanup failed: %s", exc)
            run.done.set()

    def _handle_event(self, run: ProviderRunState, event: Any, download_paths: list[str]) -> None:
        event_type = str(getattr(event.event_type, "value", event.event_type) or "").lower()
        data = event.data if isinstance(getattr(event, "data", None), dict) else {}
        if event_type == "lifecycle":
            phase = str(data.get("phase") or "").strip().lower()
            if phase == "start":
                run.status = "running"
                run.phase = "planning"
                run.progress = max(run.progress, 5)
                run.message = "Starting provider run"
            self._append_event(run, "phase", {"phase": run.phase, "message": run.message})
            return
        if event_type == "runtime":
            message = str(data.get("message") or "").strip()
            state = str(data.get("state") or "").strip().lower()
            if message:
                run.message = message
            if state == "reasoning":
                run.phase = "planning" if run.progress < 20 else "executing"
                run.progress = max(run.progress, min(20, run.progress + 2))
            self._append_event(run, "phase", {"phase": run.phase, "message": run.message})
            return
        if event_type == "assistant":
            text = str(data.get("text") or "")
            if text:
                run.final_answer += text
                run.phase = "finalizing"
                run.progress = max(run.progress, 90)
                self._append_event(run, "assistant_delta", {"text": text})
            return
        if event_type == "tool":
            tool = str(data.get("tool") or "")
            phase = str(data.get("phase") or "").strip().lower()
            if phase == "start":
                run.phase = "executing"
                run.progress = max(run.progress, min(85, 25 + 10 * len(run.events) // 3))
                run.message = f"Running {tool}" if tool else "Running tool"
                self._append_event(run, "tool_started", {"tool": tool})
            elif phase in {"end", "update"} and data.get("result") is not None:
                try:
                    references = collect_workspace_download_references_from_payloads(
                        [data.get("result")],
                        workspace_path=self.workspace_path,
                        user_id=self.user.user_id,
                        success_false_is_error=True,
                    )
                except Exception:  # noqa: BLE001 - artifact discovery is best effort
                    references = []
                for reference in references:
                    path = str(reference.get("path") or "").strip()
                    if path and path not in download_paths:
                        download_paths.append(path)
                self._append_event(run, "tool_finished", {"tool": tool})
            return
        if event_type == "error":
            run.error = {
                "code": str(data.get("code") or "run_failed"),
                "message": str(data.get("message") or "Provider run failed"),
            }
            run.phase = "finalizing"
            self._append_event(run, "run_failed", dict(run.error))

    def _append_event(self, run: ProviderRunState, event_type: str, data: dict[str, Any]) -> None:
        run.events.append({
            "type": event_type,
            "run_id": run.run_id,
            "seq": len(run.events) + 1,
            "at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        })
        if len(run.events) > 500:
            del run.events[: len(run.events) - 500]

    async def _materialize_artifacts(
        self,
        run_id: str,
        download_paths: Sequence[str],
    ) -> list[ProviderArtifact]:
        if not download_paths:
            return []
        work_dir = ensure_user_work_dir(self.workspace_path, self.user.user_id)
        target_dir = self.data_dir / "runs" / run_id
        artifacts: list[ProviderArtifact] = []
        for raw_path in download_paths:
            resolved = self._resolve_work_file(work_dir, raw_path)
            if resolved is None:
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            destination = target_dir / sanitize_filename(resolved.name, "artifact")
            try:
                await asyncio.to_thread(shutil.copy2, resolved, destination)
            except OSError as exc:
                logger.warning("Provider artifact copy failed: %s", exc)
                continue
            mime, _ = mimetypes.guess_type(destination.name)
            artifacts.append(ProviderArtifact(
                name=destination.name,
                path=destination,
                mime=mime or "application/octet-stream",
                size=destination.stat().st_size,
                source="download_path",
            ))
        return artifacts
