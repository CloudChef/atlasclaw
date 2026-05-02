# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

"""MD Skills 单元测试

覆盖 Frontmatter 解析、MdSkillEntry、SkillRegistry MD 发现、
PromptBuilder MD Skills 索引注入、AgentRunner 快照收集、SkillsConfig 配置。
"""

import asyncio
import json
import os
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.atlasclaw.skills.frontmatter import FrontmatterResult, parse_frontmatter
from app.atlasclaw.skills.registry import (
    MdSkillEntry,
    SkillMetadata,
    SkillRegistry,
    validate_skill_name,
)
from app.atlasclaw.agent.prompt_builder import PromptBuilder, PromptBuilderConfig, PromptMode
from app.atlasclaw.core.config_schema import SkillsConfig, AtlasClawConfig
from app.atlasclaw.tools.catalog import ToolCatalog, ToolProfile
from app.atlasclaw.tools.registration import register_builtin_tools


# ======================================================================
# 7.1 TestFrontmatterParser
# ======================================================================

class TestFrontmatterParser:
    """Frontmatter 解析器测试"""

    def test_standard_parse(self):
        """标准 Frontmatter 解析"""
        content = "---\nname: jira-task-manager\ndescription: 管理 Jira 任务\n---\n# Body"
        result = parse_frontmatter(content)

        assert result.metadata == {
            "name": "jira-task-manager",
            "description": "管理 Jira 任务",
        }
        assert result.body == "# Body"

    def test_no_frontmatter(self):
        """无 Frontmatter 时返回空 metadata"""
        content = "# Just markdown\nNo frontmatter"
        result = parse_frontmatter(content)

        assert result.metadata == {}
        assert result.body == content

    def test_empty_frontmatter(self):
        """空 Frontmatter 块"""
        content = "---\n---\n# Body"
        result = parse_frontmatter(content)

        assert result.metadata == {}
        assert result.body == "# Body"

    def test_value_with_colon(self):
        """值包含冒号时按第一个冒号分割"""
        content = "---\nbase_url: https://jira.example.com:8080\n---\n"
        result = parse_frontmatter(content)

        assert result.metadata["base_url"] == "https://jira.example.com:8080"
        assert result.body == ""

    def test_quoted_values(self):
        """单引号和双引号包裹的值"""
        content = '---\nname: \'my-skill\'\ndescription: "A skill"\n---\n'
        result = parse_frontmatter(content)

        assert result.metadata["name"] == "my-skill"
        assert result.metadata["description"] == "A skill"

    def test_windows_newline_and_bom(self):
        """Windows 换行 + BOM 预处理"""
        content = "\ufeff---\r\nname: test\r\n---\r\nBody"
        result = parse_frontmatter(content)

        assert result.metadata == {"name": "test"}
        assert result.body == "Body"

    def test_malformed_no_closing(self):
        """格式错误（无闭合分隔符）静默处理"""
        content = "---\nname: test\nNo closing"
        result = parse_frontmatter(content)

        assert result.metadata == {}
        # body 保留完整内容
        assert "name: test" in result.body
        assert "No closing" in result.body

    def test_comment_and_blank_lines_skipped(self):
        """注释行和空行在 Frontmatter 中被跳过"""
        content = "---\n# this is a comment\nname: test\n\ndescription: desc\n---\n"
        result = parse_frontmatter(content)

        assert result.metadata == {"name": "test", "description": "desc"}
        assert "comment" not in str(result.metadata)

    def test_yaml_list_parsing(self):
        """YAML list format parsing"""
        content = """---
name: test-skill
description: A test skill
keywords:
  - keyword1
  - keyword2
  - keyword3
use_when:
  - "User wants to do X"
  - "User mentions Y"
---
Body content
"""
        result = parse_frontmatter(content)

        assert result.metadata["name"] == "test-skill"
        assert result.metadata["description"] == "A test skill"
        assert result.metadata["keywords"] == ["keyword1", "keyword2", "keyword3"]
        assert result.metadata["use_when"] == ["User wants to do X", "User mentions Y"]
        assert result.body.strip() == "Body content"

    def test_yaml_list_with_mixed_content(self):
        """YAML list mixed with scalar fields"""
        content = """---
name: mixed
triggers:
  - create
  - update
version: "1.0.0"
avoid_when:
  - User wants to search
---
"""
        result = parse_frontmatter(content)

        assert result.metadata["name"] == "mixed"
        assert result.metadata["triggers"] == ["create", "update"]
        assert result.metadata["version"] == "1.0.0"
        assert result.metadata["avoid_when"] == ["User wants to search"]

    def test_nested_mapping_parsing(self):
        """Nested YAML mappings should be preserved as dict values."""
        content = """---
name: nested-skill
description: Uses nested metadata
tool_submit_cli_flag_overrides:
  json_body: "--json"
  payload_file: "--file"
---
Body content
"""
        result = parse_frontmatter(content)

        assert result.metadata["tool_submit_cli_flag_overrides"] == {
            "json_body": "--json",
            "payload_file": "--file",
        }
        assert "json_body" not in result.metadata

