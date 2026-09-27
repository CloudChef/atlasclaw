# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Bounded, user/session-owned chat images using existing DB and workspace services."""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pydantic_ai import BinaryContent
from sqlalchemy import select

from app.atlasclaw.core.user_paths import user_runtime_dir
from app.atlasclaw.db.database import get_db_manager
from app.atlasclaw.db.models import ChatAttachmentModel

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 4
MAX_HISTORY_IMAGES = 8
MAX_HISTORY_BYTES = 32 * 1024 * 1024
DEFAULT_IMAGE_REQUEST = "Please analyze the text and content in these images."
_upload_locks: dict[str, asyncio.Lock] = {}


class ImageInputError(ValueError):
    """
    Image failure carrying a stable UI error code, readable message and HTTP status.
    """

    def __init__(self, code: str, message: str, status: int = 422):
        """
        Default validation failures to 422; callers may specify size or access errors.
        """
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.status = code, message, status


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
    Return transcript-safe metadata without exposing a filesystem path or image bytes.
    """
    return {key: getattr(row, key) for key in (
        "id", "name", "media_type", "size", "width", "height", "sha256"
    )}


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
        Validate and save a batch of draft images, returning their ordered references.

        The caller must authorize the session. Expired drafts are removed before
        checking the user's storage quota; a failed batch removes its new files.
        """
        if not files or len(files) > MAX_FILES:
            raise ImageInputError("too_many_images", "Attach between 1 and 4 images.")
        batches = []
        for file in files:
            data = await file.read(MAX_FILE_BYTES + 1)
            media, width, height = await asyncio.to_thread(validate_image, data)
            batches.append((file, data, media, width, height))
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
                raise ImageInputError("image_storage_full", "Remove unsent images before uploading more.", 413)
            paths = []
            refs = []
            try:
                for file, data, media, width, height in batches:
                    identifier = str(uuid.uuid4())
                    path = self.path(identifier)
                    await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
                    paths.append(path)
                    await asyncio.to_thread(path.write_bytes, data)
                    row = ChatAttachmentModel(
                        id=identifier, user_id=self.user_id, session_key=session_key,
                        session_hash=hashlib.sha256(session_key.encode()).hexdigest(),
                        name=Path(file.filename or "image").name[:255], media_type=media,
                        size=len(data), width=width, height=height,
                        sha256=hashlib.sha256(data).hexdigest(), bound=False,
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
                    raise ImageInputError("attachment_not_found", "Image not found in this conversation.", 404)
                if not row.bound and row.created_at < datetime.utcnow() - timedelta(hours=24):
                    raise ImageInputError("attachment_not_found", "This image upload has expired.", 404)
                if bind:
                    row.bound = True
                refs.append(attachment_reference(row))
            return refs

    async def content(self, identifier: str) -> tuple[bytes, str]:
        """
        Return owned image bytes and MIME type through a symlink-refusing descriptor.

        Reject expired drafts, unavailable files and files exceeding the byte limit.
        """
        async with get_db_manager().get_session() as session:
            row = await session.get(ChatAttachmentModel, identifier)
            if row is None or row.user_id != self.user_id:
                raise ImageInputError("attachment_not_found", "Image not found.", 404)
            if not row.bound and row.created_at < datetime.utcnow() - timedelta(hours=24):
                raise ImageInputError("attachment_not_found", "This image upload has expired.", 404)
            media = row.media_type
        def read():
            try:
                fd = os.open(self.path(identifier), os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as file:
                    return file.read(MAX_FILE_BYTES + 1)
            except OSError as exc:
                raise ImageInputError("attachment_not_found", "Image is no longer available.", 404) from exc
        data = await asyncio.to_thread(read)
        if len(data) > MAX_FILE_BYTES:
            raise ImageInputError("image_too_large", "Stored image exceeds the size limit.", 413)
        return data, media

    async def delete(self, *, identifier: str | None = None, session_key: str | None = None) -> None:
        """
        Delete owned files and metadata by draft ID or exact session key.

        An identifier takes precedence and never deletes a bound attachment. A session
        key also removes bound history; callers must authorize that session operation.
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
            try:
                data, media = await self.content(ref["id"])
            except ImageInputError as exc:
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
