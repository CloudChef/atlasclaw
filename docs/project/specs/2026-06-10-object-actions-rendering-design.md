# 2026-06-10 Object Actions Rendering Design

## Goal

为 AtlasClaw agent 输出增加通用对象操作能力，使“我的审批”“我的资源”等列表结果和单对象详情可以直接提供可点击操作。

设计目标：

1. provider 声明业务对象的可行操作，不声明前端布局。
2. core/前端只识别明确命名的 `object_actions`，不从普通 URL 字段猜测语义。
3. 列表里的操作渲染在每行右侧。
4. 单对象详情里的操作渲染在内容底部。
5. 打开页面、查看详情、分析、批准、拒绝、启动、停止等都用同一套 action contract 表达。
6. 该能力是 provider-agnostic，不与 SmartCMP 强绑定。
7. 该协议是可选增强能力；未输出 `object_actions` 的 provider 仍按普通 Markdown/text/table 正常展示。

## Contract

Provider 在对象 metadata 上输出 `object_actions`。每个对象引用由对象身份字段和 action 列表组成：

```json
{
  "index": 1,
  "object_type": "approval_request",
  "object_id": "RES20260605000003",
  "object_name": "生产 VM 申请",
  "object_actions": [
    {
      "action_id": "view_detail",
      "kind": "agent_prompt",
      "display_label": {
        "default": "View details",
        "translations": {
          "zh-CN": "查看详情",
          "en-US": "View details"
        }
      },
      "agent_prompt": {
        "default": "Show approval details for RES20260605000003",
        "translations": {
          "zh-CN": "查看 RES20260605000003 的审批详情",
          "en-US": "Show approval details for RES20260605000003"
        }
      },
      "effect": "read",
      "tone": "default"
    },
    {
      "action_id": "open_detail",
      "kind": "open_url",
      "display_label": {
        "default": "Open",
        "translations": {
          "zh-CN": "打开",
          "en-US": "Open"
        }
      },
      "href": "https://cmp.example/#/main/service-request/my-approval",
      "effect": "navigate",
      "tone": "default"
    }
  ]
}
```

`object_actions` 是 provider 与 core/frontend 之间的显式交互协议，不是 provider 输出的必备字段。Provider 遵循该协议时，AtlasClaw 会渲染可点击操作；provider 不遵循或没有当前可用操作时，输出内容必须保持原样展示，不应出现错误、空操作区或布局降级。

### Object Identity

Core 和前端会保留这些通用身份字段，用于列表行匹配、详情匹配、去重和无障碍文案：

| Field | Meaning |
| --- | --- |
| `index` | 当前列表中的 1-based 行号。仅用于辅助匹配，不应作为业务主键。 |
| `object_type` | provider 自定义对象类型，例如 `approval_request`、`cloud_resource`。 |
| `object_id` | provider 面向用户或业务稳定的对象 ID。 |
| `object_name` | 对象显示名。 |

### Action Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `action_id` | Yes | provider 内稳定操作 ID，例如 `open_detail`、`approve`。 |
| `kind` | Yes | `open_url` 或 `agent_prompt`。 |
| `display_label` | No | LocalizedText。UI 按钮文案。缺省时前端按 action 类型显示通用文案。 |
| `href` | `open_url` required | 用户可直接打开的 HTTP(S) 页面地址。 |
| `agent_prompt` | `agent_prompt` required unless `agent_prompt_template` exists | LocalizedText。点击后提交给 agent 的自然语言指令。 |
| `agent_prompt_template` | Optional | LocalizedText。可带 `{{input_name}}` 占位符的 agent 指令模板。 |
| `inputs` | Optional | 点击前收集的输入项，例如拒绝原因。 |
| `requires_confirmation` | Optional | 对 mutate 操作要求二次确认。 |
| `confirmation_message` | Optional | LocalizedText。二次确认弹窗文案。 |
| `effect` | Optional | `read`、`navigate`、`mutate` 等语义提示。 |
| `tone` | Optional | `default`、`success`、`warning`、`danger` 等视觉语义。 |

### LocalizedText

Provider 面向用户或 agent 的文案必须使用 LocalizedText，而不是裸字符串字段：

```json
{
  "default": "Approve RES20260605000003",
  "translations": {
    "zh-CN": "批准 RES20260605000003",
    "en-US": "Approve RES20260605000003"
  }
}
```

`default` 必填。`translations` 可选，key 使用 BCP 47 风格 locale，例如 `zh-CN`、`en-US`。前端解析顺序为：当前完整 locale、当前基础语言、`default`。这是 object action 协议本身的 locale 解析规则；core 不从旧字段或 provider 名称推断文案。

## Action Kinds

### `open_url`

打开真实对象页面。只接受 `http://` 和 `https://` 且必须有 host。不得使用 API endpoint、下载地址、文档引用、`workspace://`、`file://`、`javascript:` 或相对路径。

### `agent_prompt`

