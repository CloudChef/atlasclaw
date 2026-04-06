# AtlasClaw Context 管理对齐 OpenClaw（务实对齐）设计说明

## 1. 背景

当前 AtlasClaw 在 Context 管理上已经具备基础能力（session、compaction、prompt builder、memory/search），但仍存在以下结构性问题：

- Context Window 决策缺少统一来源标记与守卫策略，低窗口模型风险不可控。
- Context Pruning 具备函数能力，但未形成稳定的运行时治理链路。
- Compaction 以摘要压缩为主，缺少“保护关键历史与失败工具信息”的保障机制。
- Prompt 注入以固定文件列表为主，缺少会话级过滤与预算化注入。
- Memory/Search 可核实性不足，缺少统一 citation（来源+行号）输出约束。
- Session/Transcript 治理能力与生产级诉求存在差距（缓存/锁/重试/预算治理需加强）。

本设计采用“务实对齐”策略：保留 Atlas 主体架构，不复制 OpenClaw 全部内部实现，但对齐关键行为能力和稳定性标准。

## 2. 目标与非目标

### 2.1 目标（硬验收标准）

1. 上下文不超载也不断片  
2. 长会话成本更低  
3. 关键信息不容易被压掉  
4. Prompt 注入更精准  
5. 可审计性更强  
6. 运行时治理更生产级  
7. 架构上更易演进  
8. 与 OpenClaw 在关键 Context 能力上行为对齐

### 2.2 非目标

- 不追求与 OpenClaw 1:1 文件结构或扩展接口兼容。
- 不引入新的“业务关键词规则系统”来驱动 Context 策略。
- 不在本阶段重写全部 Runner；以可分阶段落地为原则。

## 3. 设计原则

- 能力优先：优先实现可观测、可验证、可回退的运行时能力。
- 默认安全：默认开启守卫与预算治理，不依赖人工手工配置。
- 分层解耦：Context 策略从超大 Runner 中拆分到独立模块与运行时阶段。
- 证据优先：Memory/Search 输出默认可追溯，降低“模型口说”风险。
- 渐进迁移：保持兼容，避免一次性大爆炸重构。

## 4. 目标架构（务实对齐）

### 4.1 模块分层

新增/重构后形成如下层次：

1. `ContextWindowGuard`  
- 负责上下文窗口来源解析、阈值判断、告警/阻断决策。

2. `ContextPruningRuntime`  
- 在模型调用前对历史消息进行可配置、可审计的低价值内容裁剪。

3. `CompactionSafeguard`  
- 在 compaction 期间保护关键上下文（关键历史、工具失败信息、近期会话尾部）。

4. `PromptContextResolver`  
- 负责 bootstrap/context 文件的会话过滤、预算裁剪、注入顺序治理。

5. `MemoryCitationAdapter`  
- 统一 memory_search/memory_get 输出结构，支持 citation（path + lines）。

6. `SessionTranscriptGovernance`  
- 统一缓存、锁、重试、预算治理策略，并增强异常场景稳定性。

### 4.2 运行时主流程

每轮请求的 Context 路径：

1. 解析上下文窗口（来源：模型元数据/配置/回退）  
2. Guard 判断（warn/block）  
3. 构建基础消息历史  
4. 执行 Pruning（软裁剪/硬清理受控）  
5. 必要时执行 CompactionSafeguard  
6. 执行 PromptContextResolver 注入 bootstrap/context  
7. 发起模型调用  
8. 对 Memory/Search 结果进行 citation 规范化  
9. transcript 持久化与治理（缓存、锁、重试、预算）

## 5. 与 OpenClaw 对齐映射

### 5.1 Context Window Guard

对齐能力：
- `resolveContextWindowInfo` 等价能力（tokens + source）
- `evaluateContextWindowGuard` 等价能力（warn/block）

Atlas 实现要求：
- 输出 source：`model | models_config | runtime_override | default`
- 阈值采用系统默认值（无需新增用户级显式开关）
- 触发 block 时中止本轮并返回明确可观测错误事件

### 5.2 Context Pruning

对齐能力：
- 运行时阶段化 pruning，而非仅工具函数。
- 支持 soft-trim 与 hard-clear 两段策略。
- 保留最近 N 条 assistant 尾部上下文。

Atlas 实现要求：
- 通过统一 `ContextPruningRuntime` 在模型调用前执行。
- 对图像型/关键结构型消息默认保护，不盲删。
- 记录 pruning 指标与事件，便于审计。

### 5.3 Compaction Safeguard

对齐能力：
- 非简单截断，保留关键历史。
- 工具失败信息可保留到 summary 上下文中。
- 支持上下文占比保护（history share）。

