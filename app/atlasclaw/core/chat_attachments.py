# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Bounded, user/session-owned chat images using existing DB and workspace services."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pydantic_ai import BinaryContent
from sqlalchemy import select

from app.atlasclaw.core.chat_documents import (
    MAX_DOC_BYTES,
    AttachmentInputError,
    detect_document_type,
    probe_document_async,
    sanitize_filename,
    validate_document,
)
from app.atlasclaw.core.security_guard import ensure_user_work_dir
from app.atlasclaw.core.user_paths import user_runtime_dir
from app.atlasclaw.db.database import get_db_manager
from app.atlasclaw.db.models import ChatAttachmentModel

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 4
MAX_HISTORY_IMAGES = 8
MAX_HISTORY_BYTES = 32 * 1024 * 1024
DEFAULT_IMAGE_REQUEST = "Please analyze the text and content in these images."
DEFAULT_DOCUMENT_REQUEST = "Please analyze the attached documents."
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
_upload_locks: dict[str, asyncio.Lock] = {}

# Backward-compatible name: callers previously only handled image failures.
ImageInputError = AttachmentInputError


def validate_image(data: bytes) -> tuple[str, int, int]:
    """
    Decode a static image and return its verified MIME type, width and height.

    Raise ImageInputError for invalid formats, animation, or byte/pixel limit breaches.
    """
    if len(data) > MAX_FILE_BYTES:
        raise ImageInputError("image_too_large", "Each image must be at most 5 MiB.", 413)
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"} or getattr(image, "is_animated", False):
                raise ImageInputError("invalid_image", "Use a static PNG, JPEG or WebP image.")
            if image.width * image.height > 20_000_000:
                raise ImageInputError("image_too_large", "Image exceeds 20 million pixels.", 413)
            image.load()
            return Image.MIME[image.format], image.width, image.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ImageInputError("invalid_image", "The image cannot be decoded.") from exc


def attachment_reference(row: ChatAttachmentModel) -> dict:
    """
    Return transcript-safe metadata without exposing a filesystem path or payload bytes.
    """
    reference = {key: getattr(row, key) for key in (
        "id", "name", "media_type", "size", "width", "height", "sha256",
    )}
    reference["kind"] = getattr(row, "kind", "image") or "image"
    reference["extension"] = getattr(row, "extension", "") or ""
    try:
        reference["metadata"] = json.loads(getattr(row, "metadata_json", "") or "{}")
    except (TypeError, ValueError):
        reference["metadata"] = {}
    return reference


