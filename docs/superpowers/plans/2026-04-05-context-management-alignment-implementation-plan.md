# Context Management Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 AtlasClaw 的 Context 管理能力按“务实对齐”方案升级到生产可用标准（guard/pruning/compaction safeguard/bootstrap budgeting/memory citation/session governance）。

**Architecture:** 采用“Runner 保持主干 + Context 子模块分层注入”路线。把上下文窗口守卫、历史裁剪、压缩保护、Prompt 上下文注入、Memory 引用、Session 治理拆为独立组件，并在 `runner_execution` 的统一链路接入，避免再向大文件追加耦合逻辑。

**Tech Stack:** Python 3.11+, FastAPI/PydanticAI runtime, pytest, aiofiles

---

## File Structure

- Create: `app/atlasclaw/agent/context_window_guard.py`
- Create: `app/atlasclaw/agent/context_pruning.py`
- Create: `app/atlasclaw/agent/prompt_context_resolver.py`
- Create: `app/atlasclaw/agent/compaction_safeguard.py`
- Modify: `app/atlasclaw/agent/runner_execution.py`
- Modify: `app/atlasclaw/agent/prompt_builder.py`
- Modify: `app/atlasclaw/agent/compaction.py`
- Modify: `app/atlasclaw/tools/memory/search_tool.py`
- Modify: `app/atlasclaw/tools/memory/get_tool.py`
- Modify: `app/atlasclaw/session/manager.py`
- Test: `tests/atlasclaw/test_context_window_guard.py`
- Test: `tests/atlasclaw/test_context_pruning.py`
- Test: `tests/atlasclaw/test_prompt_context_resolver.py`
- Test: `tests/atlasclaw/test_memory_tool_citations.py`
- Test: `tests/atlasclaw/session/test_session_manager_governance.py`

---

### Task 1: Context Window Guard（告警/阻断）

**Files:**
- Create: `app/atlasclaw/agent/context_window_guard.py`
- Modify: `app/atlasclaw/agent/runner_execution.py`
- Test: `tests/atlasclaw/test_context_window_guard.py`

- [ ] **Step 1: 写失败测试（来源解析 + guard 阈值）**

```python
def test_resolve_context_window_info_prefers_models_config_cap():
    info = resolve_context_window_info(
        selected_token_window=200000,
        models_config_window=128000,
        runtime_override_window=None,
        default_window=64000,
    )
    assert info.tokens == 128000
    assert info.source == "models_config"

def test_evaluate_context_window_guard_warn_and_block():
    guard = evaluate_context_window_guard(tokens=12000, warn_below=32000, hard_min=16000)
    assert guard.should_warn is True
    assert guard.should_block is True
```

- [ ] **Step 2: 运行测试确保失败**

Run: `pytest tests/atlasclaw/test_context_window_guard.py -q`  
Expected: `ImportError` 或断言失败（新模块未实现）

- [ ] **Step 3: 实现 guard 模块并接入 runner**

```python
@dataclass
class ContextWindowInfo:
    tokens: int
    source: str

@dataclass
class ContextWindowGuardResult(ContextWindowInfo):
    should_warn: bool
    should_block: bool
```

接入点：
- `runner_execution.py` 中替换 `_resolve_runtime_context_window` 的内部逻辑；
- 在 run 流程里对 `should_warn` 发 runtime warning，对 `should_block` 直接 fail。

- [ ] **Step 4: 运行测试确保通过**

Run: `pytest tests/atlasclaw/test_context_window_guard.py -q`  
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add app/atlasclaw/agent/context_window_guard.py app/atlasclaw/agent/runner_execution.py tests/atlasclaw/test_context_window_guard.py
git commit -m "feat(agent): add context window guard with warn/block enforcement"
```

---

### Task 2: Prompt Context Resolver（会话过滤 + 预算注入）

**Files:**
- Create: `app/atlasclaw/agent/prompt_context_resolver.py`
- Modify: `app/atlasclaw/agent/prompt_builder.py`
- Test: `tests/atlasclaw/test_prompt_context_resolver.py`

- [ ] **Step 1: 写失败测试（预算与会话过滤）**

```python
def test_resolver_respects_total_budget(tmp_path):
    # 准备两个大文件，验证注入后总长度 <= budget
    ...
    assert len(rendered) <= 1000

def test_resolver_filters_by_session_key(tmp_path):
    # 带 channel/group 过滤规则的 bootstrap 文件在不匹配会话时不注入
    ...
    assert "SECRET_CONTEXT" not in rendered