Atlas 实现要求：
- 在现有 `CompactionPipeline` 之上增加 safeguard 模式。
- summary 生成前先做“关键片段提取 + 失败工具提取”。
- compaction 失败时采用 fail-safe（保守回退），不写坏历史。

### 5.4 Prompt 注入治理

对齐能力：
- bootstrap/context 按会话过滤。
- 按字符预算注入，避免固定全塞。

Atlas 实现要求：
- 用 `PromptContextResolver` 替代固定 `BOOTSTRAP_FILES` 直塞逻辑。
- 支持：会话过滤、总预算、单文件预算、顺序稳定。
- 注入失败不影响主链路，发 warning 事件。

### 5.5 Memory Citation

对齐能力：
- memory 结果包含来源路径与行号，支持 citation 模式。

Atlas 实现要求：
- `memory_search_tool` 输出结构化结果：`snippet/path/start_line/end_line/citation`
- `memory_get_tool` 支持按行段读取并返回可引用片段
- citation 默认启用自动模式（直接会话开、群组可配置降级）

### 5.6 Session/Transcript 治理

对齐能力：
- 缓存 + mtime 校验
- 锁/队列
- 读写重试
- 磁盘预算治理

Atlas 实现要求：
- 在现有 `SessionManager` 基础上补齐上述治理链路。
- 提供可观测指标：缓存命中、锁等待、重试次数、预算清理。

## 6. 关键数据模型调整

### 6.1 Context Window 信息

```text
ContextWindowInfo {
  tokens: int
  source: enum
  should_warn: bool
  should_block: bool
}
```

### 6.2 Pruning/Compaction 事件

```text
context.guard.warn
context.guard.block
context.pruning.applied
context.compaction.start
context.compaction.safeguard.applied
context.compaction.failed
```

### 6.3 Memory Citation 输出

```text
MemorySearchResult {
  snippet: string
  path: string
  start_line: int
  end_line: int
  citation: string   # path#Lx-Ly
}
```

## 7. 错误处理与回退策略

1. Guard 阻断  
- 直接停止本轮模型调用，返回可诊断错误与建议（切换模型/增大窗口）。

2. Pruning 失败  
- 回退为“不裁剪继续运行”，但记录 warning，避免服务中断。

3. Compaction 失败  
- 回退为保留原历史（不覆盖），并记录 `context.compaction.failed`。

4. Prompt 注入失败  
- 不阻断回答流程，降级为基础 prompt，并输出 warning 事件。

5. Memory citation 组装失败  
- 保留原 search 结果，citation 置空并告警，不影响主流程可用性。

## 8. 测试与验证策略

### 8.1 单元测试

- Context window 来源解析与 warn/block 边界测试
- Pruning soft/hard 策略测试（含图像消息保护）
- Compaction safeguard 关键片段保留测试
- Prompt 注入预算与会话过滤测试
- Memory citation 格式与行号边界测试
- Session 缓存/锁/重试/预算治理测试

### 8.2 集成测试

- 长会话多轮压测：验证 token 开销下降与响应稳定
- 工具失败场景：验证失败信息在 compaction 后仍可追踪
- bootstrap 大文件场景：验证预算注入与无阻断降级

### 8.3 E2E 测试

- 典型多轮会话：确认不崩、不断片、可持续响应
- memory 检索问答：确认输出可核实 citation
- 异常写盘/锁竞争：确认 transcript 仍一致可恢复

## 9. 分阶段落地计划

### Phase A（稳定性底座）
- ContextWindowGuard
- PromptContextResolver（预算化注入）

### Phase B（成本与质量）
- ContextPruningRuntime
- CompactionSafeguard

### Phase C（审计与治理）
- MemoryCitationAdapter
- SessionTranscriptGovernance

每个 Phase 必须满足：
- 单测通过
- 集成验证通过
- 回归无 blocker

## 10. 风险与缓解

1. Runner 与新模块并存导致行为漂移  
- 缓解：明确单一入口与事件埋点，对关键阶段加断言。

2. 裁剪过度导致答复质量下降  
- 缓解：保留最近 N assistant + 关键消息白名单 + 渐进阈值调优。

3. Compaction summary 误丢关键事实  
- 缓解：safeguard 提前抽取关键历史与失败工具上下文。

4. Session 治理增强引入性能回归  
- 缓解：先加观测再优化，逐步放量。

## 11. 完成定义（DoD）

以下条目全部满足即视为完成：

- 已实现上下文窗口 guard 告警/阻断能力，且可观测。
- 已实现 pruning + compaction safeguard，且长会话 token 成本可测下降。
- 已实现关键历史/失败工具信息保留策略。
- 已实现会话与预算驱动的 prompt 注入。
- 已实现 memory/search citation（来源+行号）。
- 已实现 session/transcript 缓存、锁、重试、预算治理增强。
- 已完成单测、集成、E2E 验证并无阻断问题。