def image_token_allowance(ref: dict) -> int:
    """
    Estimate a conservative context allowance from pixel dimensions, not billed usage.
    """
    return 1024 + 256 * ((ref.get("width", 512) + 511) // 512) * ((ref.get("height", 512) + 511) // 512)


class ChatAttachments:
    """
    Private image storage scoped to one authenticated user workspace.
    """

    def __init__(self, workspace: str | Path, user_id: str):
        """
        Select the user's attachment directory without creating files or querying the DB.
        """
        self.user_id = user_id
        self.workspace = Path(workspace)
        self.root = user_runtime_dir(workspace, user_id) / "attachments"

    def path(self, attachment_id: str) -> Path:
        """
        Resolve a UUID beneath this user's storage root and reject symlinks.

        This validates the path only; callers must separately verify DB ownership.
        """
        try:
            name = str(uuid.UUID(attachment_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ImageInputError("attachment_not_found", "Image not found.", 404) from exc
        path = self.root / name
        if any(p.is_symlink() for p in (path, *self.root.parents, self.root)):
            raise ImageInputError("attachment_not_found", "Image not found.", 404)
        return path

    async def upload(self, session_key: str, files: list) -> list[dict]:
        """
        Validate and save a batch of draft attachments, returning ordered references.

        Images keep their visual contract; documents are validated by content
        signature and receive a bounded metadata probe. The caller must authorize
        the session. Expired drafts are removed before checking the storage quota;
        a failed batch removes its new files.
        """
        if not files or len(files) > MAX_FILES:
            raise AttachmentInputError("too_many_files", "Attach between 1 and 4 files.")
        batches = []
        for file in files:
            filename = sanitize_filename(file.filename, "upload")
            data = await file.read(MAX_DOC_BYTES + 1)
            if len(data) > MAX_DOC_BYTES:
                raise AttachmentInputError("file_too_large", "Each file must be at most 20 MiB.", 413)
            detected = detect_document_type(filename, data)
            if detected is not None:
                extension, media = validate_document(filename, data)
                metadata = await probe_document_async(data, extension)
                batches.append((filename, data, "document", extension, media, None, None, metadata))
                continue
            if Path(filename).suffix.lower() not in IMAGE_EXTENSIONS:
                raise AttachmentInputError(
                    "unsupported_file",
                    "Supported files: PNG/JPEG/WebP images, PDF, Word (.docx), TXT, Markdown, CSV and JSON.",
                )
            media, width, height = await asyncio.to_thread(validate_image, data)
            batches.append((filename, data, "image", Path(filename).suffix.lower(), media, width, height, {}))
        lock = _upload_locks.setdefault(str(self.root), asyncio.Lock())
        async with lock, get_db_manager().get_session() as session:
            drafts = list(await session.scalars(select(ChatAttachmentModel).where(
                ChatAttachmentModel.user_id == self.user_id,
                ChatAttachmentModel.bound.is_(False),
            )))
            for row in list(drafts):
                if row.created_at < datetime.utcnow() - timedelta(hours=24):
                    await asyncio.to_thread(self.path(row.id).unlink, missing_ok=True)
                    await session.delete(row)
                    drafts.remove(row)
            if sum(r.size for r in drafts) + sum(len(b[1]) for b in batches) > 100 * 1024 * 1024:
                raise AttachmentInputError("attachment_storage_full", "Remove unsent files before uploading more.", 413)
            paths = []
            refs = []
            try:
                for filename, data, kind, extension, media, width, height, metadata in batches:
                    identifier = str(uuid.uuid4())
                    path = self.path(identifier)
                    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
                    paths.append(path)
                    await asyncio.to_thread(path.write_bytes, data)
                    row = ChatAttachmentModel(
                        id=identifier, user_id=self.user_id, session_key=session_key,
                        session_hash=hashlib.sha256(session_key.encode()).hexdigest(),
                        name=filename[:255], media_type=media, kind=kind,
                        extension=extension, size=len(data), width=width, height=height,
                        sha256=hashlib.sha256(data).hexdigest(), bound=False,
                        metadata_json=json.dumps(metadata, ensure_ascii=False),
                    )
                    session.add(row)
                    refs.append(attachment_reference(row))
                await session.commit()
            except BaseException:
                for path in paths:
                    await asyncio.to_thread(path.unlink, missing_ok=True)
                raise
            return refs

    async def resolve(self, session_key: str, ids: list[str], *, bind: bool = False, optional_ids: set[str] | None = None) -> list[dict]:
        """
        Return references in request order after checking user and session ownership.

        Setting bind marks drafts as history attachments, exempt from draft expiry.
        Missing or mismatched optional_ids are skipped; expired drafts still fail.
        """
        if not ids:
            return []
        async with get_db_manager().get_session() as session:
            rows = list(await session.scalars(select(ChatAttachmentModel).where(
                ChatAttachmentModel.id.in_(ids), ChatAttachmentModel.user_id == self.user_id,
            ).with_for_update()))
            by_id = {row.id: row for row in rows}
            refs = []
            for identifier in ids:
                row = by_id.get(identifier)
                if row is None or row.session_key != session_key:
                    if identifier in (optional_ids or set()):
                        continue
                    raise AttachmentInputError("attachment_not_found", "Attachment not found in this conversation.", 404)
                if not row.bound and row.created_at < datetime.utcnow() - timedelta(hours=24):
                    raise AttachmentInputError("attachment_not_found", "This upload has expired.", 404)
                if bind:
                    row.bound = True
                refs.append(attachment_reference(row))
            return refs

    async def content(self, identifier: str) -> tuple[bytes, str]:
        """
        Return owned attachment bytes and MIME type through a symlink-refusing descriptor.

        Reject expired drafts, unavailable files and files exceeding the kind limit.
        """
        data, media, _kind, _name = await self.content_with_meta(identifier)
        return data, media

    async def content_with_meta(self, identifier: str) -> tuple[bytes, str, str, str]:
        """
        Return owned bytes plus media type, kind and display name.

        The byte limit follows the attachment kind so documents may be larger
        than chat images without relaxing the image contract.
        """
        async with get_db_manager().get_session() as session:
            row = await session.get(ChatAttachmentModel, identifier)
            if row is None or row.user_id != self.user_id:
                raise AttachmentInputError("attachment_not_found", "Attachment not found.", 404)
            if not row.bound and row.created_at < datetime.utcnow() - timedelta(hours=24):
                raise AttachmentInputError("attachment_not_found", "This upload has expired.", 404)
            kind, media, name = getattr(row, "kind", "image") or "image", row.media_type, row.name
        limit = MAX_DOC_BYTES if kind == "document" else MAX_FILE_BYTES

        def read():
            try:
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(self.path(identifier), flags)
                with os.fdopen(fd, "rb") as file:
                    return file.read(limit + 1)
            except OSError as exc:
                raise AttachmentInputError(
                    "attachment_not_found", "The attachment is no longer available.", 404,
                ) from exc

        data = await asyncio.to_thread(read)
        if len(data) > limit:
            raise AttachmentInputError("file_too_large", "Stored file exceeds the size limit.", 413)
        return data, media, kind, name

    async def materialize(self, session_key: str, refs: list[dict]) -> list[dict]:
        """
        Copy owned documents into the user work directory and return sandbox paths.

        Paths are work_dir-relative so ``read`` and ``skill_exec`` can use them
        directly. Missing or expired optional files are skipped.
        """
        documents = [ref for ref in refs if ref.get("kind") == "document"]
        if not documents:
            return []
        resolved = await self.resolve(
            session_key, [ref["id"] for ref in documents],
            optional_ids={ref["id"] for ref in documents},
        )
        uploads_root = ensure_user_work_dir(self.workspace, self.user_id) / "uploads"
        materialized: list[dict] = []
        for ref in resolved:
            try:
                data, _media, _kind, _name = await self.content_with_meta(ref["id"])
            except AttachmentInputError:
                continue
            extension = str(ref.get("extension") or "")
            target_dir = uploads_root / ref["id"]
            target = target_dir / sanitize_filename(
                ref.get("name"), f"{ref['id']}{extension}",
            )
            await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(target.write_bytes, data)
            materialized.append({
                **ref,
                "path": str(target.relative_to(uploads_root.parent)).replace("\\", "/"),
            })
        return materialized

    async def delete(self, *, identifier: str | None = None, session_key: str | None = None) -> None:
        """
        Delete owned files and metadata by draft ID or exact session key.

        An identifier takes precedence and never deletes a bound attachment. A session
        key also removes bound history; callers must authorize that session operation.
        Materialized document copies are removed with their attachment.
        """
        async with get_db_manager().get_session() as session:
            query = select(ChatAttachmentModel).where(ChatAttachmentModel.user_id == self.user_id)
            if identifier:
                query = query.where(ChatAttachmentModel.id == identifier, ChatAttachmentModel.bound.is_(False))
            elif session_key:
                query = query.where(ChatAttachmentModel.session_key == session_key)
            else:
                return
            for row in await session.scalars(query.with_for_update()):
                await asyncio.to_thread(self.path(row.id).unlink, missing_ok=True)
                await asyncio.to_thread(
                    shutil.rmtree,
                    ensure_user_work_dir(self.workspace, self.user_id) / "uploads" / row.id,
                    True,
                )
                await session.delete(row)

    async def model_images(self, session_key: str, refs: list[dict], *, optional_ids: set[str] | None = None) -> dict[str, BinaryContent]:
        """
        Build native PydanticAI images keyed by attachment ID after ownership checks.

        Unavailable optional history images may be skipped. Embed safe reference
        metadata so transcript persistence can recover references without image bytes.
        """
        refs = await self.resolve(session_key, [ref["id"] for ref in refs], optional_ids=optional_ids)
        result = {}
        for ref in refs:
            if ref.get("kind", "image") != "image":
                continue
            try:
                data, media = await self.content(ref["id"])
            except AttachmentInputError as exc:
                if exc.code == "attachment_not_found" and ref["id"] in (optional_ids or set()):
                    continue
                raise
            result[ref["id"]] = BinaryContent(
                data=data, media_type=media, identifier=ref["id"],
                vendor_metadata={"chat_attachment": ref},
            )
        return result


def image_prompt(text: str, extra: dict, history: list | None = None):
    """
    Add current attachments not already present in native message history.

    Return text unchanged when no new images remain; image-only submissions use the
    default analysis prompt without changing the user text saved in the transcript.
    """
    seen = {
        item.identifier for message in (history or []) for part in getattr(message, "parts", [])
        for item in (part.content if isinstance(getattr(part, "content", None), list) else [])
        if isinstance(item, BinaryContent)
    }
    inputs = extra.get("_chat_image_inputs", {})
    images = [inputs[ref["id"]] for ref in extra.get("_chat_attachments", [])
              if ref["id"] in inputs and ref["id"] not in seen]
    return [text or DEFAULT_IMAGE_REQUEST, *images] if images else text


def classify_image_error(error: Exception) -> Exception:
    """
    Translate explicit 400/422 image rejection into the stable unsupported-model error.

    Preserve unrelated errors, including authentication failures and network timeouts.
    """
    text = str(error).lower()
    if getattr(error, "status_code", None) in {400, 422} and any(marker in text for marker in (
        "does not support image", "doesn't support image", "image input is not supported",
        "image inputs are not supported", "image_url is not supported",
    )):
        return ImageInputError("model_image_unsupported", "The model endpoint does not support image input.")
    return error
