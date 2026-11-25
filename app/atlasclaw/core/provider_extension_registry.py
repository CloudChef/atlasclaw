# -*- coding: utf-8 -*-
# Copyright 2026 Qianyun, Inc., www.cloudchef.io, All rights reserved.
"""Registry for provider UI/API extension points (Provider Extension Standard v1)."""
from __future__ import annotations

import logging
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.atlasclaw.api.service_provider_schemas import ProviderSchemaDefinition, ProviderUiNav

logger = logging.getLogger(__name__)

NAV_ASSET_EXTENSIONS = frozenset({
    ".html", ".htm", ".js", ".mjs", ".css", ".json", ".map", ".svg",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".woff2", ".txt",
})
PROVIDER_ASSET_EXTENSIONS = frozenset({".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico"})
HTML_EXTENSIONS = frozenset({".html", ".htm"})


class ExtensionAssetError(Exception):
    """Path resolution failure for a provider UI asset."""

    def __init__(self, reason: str, detail: str = "Asset not found") -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ProviderNavEntry:
    """One registered navigation entry bound to its provider package."""

    provider_type: str
    provider_root: Path
    nav: ProviderUiNav
    assets_root: str
    display_name: str = ""
    catalog_icon_path: str = ""

    @property
    def id(self) -> str:
        """Return the namespaced extension id."""
        return f"{self.provider_type}.{self.nav.id}"

    @property
    def route(self) -> str:
        """Return the SPA route for this entry."""
        return f"/ext/{self.provider_type}/{self.nav.id}"

    @property
    def entry_url(self) -> str:
        """Return the iframe entry URL."""
        return f"/api/ui/extensions/{self.provider_type}/{self.nav.id}/"

    @property
    def icon_url(self) -> str:
        """Return the provider asset URL for this entry's icon, if any."""
        icon_path = self.nav.icon_path or self.catalog_icon_path
        if not icon_path:
            return ""
        relative = icon_path[len("assets/"):] if icon_path.startswith("assets/") else icon_path
        return f"/api/ui/extensions/{self.provider_type}/_assets/{relative}"

    def title_for(self, locale: str = "") -> str:
        """Return the localized navigation title."""
        return self.nav.resolve_title(locale)

    def to_api_dict(self, locale: str = "") -> dict:
        """Serialize this entry for ``GET /api/ui/extensions``."""
        return {
            "id": self.id,
            "provider_type": self.provider_type,
            "nav_id": self.nav.id,
            "title": self.title_for(locale),
            "i18n_key": self.nav.i18n_key,
            "route": self.route,
            "entry_url": self.entry_url,
            "icon_url": self.icon_url,
            "access": self.nav.access,
            "layout": self.nav.layout,
            "order": self.nav.order,
        }


def _is_symlink(path: Path, base: Path) -> bool:
    """Return whether any component of ``path`` below ``base`` is a symlink."""
    try:
        relative = path.relative_to(base)
    except ValueError:
        return True
    current = base
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return True
    return False


class ProviderExtensionRegistry:
    """Aggregate provider extension points discovered at startup."""

    def __init__(self) -> None:
        self._navs: dict[str, ProviderNavEntry] = {}

    def clear(self) -> None:
        """Drop all registered entries (used when provider discovery restarts)."""
        self._navs.clear()

    def register(
        self,
        *,
        provider_root: Path,
        definition: ProviderSchemaDefinition,
    ) -> int:
        """Register one provider schema definition; returns the number of nav entries."""
        root = Path(provider_root)
        count = 0
        for nav in definition.ui_nav:
            entry = ProviderNavEntry(
                provider_type=definition.provider_type,
                provider_root=root,
                nav=nav,
                assets_root=definition.ui_assets_root,
                display_name=definition.display_name,
                catalog_icon_path=definition.icon_path,
            )
            self._navs[entry.id] = entry
            count += 1
        return count

    def list_nav(self, *, is_admin: bool, locale: str = "") -> list[dict]:
        """Return authorized navigation entries sorted by provider order then id."""
        items = [
            entry.to_api_dict(locale)
            for entry in self._navs.values()
            if entry.nav.access != "admin" or is_admin
        ]
        items.sort(key=lambda item: (int(item.get("order", 100)), item["provider_type"], item["nav_id"]))
        return items

    def resolve_nav(self, provider_type: str, nav_id: str) -> Optional[ProviderNavEntry]:
        """Return one nav entry by provider type and nav id."""
        return self._navs.get(f"{provider_type}.{nav_id}")

    def resolve_asset(self, entry: ProviderNavEntry, relative_path: str = "") -> Path:
        """Resolve a file under the provider's UI assets root with a strict path jail."""
        base = self._assets_base(entry)
        requested = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
        if not requested or requested.endswith("/"):
            requested = entry.nav.entry
        if "\x00" in requested or Path(requested).is_absolute() or ".." in Path(requested).parts:
            raise ExtensionAssetError("forbidden", "Asset path is outside the provider UI directory")
        target = (base / requested).resolve()
        try:
            target.relative_to(base.resolve())
        except ValueError as exc:
            raise ExtensionAssetError("forbidden", "Asset path is outside the provider UI directory") from exc
        if _is_symlink(target, base):
            raise ExtensionAssetError("forbidden", "Asset path is outside the provider UI directory")
        if not target.is_file():
            raise ExtensionAssetError("not_found", "Asset not found")
        if target.suffix.lower() not in NAV_ASSET_EXTENSIONS:
            raise ExtensionAssetError("forbidden", "Asset type is not allowed")
        return target

    def resolve_provider_asset(self, provider_type: str, relative_path: str) -> Path:
        """Resolve an icon under ``<provider>/assets`` with a strict path jail."""
        entry = next(
            (item for item in self._navs.values() if item.provider_type == provider_type),
            None,
        )
        if entry is None:
            raise ExtensionAssetError("not_found", "Provider extension not found")
        base = (entry.provider_root / "assets").resolve()
        requested = str(relative_path or "").strip().replace("\\", "/").lstrip("/")
        if "\x00" in requested or not requested or Path(requested).is_absolute() or ".." in Path(requested).parts:
            raise ExtensionAssetError("forbidden", "Asset path is outside the provider assets directory")
        target = (base / requested).resolve()
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise ExtensionAssetError("forbidden", "Asset path is outside the provider assets directory") from exc
        if _is_symlink(target, base):
            raise ExtensionAssetError("forbidden", "Asset path is outside the provider assets directory")
        if not target.is_file():
            raise ExtensionAssetError("not_found", "Asset not found")
        if target.suffix.lower() not in PROVIDER_ASSET_EXTENSIONS:
            raise ExtensionAssetError("forbidden", "Asset type is not allowed")
        return target

    def has_provider(self, provider_type: str) -> bool:
        """Return whether the provider registered any UI extension."""
        return any(item.provider_type == provider_type for item in self._navs.values())

    @staticmethod
    def _assets_base(entry: ProviderNavEntry) -> Path:
        base = (entry.provider_root / entry.assets_root).resolve()
        if not base.is_dir():
            raise ExtensionAssetError("not_found", "Provider UI assets are missing")
        return base

    @staticmethod
    def mime_type_for(path: Path) -> str:
        """Return a safe content type for a served asset."""
        guessed, _ = mimetypes.guess_type(str(path))
        if guessed:
            return guessed
        return "application/octet-stream"


_extension_registry = ProviderExtensionRegistry()


def get_provider_extension_registry() -> ProviderExtensionRegistry:
    """Return the process-wide provider extension registry."""
    return _extension_registry
