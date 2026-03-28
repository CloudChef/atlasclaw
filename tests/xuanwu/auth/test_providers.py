# -*- coding: utf-8 -*-
"""
AuthProvider 单元测试

涵盖 NoneProvider。

"""


from __future__ import annotations

import pytest

from app.xuanwu.auth.providers.none import NoneProvider




# ───────────────────────────────────────────────────────────
# NoneProvider
# ───────────────────────────────────────────────────────────

class TestNoneProvider:
    def test_provider_name(self):
        provider = NoneProvider()
        assert provider.provider_name() == "none"

    @pytest.mark.asyncio
    async def test_returns_default_user(self):
        provider = NoneProvider(default_user_id="admin")
        result = await provider.authenticate("")
        assert result.subject == "admin"
        assert result.display_name == "Default User"

    @pytest.mark.asyncio
    async def test_ignores_credential(self):
        provider = NoneProvider(default_user_id="dev")
        result = await provider.authenticate("any-token")
        assert result.subject == "dev"