点击后把 action 的 `agent_prompt` 或填充后的 `agent_prompt_template` 提交给当前 agent。该方式复用现有 agent routing 和 provider 工具，不引入新的直接执行 API。

例子：

```json
{
  "action_id": "reject",
  "kind": "agent_prompt",
  "display_label": {
    "default": "Reject",
    "translations": {
      "zh-CN": "拒绝",
      "en-US": "Reject"
    }
  },
  "agent_prompt_template": {
    "default": "Reject RES20260605000003, reason: {{reason}}",
    "translations": {
      "zh-CN": "拒绝 RES20260605000003，原因：{{reason}}",
      "en-US": "Reject RES20260605000003, reason: {{reason}}"
    }
  },
  "confirmation_message": {
    "default": "Confirm rejecting RES20260605000003?",
    "translations": {
      "zh-CN": "确认拒绝 RES20260605000003？",
      "en-US": "Confirm rejecting RES20260605000003?"
    }
  },
  "effect": "mutate",
  "tone": "danger",
  "requires_confirmation": true,
  "inputs": [
    {
      "name": "reason",
      "display_label": {
        "default": "Rejection reason",
        "translations": {
          "zh-CN": "拒绝原因",
          "en-US": "Rejection reason"
        }
      },
      "type": "textarea",
      "required": true
    }
  ]
}
```

## Rendering Rules

### List Results

1. 前端根据 `index`、`object_id`、`object_name` 匹配 Markdown table 行。Provider 必须把业务 ID 和显示名归一化到这些字段。
2. 匹配到的 action group 渲染到该行最右侧的操作列。
3. 原始 `object_actions` 列或详情字段不显示为正文。
4. 多个未匹配对象不会被堆到消息底部，避免“列表操作全部跑到底部”。

### Detail Results

1. 单对象详情的 action group 渲染在 assistant 消息底部。
2. 若列表 metadata 之后又出现单对象详情 metadata，详情 action group 替换之前的列表 action sidecar。
3. 详情底部可以同时显示多个动作，例如 `打开`、`分析`、`同意`、`拒绝`。

## Core Responsibilities

Core 只负责 provider-agnostic 逻辑：

1. 从 tool result、runtime metadata、JSON 字符串和 `_internal` metadata 中递归提取精确字段 `object_actions`。
2. 校验 action 结构和 `open_url.href` 安全性。
3. 去重并保留通用对象身份字段。
4. 通过 streaming runtime metadata 和 session history 暴露 `object_actions` sidecar。
5. 明确忽略普通 `url`、`href`、`link`、`source_url`、`api_url`、`doc_url` 以及 `object_href`。
6. 对没有 `object_actions` 的 provider 输出不做协议推断，继续使用普通内容渲染路径。

Core 不包含 SmartCMP 路由、审批字段模型、资源字段模型或 provider 业务动作语义。

## Provider Responsibilities

Provider 负责业务语义：

1. 为对象生成真实可打开的 Web 页面 URL。
2. 为每个对象声明当前可用 action。
3. 为 mutate action 设置清晰 `display_label`、`agent_prompt` / `agent_prompt_template`、`confirmation_message` 和输入项。
4. 输出标准 Markdown table 作为用户可读列表，结构化 metadata 放在 sidecar 中。

## Testing

Backend tests cover:

1. Nested `object_actions` extraction.
2. Runtime envelope extraction.
3. JSON string and `_internal` metadata extraction.
4. Unsafe URL rejection.
5. Ignoring ordinary URL-like fields and `object_href`.
6. Session history attaching object actions to assistant messages.

Frontend tests cover:

1. List actions render in the right-side operation column.
2. Detail actions render at the bottom.
3. Multiple unmatched list actions are not appended at the bottom.
4. Raw `object_actions` values are hidden only when valid sidecar metadata drives controls.
5. `agent_prompt` buttons submit prompts as hidden action turns.
6. Approval detail actions render `打开`、`分析`、`同意`、`拒绝` and handle confirmation/input.
7. Provider outputs without `object_actions` continue to render as ordinary Markdown/text/table content.

Provider tests cover:

1. Approval list metadata includes `查看详情` and `打开`.
2. Approval detail metadata includes `打开`、`分析`、`同意`、`拒绝`.
3. Resource list/detail metadata emits `object_actions`, not `object_href`.
4. Generated URLs are UI page URLs, not API endpoints.

## Decision

`object_actions` is the only generic object interaction contract, and it is optional. Providers that follow the contract get clickable object operations; providers that do not follow it still render their ordinary answer content. `object_href` is not a compatibility path. Opening a page is represented as one action:

```json
{
  "action_id": "open_detail",
  "kind": "open_url",
  "display_label": {
    "default": "Open",
    "translations": {
      "zh-CN": "打开",
      "en-US": "Open"
    }
  },
  "href": "https://cmp.example/#/..."
}
```

This keeps provider output explicit without requiring providers to know how AtlasClaw renders lists or details.
