# -*- coding: utf-8 -*-
"""
PydanticAI Agent 集成测试
"""

import pytest


class TestAgentTypeResolution:
    """测试 PydanticAI Agent 类型解析"""

    def test_import_runcontext_at_runtime(self):
        from pydantic_ai import RunContext

        assert RunContext is not None

    def test_import_skilldeps_at_runtime(self):
        from app.xuanwu.core.deps import SkillDeps

        assert SkillDeps is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
