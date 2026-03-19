---
name: approval-query
description: 查询钉钉审批实例状态和详情
category: provider:dingtalk
provider_type: dingtalk
instance_required: true
version: "1.0"
author: SmartCMP

# Tool Entry Declaration
tool_query_name: dingtalk_approval_query
tool_query_entrypoint: scripts/handler.py:handler
tool_query_description: 查询钉钉审批实例状态和详情，支持单实例查询和列表查询

# LLM Context Fields
triggers:
  - 查询审批
  - 审批状态
  - 审批详情
  - 审批进度
  - 查看审批

use_when:
  - 用户需要查询审批实例的状态
  - 用户需要查看审批详情
  - 用户需要获取审批进度
  - 用户需要列出某模板下的审批实例

avoid_when:
  - 用户需要发起新的审批
  - 用户需要查询待办任务数量

examples:
  - "查询审批 abc123 的状态"
  - "查看这个审批的进度"
  - "列出最近7天的报销审批"

related:
  - approval-create
  - approval-todo
---

# approval-query - 查询钉钉审批

## 描述

查询钉钉审批实例的状态和详情。支持两种查询模式：

1. **单实例查询**: 提供 process_instance_id，获取指定审批实例的完整详情
2. **列表查询**: 提供 process_code，获取该模板下的审批实例列表

## 使用场景

- 查询某个审批的当前状态（进行中、已通过、已拒绝等）
- 获取审批实例的完整详情（表单数据、操作记录、审批任务等）
- 列出某个审批模板下最近的所有审批实例

## 参数

### 输入参数

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| process_instance_id | string | 否 | 审批实例ID（查询单个实例详情时使用） |
| process_code | string | 否 | 审批模板编号（查询实例列表时使用） |
| start_time | string | 否 | 开始时间（毫秒时间戳），查询列表时使用，默认7天前 |
| end_time | string | 否 | 结束时间（毫秒时间戳），查询列表时使用，默认当前时间 |

**注意**: process_instance_id 和 process_code 至少提供一个

### 输出响应

| 字段 | 类型 | 描述 |
|------|------|------|
| success | boolean | 操作是否成功 |
| message | string | 操作结果描述 |
| data | object | 返回数据 |

#### 单实例查询返回数据

| 字段 | 类型 | 描述 |
|------|------|------|
| title | string | 审批标题 |
| status | string | 审批状态: NEW, RUNNING, TERMINATED, COMPLETED, CANCELED |
| result | string | 审批结果: agree, refuse, none |
| originator_userid | string | 发起人用户ID |
| form_component_values | array | 表单数据 |
| operation_records | array | 操作记录 |
| tasks | array | 审批任务列表 |

#### 列表查询返回数据

| 字段 | 类型 | 描述 |
|------|------|------|
| instance_ids | array | 审批实例ID列表 |
| next_cursor | number | 下一页游标（如果有更多数据） |

## 示例

### 示例 1: 查询单个审批实例

输入:
```json
{
  "process_instance_id": "abc123-def456-ghi789"
}
```

输出:
```json
{
  "success": true,
  "message": "查询成功",
  "data": {
    "title": "报销申请-张三",
    "status": "COMPLETED",
    "result": "agree",
    "originator_userid": "user123",
    "form_component_values": [
      {"name": "报销金额", "value": "1500.00"}
    ],
    "operation_records": [...],
    "tasks": [...]
  }
}
```

### 示例 2: 查询审批实例列表

输入:
```json
{
  "process_code": "PROC-FF6YR2IQO2-XXXXX",
  "start_time": "1710000000000",
  "end_time": "1710600000000"
}
```

输出:
```json
{
  "success": true,
  "message": "查询成功，共 5 条记录",
  "data": {
    "instance_ids": ["id1", "id2", "id3", "id4", "id5"],
    "next_cursor": 5
  }
}
```

## 错误处理

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| AUTH_FAILED | 认证失败 | 检查 AppKey/AppSecret 配置 |
| INSTANCE_NOT_FOUND | 审批实例不存在 | 确认实例ID是否正确 |
| INVALID_PROCESS_CODE | 审批模板编号无效 | 确认模板编号是否正确 |
| INVALID_PARAMS | 参数错误 | 至少提供 process_instance_id 或 process_code |

## 注意事项

1. 必须至少提供 process_instance_id 或 process_code 之一
2. 列表查询默认返回最近7天的数据，可通过 start_time/end_time 调整范围
3. 列表查询每次最多返回20条记录
