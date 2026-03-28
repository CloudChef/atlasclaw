# -*- coding: utf-8 -*-
"""
SkillDeps 集成测试

涵盖：user_info 字段正确传入、user_token 兼容属性返回 raw_token。
"""

from __future__ import annotations

import pytest

from app.xuanwu.core.deps import SkillDeps
from app.xuanwu.auth.models import UserInfo, ANONYMOUS_USER


class TestSkillDeps:

    def test_user_info_default_is_anonymous(self):
        deps = SkillDeps()
        assert deps.user_info.user_id == "anonymous"

    def test_user_info_can_be_set(self):
        ui = UserInfo(user_id="u-123", display_name="Alice", raw_token="tok-abc")
        deps = SkillDeps(user_info=ui)
        assert deps.user_info.user_id == "u-123"
        assert deps.user_info.display_name == "Alice"

    def test_user_token_compat_returns_raw_token(self):
        """deps.user_token must return user_info.raw_token for backward compat."""
        ui = UserInfo(user_id="u-456", raw_token="my-raw-token")
        deps = SkillDeps(user_info=ui)
        assert deps.user_token == "my-raw-token"

    def test_user_token_empty_when_anonymous(self):
        deps = SkillDeps(user_info=ANONYMOUS_USER)
        assert deps.user_token == ""

    def test_user_info_fields_are_preserved(self):
        ui = UserInfo(
            user_id="u-789",
            tenant_id="tenant-A",
            roles=["admin", "user"],
            raw_token="t-xyz",
            provider_subject="smartcmp:alice@corp.com",
        )
        deps = SkillDeps(user_info=ui)
        assert deps.user_info.tenant_id == "tenant-A"
        assert "admin" in deps.user_info.roles
        assert deps.user_info.provider_subject == "smartcmp:alice@corp.com"