```

- [ ] **Step 2: 运行测试确保失败**

Run: `pytest tests/atlasclaw/test_prompt_context_resolver.py -q`  
Expected: 失败（模块不存在或逻辑未实现）

- [ ] **Step 3: 实现 resolver，并替换 PromptBuilder 固定注入**

```python
class PromptContextResolver:
    def resolve(self, *, workspace: Path, session_key: str | None, files: list[str], total_budget: int, per_file_budget: int) -> list[ResolvedPromptFile]:
        ...
```

替换 `_build_bootstrap()`：
- 不再直接遍历固定文件并全量读入；
- 使用 resolver 输出的已过滤、已裁剪内容；
- 注入失败降级为 warning 文本，不阻断主链路。

- [ ] **Step 4: 运行测试确保通过**

Run: `pytest tests/atlasclaw/test_prompt_context_resolver.py -q`  
Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add app/atlasclaw/agent/prompt_context_resolver.py app/atlasclaw/agent/prompt_builder.py tests/atlasclaw/test_prompt_context_resolver.py
git commit -m "feat(prompt): add session-aware budgeted bootstrap context resolver"
```

---

### Task 3: Context Pruning Runtime + Compaction Safeguard

**Files:**
- Create: `app/atlasclaw/agent/context_pruning.py`
- Create: `app/atlasclaw/agent/compaction_safeguard.py`
- Modify: `app/atlasclaw/agent/compaction.py`
- Modify: `app/atlasclaw/agent/runner_execution.py`
- Test: `tests/atlasclaw/test_context_pruning.py`

- [ ] **Step 1: 写失败测试（soft/hard + 关键保护）**

```python
def test_soft_trim_tool_result_keeps_head_tail():
    ...
    assert "..." in trimmed_content

def test_pruning_keeps_recent_assistant_tail():
    ...
    assert kept_assistant_count >= 3

def test_safeguard_extracts_tool_failures_into_summary():
    ...
    assert "tool failures" in summary.lower()
```

- [ ] **Step 2: 运行测试确保失败**

Run: `pytest tests/atlasclaw/test_context_pruning.py -q`  
Expected: 失败

- [ ] **Step 3: 实现 pruning runtime 与 safeguard 组件**

```python
def prune_context_messages(messages: list[dict], settings: ContextPruningSettings, *, context_window_tokens: int | None = None) -> list[dict]:
    ...

def build_safeguarded_compaction_summary(messages: list[dict], *, base_summary: str) -> str:
    # 注入关键历史 + 失败工具摘要
    ...
```

接入点：
- `runner_execution.py`：模型调用前先执行 pruning；
- `compaction.py`：summary 生成后经过 safeguard 组装。

- [ ] **Step 4: 运行测试确保通过**

Run: `pytest tests/atlasclaw/test_context_pruning.py -q`  
Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add app/atlasclaw/agent/context_pruning.py app/atlasclaw/agent/compaction_safeguard.py app/atlasclaw/agent/compaction.py app/atlasclaw/agent/runner_execution.py tests/atlasclaw/test_context_pruning.py
git commit -m "feat(agent): add runtime context pruning and compaction safeguard"
```

---

### Task 4: Memory Citation Adapter（来源 + 行号）

**Files:**
- Modify: `app/atlasclaw/tools/memory/search_tool.py`
- Modify: `app/atlasclaw/tools/memory/get_tool.py`
- Test: `tests/atlasclaw/test_memory_tool_citations.py`

- [ ] **Step 1: 写失败测试（结构化 citation 输出）**

```python
@pytest.mark.asyncio
async def test_memory_search_returns_structured_results_with_citation():
    payload = await memory_search_tool(ctx, "deploy", limit=3)
    assert isinstance(payload.get("details", {}).get("results"), list)
    assert "citation" in payload["details"]["results"][0]
```

- [ ] **Step 2: 运行测试确保失败**

Run: `pytest tests/atlasclaw/test_memory_tool_citations.py -q`  
Expected: 失败

- [ ] **Step 3: 实现 citation 结构化输出**

```python
result_item = {
    "snippet": snippet,
    "path": path,
    "start_line": start_line,
    "end_line": end_line,
    "citation": f"{path}#L{start_line}-L{end_line}",
}
```

要求：
- 不在代码中硬编码语言；
- 返回结构化 details 供前端或 LLM 二次组织。

- [ ] **Step 4: 运行测试确保通过**

Run: `pytest tests/atlasclaw/test_memory_tool_citations.py -q`  
Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add app/atlasclaw/tools/memory/search_tool.py app/atlasclaw/tools/memory/get_tool.py tests/atlasclaw/test_memory_tool_citations.py
git commit -m "feat(memory): add structured citation output for memory search/get"
```