# ======================================================================
# 7.2 TestMdSkillEntry (3 cases)
# ======================================================================

class TestMdSkillEntry:
    """MdSkillEntry 数据类测试"""

    def test_default_metadata(self):
        """metadata 默认为空字典"""
        entry = MdSkillEntry(
            name="test",
            description="desc",
            file_path="/p/SKILL.md",
            location="built-in",
        )
        assert entry.metadata == {}

    def test_fields_accessible(self):
        """所有字段值与构造参数一致"""
        entry = MdSkillEntry(
            name="jira",
            description="Jira helper",
            file_path="/skills/jira/SKILL.md",
            location="workspace",
        )
        assert entry.name == "jira"
        assert entry.description == "Jira helper"
        assert entry.file_path == "/skills/jira/SKILL.md"
        assert entry.location == "workspace"

    def test_metadata_storage(self):
        """metadata 字段正确存储"""
        entry = MdSkillEntry(
            name="test",
            description="desc",
            file_path="/p",
            location="built-in",
            metadata={"os": "linux", "requires": "gh"},
        )
        assert entry.metadata == {"os": "linux", "requires": "gh"}


# ======================================================================
# 7.3 TestSkillRegistryMdLoading (17 cases)
# ======================================================================

def _write_skill_md(path: Path, frontmatter_lines: list[str], body: str = ""):
    """Helper: 写入带 Frontmatter 的 SKILL.md 文件"""
    parts = ["---"]
    parts.extend(frontmatter_lines)
    parts.append("---")
    if body:
        parts.append(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), encoding="utf-8")


