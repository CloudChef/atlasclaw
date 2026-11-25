# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Document attachment contracts: validation, probing, sandbox materialization."""
from io import BytesIO

import pytest
import pytest_asyncio
from starlette.datastructures import UploadFile

from app.atlasclaw.core.chat_attachments import ChatAttachments
from app.atlasclaw.core.chat_documents import (
    AttachmentInputError,
    document_prompt,
    probe_document,
    sanitize_filename,
    validate_document,
)
from app.atlasclaw.db.database import DatabaseConfig, init_database


@pytest_asyncio.fixture
async def db(tmp_path):
    manager = await init_database(
        DatabaseConfig(db_type="sqlite", sqlite_path=str(tmp_path / "documents.db"))
    )
    await manager.create_tables()
    yield manager
    await manager.close()


def pdf_bytes(text: str = "Report body") -> bytes:
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawString(72, 720, text)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def docx_bytes(text: str = "Report paragraph") -> bytes:
    import docx

    document = docx.Document()
    document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def upload(data: bytes, filename: str) -> UploadFile:
    return UploadFile(BytesIO(data), filename=filename)


def test_validate_document_types_and_rejections():
    assert validate_document("report.pdf", pdf_bytes())[0] == ".pdf"
    assert validate_document("report.docx", docx_bytes())[0] == ".docx"
    assert validate_document("notes.txt", b"plain text")[0] == ".txt"

    with pytest.raises(AttachmentInputError, match="unsupported_file"):
        validate_document("report.xls", b"binary")
    with pytest.raises(AttachmentInputError, match="invalid_document"):
        validate_document("report.pdf", b"not a pdf")
    with pytest.raises(AttachmentInputError, match="invalid_document"):
        validate_document("report.docx", b"PK-not-a-docx")
    with pytest.raises(AttachmentInputError, match="file_too_large"):
        validate_document("report.pdf", b"%PDF-" + b"0" * (20 * 1024 * 1024))


def test_probe_pdf_docx_and_text():
    pdf_metadata = probe_document(pdf_bytes("audit target"), ".pdf")
    assert pdf_metadata["probe_status"] == "ready"
    assert pdf_metadata["pages"] == 1
    assert "audit target" in pdf_metadata["preview"]

    docx_metadata = probe_document(docx_bytes("word body"), ".docx")
    assert docx_metadata["probe_status"] == "ready"
    assert docx_metadata["paragraphs"] == 1
    assert "word body" in docx_metadata["preview"]

    text_metadata = probe_document(b"line one", ".txt")
    assert text_metadata["text_chars"] == 8


def test_sanitize_filename_strips_paths_and_control_characters():
    assert sanitize_filename("../../etc/passwd", "fallback") == "passwd"
    assert sanitize_filename("C:\\temp\\report final.pdf") == "report final.pdf"
    assert sanitize_filename("bad\x00name.pdf") == "bad_name.pdf"
    assert sanitize_filename("", "fallback") == "fallback"


def test_document_prompt_is_bounded_and_tool_oriented():
    prompt = document_prompt([{
        "name": "report.pdf",
        "path": "uploads/abc/report.pdf",
        "size": 2048,
        "metadata": {"pages": 3, "preview": "evidence"},
    }])
    assert "uploads/abc/report.pdf" in prompt
    assert "3 pages" in prompt
    assert "read" in prompt and "skill_exec" in prompt
    assert "audit" not in prompt.lower()
    assert document_prompt([]) == ""


def test_compose_model_prompt_prefixes_manifest_for_text_and_image_prompts():
    from app.atlasclaw.core.chat_documents import compose_model_prompt

    documents = [{
        "name": "report.pdf",
        "path": "uploads/abc/report.pdf",
        "size": 2048,
        "metadata": {"pages": 3, "preview": "evidence"},
    }]
    text_prompt = compose_model_prompt("summarize this", {"_chat_documents": documents})
    assert text_prompt.startswith("## Attached files")
    assert text_prompt.endswith("summarize this")

    from pydantic_ai import BinaryContent

    image = BinaryContent(data=b"png", media_type="image/png", identifier="img-1")
    image_prompt_value = compose_model_prompt(
        "describe both",
        {
            "_chat_attachments": [{"id": "img-1"}],
            "_chat_image_inputs": {"img-1": image},
            "_chat_documents": documents,
        },
    )
    assert isinstance(image_prompt_value, list)
    assert image_prompt_value[0].startswith("## Attached files")
    assert image_prompt_value[-1] is image


@pytest.mark.asyncio
async def test_document_upload_and_materialize_into_work_dir(db, tmp_path):
    store = ChatAttachments(tmp_path, "alice")
    refs = await store.upload("session-a", [
        upload(pdf_bytes(), "../../report.pdf"),
        upload(docx_bytes(), "report.docx"),
    ])

    assert [ref["kind"] for ref in refs] == ["document", "document"]
    assert refs[0]["name"] == "report.pdf"
    assert refs[0]["metadata"]["pages"] == 1
    assert refs[0]["width"] is None

    materialized = await store.materialize("session-a", refs)
    assert len(materialized) == 2
    for item in materialized:
        assert item["path"].startswith(f"uploads/{item['id']}/")
        target = tmp_path / "users" / "alice" / "work_dir" / item["path"]
        assert target.is_file()
        assert ".." not in item["path"]

    data, media, kind, name = await store.content_with_meta(refs[0]["id"])
    assert media == "application/pdf"
    assert kind == "document"
    assert name == "report.pdf"
    assert data.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_document_materialization_skips_other_users(db, tmp_path):
    store = ChatAttachments(tmp_path, "alice")
    refs = await store.upload("session-a", [upload(pdf_bytes(), "report.pdf")])
    materialized = await ChatAttachments(tmp_path, "bob").materialize("session-a", refs)
    assert materialized == []
    assert not (tmp_path / "users" / "bob" / "work_dir" / "uploads").exists()