---

### Task 5: Session/Transcript 治理增强（缓存/锁/重试/预算）

**Files:**
- Modify: `app/atlasclaw/session/manager.py`
- Test: `tests/atlasclaw/session/test_session_manager_governance.py`

- [ ] **Step 1: 写失败测试（缓存命中、读重试、预算清理）**

```python
@pytest.mark.asyncio
async def test_load_transcript_uses_cache_until_mtime_changes(...):
    ...

@pytest.mark.asyncio
async def test_read_retries_on_transient_failure(...):
    ...

@pytest.mark.asyncio
async def test_archive_budget_cleanup_removes_old_files(...):
    ...
```

- [ ] **Step 2: 运行测试确保失败**

Run: `pytest tests/atlasclaw/session/test_session_manager_governance.py -q`  
Expected: 失败

- [ ] **Step 3: 在 SessionManager 中实现治理能力**

```python
# 建议新增内部机制
self._transcript_cache: dict[str, TranscriptCacheEntry] = {}
self._io_retry_attempts = 3
self._archive_budget_bytes = 200 * 1024 * 1024
```

要求：
- 保留现有 per-session lock；
- 增加 transcript 缓存（mtime 失效）；
- 增加读写短重试；
- 增加 archive 目录预算治理（按时间淘汰最旧文件）。

- [ ] **Step 4: 运行测试确保通过**

Run: `pytest tests/atlasclaw/session/test_session_manager_governance.py -q`  
Expected: 通过

- [ ] **Step 5: Commit**

```bash
git add app/atlasclaw/session/manager.py tests/atlasclaw/session/test_session_manager_governance.py
git commit -m "feat(session): add transcript cache retry and archive budget governance"
```

---

### Task 6: 全链路回归与文档收口

**Files:**
- Modify: `docs/project/tasks/2026-04-04-context-management-alignment-plan.md`
- Modify: `docs/project/state/current.md`

- [ ] **Step 1: 运行目标测试集**

Run:
- `pytest tests/atlasclaw/test_context_window_guard.py -q`
- `pytest tests/atlasclaw/test_context_pruning.py -q`
- `pytest tests/atlasclaw/test_prompt_context_resolver.py -q`
- `pytest tests/atlasclaw/test_memory_tool_citations.py -q`
- `pytest tests/atlasclaw/session/test_session_manager_governance.py -q`
- `pytest tests/atlasclaw -q`

Expected: 全部通过

- [ ] **Step 2: 记录验证结果到 task/state**

```markdown
## Verification
- command: ...
- expected: ...
- actual: ...
```

- [ ] **Step 3: Commit**

```bash
git add docs/project/tasks/2026-04-04-context-management-alignment-plan.md docs/project/state/current.md
git commit -m "docs(project): update context alignment task/state verification status"
```

- [ ] **Step 4: 最终合并提交（如需 squash）**

```bash
git log --oneline -n 10
# 按团队策略执行 squash 或保持分步提交
```

---

## Spec Coverage Self-Check

- Context window guard：Task 1 覆盖
- Prompt 注入预算与会话过滤：Task 2 覆盖
- Pruning + compaction safeguard：Task 3 覆盖
- Memory citation：Task 4 覆盖
- Session/transcript 治理：Task 5 覆盖
- 回归验证与文档闭环：Task 6 覆盖

## Placeholder Scan Self-Check

- 无 TBD/TODO/后续补充
- 所有任务均给出明确文件路径、命令与预期

## Type/Interface Consistency Self-Check

- `ContextWindowInfo/ContextWindowGuardResult` 在 Task 1 引入并由 runner 使用
- `ContextPruningRuntime + CompactionSafeguard` 在 Task 3 引入并由 runner/compaction 接入
- `MemorySearchResult` citation 结构在 Task 4 中统一

---

## Execution Status Snapshot (2026-04-05)

- [x] Task 1 implemented and verified (`test_context_window_guard.py`)
- [x] Task 2 implemented and verified (`test_prompt_context_resolver.py`)
- [x] Task 3 implemented and verified (`test_context_pruning.py`)
- [x] Task 4 implemented and verified (`test_memory_tool_citations.py`)
- [x] Task 5 implemented and verified (`test_session_manager_governance.py`)
- [ ] Task 6 pending final packaging (full-suite regression notes + commit/push workflow)