class TestSkillRegistryMdLoading:
    """SkillRegistry MD Skills 加载测试"""

    def test_directory_skill_discovery(self, tmp_path):
        """目录结构 SKILL.md 发现"""
        _write_skill_md(
            tmp_path / "jira" / "SKILL.md",
            ["name: jira", "description: Jira mgr"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path), location="built-in")

        snap = reg.md_snapshot()
        assert len(snap) == 1
        assert snap[0]["name"] == "jira"
        assert snap[0]["file_path"].endswith("SKILL.md")

    def test_flat_file_discovery(self, tmp_path):
        """扁平 MD 文件发现（name 回退到 stem）"""
        _write_skill_md(
            tmp_path / "my-skill.md",
            ["description: A flat skill"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        snap = reg.md_snapshot()
        assert len(snap) == 1
        assert snap[0]["name"] == "my-skill"

    @pytest.mark.parametrize("allowed_names", [{"github"}, {"github-1.0.0"}])
    def test_directory_skill_discovery_can_filter_by_enabled_names(self, tmp_path, allowed_names):
        """Only explicitly enabled standalone skills should be loaded."""
        _write_skill_md(
            tmp_path / "github-1.0.0" / "SKILL.md",
            ["name: github", "description: GitHub helper"],
        )
        _write_skill_md(
            tmp_path / "pptx" / "SKILL.md",
            ["name: pptx", "description: PPTX helper"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(
            str(tmp_path),
            location="skills-root",
            allowed_skill_names=allowed_names,
        )

        snap = reg.md_snapshot()
        assert [entry["name"] for entry in snap] == ["github"]

    def test_underscore_prefix_excluded(self, tmp_path):
        """_ 前缀的扁平 MD 文件被排除"""
        _write_skill_md(
            tmp_path / "_internal.md",
            ["name: internal", "description: hidden"],
        )
        _write_skill_md(
            tmp_path / "valid.md",
            ["name: valid", "description: visible"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        names = reg.list_md_skills()
        assert "valid" in names
        assert "internal" not in names

    def test_py_md_isolation(self, tmp_path):
        """py skill 和 MD skill 存储隔离"""
        # MD skill
        _write_skill_md(
            tmp_path / "info" / "SKILL.md",
            ["name: info", "description: info skill"],
        )
        reg = SkillRegistry()
        # 注册一个 py skill
        reg.register(
            SkillMetadata(name="py-tool", description="A py tool"),
            lambda: None,
        )
        reg.load_from_directory(str(tmp_path))

        assert "py-tool" in [s["name"] for s in reg.snapshot()]
        assert "info" in [s["name"] for s in reg.md_snapshot()]
        # 不交叉
        assert "info" not in [s["name"] for s in reg.snapshot()]
        assert "py-tool" not in [s["name"] for s in reg.md_snapshot()]

    def test_name_fallback_to_dir(self, tmp_path):
        """目录结构无 name 字段时回退到目录名"""
        _write_skill_md(
            tmp_path / "tools" / "SKILL.md",
            ["description: desc"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        assert reg.md_snapshot()[0]["name"] == "tools"

    def test_priority_override(self, tmp_path):
        """workspace 覆盖 built-in"""
        builtin = tmp_path / "builtin"
        workspace = tmp_path / "workspace"
        _write_skill_md(
            builtin / "jira" / "SKILL.md",
            ["name: jira", "description: builtin ver"],
        )
        _write_skill_md(
            workspace / "jira" / "SKILL.md",
            ["name: jira", "description: workspace ver"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(builtin), location="built-in")
        reg.load_from_directory(str(workspace), location="workspace")

        snap = reg.md_snapshot()
        assert len(snap) == 1
        assert snap[0]["location"] == "workspace"
        assert snap[0]["description"] == "workspace ver"

    def test_invalid_file_skipped(self, tmp_path):
        """损坏文件跳过不影响其他"""
        _write_skill_md(
            tmp_path / "good" / "SKILL.md",
            ["name: good", "description: valid skill"],
        )
        # 写入二进制噪声
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        (bad_dir / "SKILL.md").write_bytes(b"\x80\x81\x82\x83" * 100)

        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        assert "good" in reg.list_md_skills()

    def test_snapshot_contains_metadata(self, tmp_path):
        """snapshot 包含 metadata 字段"""
        _write_skill_md(
            tmp_path / "jira" / "SKILL.md",
            ["name: jira", "description: d", "os: linux", "requires: gh"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        snap = reg.md_snapshot()
        assert snap[0]["metadata"] == {"os": "linux", "requires": "gh"}

    def test_nested_cli_flag_overrides_flow_into_registered_script_tool(self, tmp_path):
        """Nested frontmatter mappings should affect registered script argv."""
        skill_dir = tmp_path / "request-demo"
        _write_skill_md(
            skill_dir / "SKILL.md",
            [
                "name: request-demo",
                "description: Demo request skill",
                "tool_submit_name: demo_submit_request",
                "tool_submit_description: Submit demo request",
                "tool_submit_entrypoint: scripts/submit.py",
                "tool_submit_cli_flag_overrides:",
                '  json_body: "--json"',
                "tool_submit_parameters: |",
                "  {",
                '    "type": "object",',
                '    "properties": {',
                '      "json_body": {',
                '        "type": "string",',
                '        "description": "Complete request body"',
                "      }",
                "    },",
                '    "required": ["json_body"]',
                "  }",
            ],
        )
        script_path = skill_dir / "scripts" / "submit.py"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "print(json.dumps({'argv': sys.argv[1:]}))",
                ]
            ),
            encoding="utf-8",
        )

        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        entry = reg.get("demo_submit_request")
        assert entry is not None
        _, handler = entry
        result = asyncio.run(handler(json_body={"catalogId": "catalog-1"}))

        assert result["success"] is True
        payload = json.loads(result["output"].strip())
        assert payload["argv"][0] == "--json"

    def test_cli_positional_flow_into_registered_script_tool(self, tmp_path):
        """Positional frontmatter mappings should affect registered script argv."""
        skill_dir = tmp_path / "positional-demo"
        _write_skill_md(
            skill_dir / "SKILL.md",
            [
                "name: positional-demo",
                "description: Demo positional skill",
                "tool_detail_name: demo_detail_item",
                "tool_detail_description: Inspect one item",
                "tool_detail_entrypoint: scripts/detail.py",
                "tool_detail_cli_positional:",
                "  - item_id",
                "tool_detail_parameters: |",
                "  {",
                '    "type": "object",',
                '    "properties": {',
                '      "item_id": {"type": "string"},',
                '      "days": {"type": "integer", "default": 7}',
                "    },",
                '    "required": ["item_id"]',
                "  }",
            ],
        )
        script_path = skill_dir / "scripts" / "detail.py"
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(
            "\n".join(
                [
                    "import json, sys",
                    "print(json.dumps({'argv': sys.argv[1:]}))",
                ]
            ),
            encoding="utf-8",
        )

        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        entry = reg.get("demo_detail_item")
        assert entry is not None
        _, handler = entry
        result = asyncio.run(handler(item_id="item-1", days=3))

        assert result["success"] is True
        payload = json.loads(result["output"].strip())
        assert payload["argv"] == ["item-1", "--days", "3"]

    def test_qualified_name_uses_explicit_provider(self, tmp_path):
        """显式 provider_type 生成 provider:skill 标识"""
        _write_skill_md(
            tmp_path / "jira" / "SKILL.md",
            ["name: jira", "description: d", "provider_type: jira"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path), location="built-in")

        snap = reg.md_snapshot()
        assert snap[0]["provider"] == "jira"
        assert snap[0]["qualified_name"] == "jira:jira"

    def test_qualified_name_falls_back_to_source_provider(self, tmp_path):
        """未声明 provider_type 时回退到 load_from_directory(provider=...)"""
        _write_skill_md(
            tmp_path / "preapproval-agent" / "SKILL.md",
            ["name: preapproval-agent", "description: d"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path), location="external", provider="smartcmp")

        entry = reg.get_md_skill("smartcmp:preapproval-agent")
        assert entry is not None
        assert entry.provider == "smartcmp"
        assert entry.qualified_name == "smartcmp:preapproval-agent"

    def test_list_md_skills(self, tmp_path):
        """list_md_skills 返回名称列表"""
        _write_skill_md(
            tmp_path / "jira" / "SKILL.md",
            ["name: jira", "description: d1"],
        )
        _write_skill_md(
            tmp_path / "gerrit" / "SKILL.md",
            ["name: gerrit", "description: d2"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        names = reg.list_md_skills()
        assert set(names) == {"jira", "gerrit"}

    def test_valid_name_passes(self, tmp_path):
        """合法名称验证通过"""
        _write_skill_md(
            tmp_path / "valid-skill-123" / "SKILL.md",
            ["name: valid-skill-123", "description: ok"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        assert "valid-skill-123" in reg.list_md_skills()

    def test_invalid_names_skipped(self, tmp_path):
        """非法名称全部跳过"""
        for i, bad_name in enumerate(["My_Skill", "--invalid", "a" * 65]):
            d = tmp_path / f"dir{i}"
            _write_skill_md(
                d / "SKILL.md",
                [f"name: {bad_name}", "description: should be skipped"],
            )
        reg = SkillRegistry()
        for i in range(3):
            reg.load_from_directory(str(tmp_path / f"dir{i}"))

        assert len(reg.list_md_skills()) == 0

    def test_dir_name_mismatch_skipped(self, tmp_path):
        """目录结构名与目录名不一致时仍可加载（名称验证已放宽）"""
        _write_skill_md(
            tmp_path / "jira" / "SKILL.md",
            ["name: other-name", "description: mismatch"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        # 名称验证已放宽，允许目录名与skill名不一致
        assert len(reg.list_md_skills()) == 1
        assert "other-name" in reg.list_md_skills()

    def test_missing_description_skipped(self, tmp_path):
        """缺少 description 时跳过"""
        _write_skill_md(
            tmp_path / "test" / "SKILL.md",
            ["name: test"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        assert len(reg.list_md_skills()) == 0

    def test_description_too_long_skipped(self, tmp_path):
        """description 超过 1024 字符时跳过"""
        _write_skill_md(
            tmp_path / "long" / "SKILL.md",
            ["name: long", f"description: {'A' * 1025}"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        assert len(reg.list_md_skills()) == 0

    def test_file_size_exceeded_skipped(self, tmp_path):
        """文件大小超限时跳过"""
        skill_dir = tmp_path / "big"
        skill_dir.mkdir()
        big_file = skill_dir / "SKILL.md"
        # 写入超过默认限制的内容
        big_file.write_text(
            "---\nname: big\ndescription: huge\n---\n" + "x" * 300000,
            encoding="utf-8",
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        assert "big" not in reg.list_md_skills()

    def test_metadata_excludes_name_description(self, tmp_path):
        """metadata 不包含 name 和 description"""
        _write_skill_md(
            tmp_path / "jira" / "SKILL.md",
            ["name: jira", "description: d", "os: linux", "requires: jira-cli"],
        )
        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path))

        entry_meta = reg.md_snapshot()[0]["metadata"]
        assert entry_meta == {"os": "linux", "requires": "jira-cli"}
        assert "name" not in entry_meta
        assert "description" not in entry_meta

    def test_script_backed_md_tool_disabled_by_default(self, tmp_path):
        """Script-backed markdown tools should register by default."""
        skill_dir = tmp_path / "script-skill"
        _write_skill_md(
            skill_dir / "SKILL.md",
            [
                "name: script-skill",
                "description: script based",
                "tool_name: script_tool",
                "entrypoint: run.py:handler",
            ],
        )
        (skill_dir / "run.py").write_text("print('hello')\n", encoding="utf-8")

        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path), location="workspace")

        assert reg.get("script_tool") is not None

    def test_explicit_python_handler_md_tool_still_registers(self, tmp_path):
        """Explicit callable handlers remain available without script execution."""
        skill_dir = tmp_path / "callable-skill"
        _write_skill_md(
            skill_dir / "SKILL.md",
            [
                "name: callable-skill",
                "description: callable based",
                "tool_name: callable_tool",
                "entrypoint: run.py:custom_handler",
            ],
        )
        (skill_dir / "run.py").write_text(
            "async def custom_handler(**kwargs):\n"
            "    return {'ok': True, 'kwargs': kwargs}\n",
            encoding="utf-8",
        )

        reg = SkillRegistry()
        reg.load_from_directory(str(tmp_path), location="workspace")

        assert reg.get("callable_tool") is not None


# ======================================================================
# 7.4 TestPromptBuilderMdSkills (11 cases)
# ======================================================================

def _make_md_skill(
    name: str = "jira",
    description: str = "Manage Jira",
    file_path: str = "/skills/jira/SKILL.md",
    location: str = "workspace",
    metadata: dict | None = None,
) -> dict:
    return {
        "name": name,
        "description": description,
        "file_path": file_path,
        "location": location,
        "metadata": metadata or {},
    }


class TestPromptBuilderMdSkills:
    """PromptBuilder MD Skills 索引注入测试"""

    def _builder(self, **overrides) -> PromptBuilder:
        defaults = {
            "mode": PromptMode.FULL,
            "workspace_path": tempfile.mkdtemp(),
        }
        defaults.update(overrides)
        return PromptBuilder(PromptBuilderConfig(**defaults))

    def test_basic_compact_format(self):
        """基础 compact 索引格式验证"""
        b = self._builder()
        output = b._build_md_skills_index([_make_md_skill()])

        assert "## Skills" in output
        assert "Format: `name | description | file_path`" in output
        assert "- `jira` | Manage Jira | `/skills/jira/SKILL.md`" in output

    def test_empty_list_returns_empty(self):
        """空列表返回空字符串"""
        b = self._builder()
        assert b._build_md_skills_index([]) == ""

    def test_target_md_skill_section_rendered(self):
        """定向 markdown skill 会写入系统提示"""
        b = self._builder()
        output = b.build(
            md_skills=[_make_md_skill()],
            target_md_skill={
                "provider": "smartcmp",
                "qualified_name": "smartcmp:preapproval-agent",
                "file_path": "/skills/preapproval-agent/SKILL.md",
            },
        )

        assert "Target Markdown Skill" in output
        assert "smartcmp:preapproval-agent" in output
        assert "/skills/preapproval-agent/SKILL.md" in output

    def test_target_md_skill_loaded_instructions_rendered_in_minimal_mode(self):
        """Render loaded SKILL.md instructions for the selected markdown skill in minimal mode."""
        b = self._builder(mode=PromptMode.MINIMAL)
        output = b.build(
            md_skills=[_make_md_skill()],
            target_md_skill={
                "provider": "pptx",
                "qualified_name": "pptx",
                "file_path": "/skills/pptx/SKILL.md",
                "instructions": "Use this skill to create slides.",
                "content": "# PPTX Skill\n\nFull body should not be inlined.",
            },
        )

        assert "Target Markdown Skill" in output
        assert "Loaded SKILL.md for the selected skill:" in output
        assert "Use this skill to create slides." in output
        assert "# PPTX Skill" not in output
        assert "Full body should not be inlined." not in output
        assert "## Skills" not in output

    def test_response_language_policy_precedes_target_md_skill_in_minimal_mode(self):
        b = self._builder(mode=PromptMode.MINIMAL)
        output = b.build(
            target_md_skill={
                "provider": "smartcmp",
                "qualified_name": "smartcmp:request",
                "file_path": "/skills/request/SKILL.md",
                "content": "# Request Skill",
            },
        )

        assert "## Response Language" in output
        assert "dominant language of the current user message" in output
        assert output.index("## Response Language") < output.index("## Target Markdown Skill")

    def test_target_md_skill_loaded_instructions_sanitize_backend_narration(self):
        b = self._builder(mode=PromptMode.MINIMAL)
        output = b.build(
            target_md_skill={
                "provider": "smartcmp",
                "qualified_name": "smartcmp:request",
                "file_path": "/skills/request/SKILL.md",
                "instructions": (
                    "This is a hidden backend step for cloud-resource requests:\n"
                    "- Do NOT tell the user you are checking component info, node types, or backend metadata.\n"
                ),
            },
        )

        assert "backend step" not in output.lower()
        assert "intermediate metadata" in output
        assert "Do not announce intermediate tool calls" in output
        assert "actual parameter metadata overrides all static examples" not in output
        assert "Do not show multiple selection lists in the same assistant turn" not in output

    def test_target_md_skill_loaded_instructions_preserve_json_preview_instruction(self):
        """Preserve JSON preview guidance from loaded selected-skill instructions."""
        b = self._builder(mode=PromptMode.MINIMAL)
        output = b.build(
            target_md_skill={
                "provider": "smartcmp",
                "qualified_name": "smartcmp:request",
                "file_path": "/skills/request/SKILL.md",
                "instructions": (
                    "Before submit, show JSON 预览.\n"
                    "Render the constructed request body in a fenced json block.\n"
                    "Mask credentialPassword as \"******\" in the preview.\n"
                ),
            },
        )

        assert "JSON 预览" in output
        assert "fenced json block" in output.lower()
        assert "credentialPassword" in output
        assert "******" in output

    def test_target_md_skill_body_does_not_inject_workflow_context(self):
        b = self._builder(mode=PromptMode.MINIMAL)
        output = b.build(
            target_md_skill={
                "provider": "smartcmp",
                "qualified_name": "smartcmp:request",
                "file_path": "/skills/request/SKILL.md",
                "content": "Use request workflow metadata.",
                "workflow_context": {
                    "catalog_name": "Linux VM",
                    "selected_catalog_node": "Compute",
                    "selected_catalog_type": "cloudchef.nodes.Compute",
                    "selected_catalog_os_type": "Linux",
                },
            },
        )

        assert "workflow_context" not in output
        assert "Use request node exactly as:" not in output
        assert "Use request type exactly as:" not in output
        assert "cloudchef.nodes.Compute` -> `Compute" not in output

    def test_description_truncation(self):
        """描述超过 desc_max_chars 时截断"""
        b = self._builder(md_skills_desc_max_chars=200)
        skill = _make_md_skill(description="A" * 300)
        output = b._build_md_skills_index([skill])

        # 截断为 197 + "..."
        assert "A" * 197 + "..." in output
        assert "A" * 198 not in output

    def test_count_limit(self):
        """数量超过 max_count 时截取"""
        b = self._builder(md_skills_max_count=20)
        skills = [_make_md_skill(name=f"s{i:03d}", description=f"d{i}") for i in range(25)]
        output = b._build_md_skills_index(skills)

        assert output.count("- `s") <= 20

    def test_budget_with_truncation_comment(self):
        """总预算超出时附加截断注释"""
        b = self._builder(md_skills_max_index_chars=1100)
        skills = [
            _make_md_skill(name=f"s{i:03d}", description="D" * 100)
            for i in range(10)
        ]
        output = b._build_md_skills_index(skills)

        assert len(output) <= 1100
        assert "Showing" in output
        assert "10" in output  # 总数

    def test_full_mode_includes(self):
        """FULL 模式包含 MD Skills"""
        b = self._builder(mode=PromptMode.FULL)
        output = b.build(md_skills=[_make_md_skill()])

        assert "## Skills" in output

    def test_minimal_mode_excludes(self):
        """MINIMAL 模式不包含 MD Skills"""
        b = self._builder(mode=PromptMode.MINIMAL)
        output = b.build(md_skills=[_make_md_skill()])

        assert "## Skills" not in output

    def test_none_mode_excludes(self):
        """NONE 模式不包含 MD Skills"""
        b = self._builder(mode=PromptMode.NONE)
        output = b.build(md_skills=[_make_md_skill()])

        assert "## Skills" not in output

    def test_three_instructions_present(self):
        """核心执行指令和选择指导存在"""
        b = self._builder()
        output = b._build_md_skills_index([_make_md_skill()])

        assert "Detailed instructions are loaded after an authorized skill is selected" in output
        assert "Format: `name | description | file_path`" in output

    def test_path_compression(self):
        """路径压缩：用户主目录替换为 ~"""
        home = str(Path.home())
        skill = _make_md_skill(file_path=f"{home}/.atlasclaw/skills/jira/SKILL.md")
        b = self._builder()
        output = b._build_md_skills_index([skill])

        assert "~/.atlasclaw/skills/jira/SKILL.md" in output
        assert home not in output

    def test_md_skills_before_executable_skills(self):
        """MD Skills 索引段位于可执行 Skills 之前"""
        b = self._builder()
        exe_skills = [{"name": "py-tool", "description": "exec", "location": "built-in", "category": "utility"}]
        output = b.build(skills=exe_skills, md_skills=[_make_md_skill()])

        md_pos = output.find("## Skills")
        built_in_pos = output.find("## Built-in Tools (Use ONLY if no MD Skill matches)")
        assert md_pos != -1, "Skills section should be present"
        assert built_in_pos != -1, "Built-in tools section should be present"
        assert md_pos < built_in_pos, "MD skills index should appear before built-in tools"


# ======================================================================
# 7.5 TestAgentRunnerMdSkills (4 cases)
# ======================================================================

class TestAgentRunnerMdSkills:
    """AgentRunner MD Skills 快照收集测试"""

    def test_primary_key_collection(self):
        """主键 md_skills_snapshot 收集"""
        from app.atlasclaw.agent.runner_prompt_context import collect_md_skills_snapshot
        from app.atlasclaw.core.deps import SkillDeps

        deps = SkillDeps(
            user_token="t",
            peer_id="p",
            session_key="s",
            channel="c",
            extra={
                "md_skills_snapshot": [
                    {"name": "jira", "description": "d", "file_path": "/p", "location": "built-in", "metadata": {}}
                ]
            },
        )
        result = collect_md_skills_snapshot(deps)
        assert len(result) == 1
        assert result[0]["name"] == "jira"

    def test_fallback_key_collection(self):
        """备选键 md_skills 收集"""
        from app.atlasclaw.agent.runner_prompt_context import collect_md_skills_snapshot
        from app.atlasclaw.core.deps import SkillDeps

        deps = SkillDeps(
            user_token="t",
            peer_id="p",
            session_key="s",
            channel="c",
            extra={"md_skills": [{"name": "jira"}]},
        )
        result = collect_md_skills_snapshot(deps)
        assert len(result) == 1

    def test_missing_key_returns_empty(self):
        """无 md_skills 相关键时返回空列表"""
        from app.atlasclaw.agent.runner_prompt_context import collect_md_skills_snapshot
        from app.atlasclaw.core.deps import SkillDeps

        deps = SkillDeps(
            user_token="t",
            peer_id="p",
            session_key="s",
            channel="c",
            extra={},
        )
        result = collect_md_skills_snapshot(deps)
        assert result == []

    def test_non_dict_extra_returns_empty(self):
        """extra 为 None 或空 dict 时返回空列表"""
        from app.atlasclaw.agent.runner_prompt_context import collect_md_skills_snapshot
        from app.atlasclaw.core.deps import SkillDeps

        # extra=None 应该正常工作
        deps = SkillDeps(
            user_token="t",
            peer_id="p",
            session_key="s",
            channel="c",
            extra=None,
        )
        result = collect_md_skills_snapshot(deps)
        assert result == []


# ======================================================================
# 7.6 TestConfigSchema (3 cases)
# ======================================================================

class TestConfigSchema:
    """SkillsConfig 配置测试"""

    def test_defaults(self):
        """默认值验证"""
        cfg = SkillsConfig()
        assert cfg.md_skills_max_count == 20
        assert cfg.md_skills_desc_max_chars == 200
        assert cfg.md_skills_index_max_chars == 3000
        assert cfg.capability_index_max_count == 20
        assert cfg.capability_index_desc_max_chars == 200
        assert cfg.capability_index_max_chars == 3000
        assert cfg.md_skills_max_file_bytes == 262144
        assert cfg.tools_exclusive == []

    def test_custom_values(self):
        """自定义值验证"""
        cfg = SkillsConfig(
            md_skills_max_count=50,
            capability_index_max_count=60,
            md_skills_max_file_bytes=524288,
            tools_exclusive=["read"],
        )
        assert cfg.md_skills_max_count == 50
        assert cfg.capability_index_max_count == 60
        assert cfg.md_skills_max_file_bytes == 524288
        assert cfg.tools_exclusive == ["read"]

    def test_atlasclaw_config_integration(self):
        """AtlasClawConfig 集成验证"""
        ucfg = AtlasClawConfig()
        assert hasattr(ucfg, "skills")
        assert isinstance(ucfg.skills, SkillsConfig)

        pbcfg = PromptBuilderConfig()
        assert pbcfg.md_skills_max_index_chars == 3000
        assert pbcfg.capability_index_max_chars == 3000


class TestBuiltinToolCatalog:
    """Built-in tool registration and group coverage tests."""

    def test_full_profile_includes_read_but_not_high_risk_tools(self):
        tools = ToolCatalog.get_tools_by_profile(ToolProfile.FULL)

        assert "read" in tools
        for tool_name in ("write", "edit", "delete", "exec", "process"):
            assert tool_name not in tools

    def test_register_builtin_tools_includes_read_but_not_high_risk_tools(self):
        reg = SkillRegistry()
        registered = register_builtin_tools(reg, profile=ToolProfile.FULL)

        assert "read" in registered
        assert reg.get("read") is not None
        for tool_name in ("write", "edit", "delete", "exec", "process"):
            assert tool_name not in registered
            assert reg.get(tool_name) is None
