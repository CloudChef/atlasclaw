---
name: approval-todo
description: 查询用户的待审批任务数量
category: provider:dingtalk
provider_type: dingtalk
instance_required: true
version: "1.0"
author: SmartCMP

# Tool Entry Declaration
tool_todo_name: dingtalk_approval_todo
tool_todo_entrypoint: scripts/handler.py:handler
tool_todo_description: 查询用户的待审批任务数量，支持通过用户ID或手机号查询

# LLM Context Fields
triggers:
  - 待办任务
  - 待审批
  - 审批任务
  - 待处理审批

use_when:
  - 用户需要查询待审批的任务数量
  - 用户需要了解有多少待处理的审批
  - 用户想知道某人有多少待办审批

avoid_when:
  - 用户需要发起新的审批
  - 用户需要查询具体审批实例详情

examples:
  - "我有多少待审批的任务"
  - "查询用户的待办数量"
  - "13800138000 有多少待审批"

related:
  - approval-create
  - approval-query
---

# approval-todo - 查询待审批任务

## 描述

查询用户的待审批任务数量。支持通过用户 ID 直接查询，或通过手机号先查找用户再查询待办数量。

## 使用场景

- 员工想了解自己有多少待审批的任务
- 管理者想了解某个员工的审批工作量
- 系统需要显示用户的待办提醒

## 参数

### 输入参数

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| userid | string | 否 | 用户ID（直接查询待办数量） |
| mobile | string | 否 | 用户手机号（先查找用户ID再查询待办数量） |

**注意**: userid 和 mobile 至少提供一个

### 输出响应

| 字段 | 类型 | 描述 |
|------|------|------|
| success | boolean | 操作是否成功 |
| message | string | 操作结果描述 |
| data | object | 返回数据 |

#### 返回数据字段

| 字段 | 类型 | 描述 |
|------|------|------|
| userid | string | 用户ID |
| count | number | 待审批任务数量 |

## 示例

### 示例 1: 通过用户ID查询

输入:
```json
{
  "userid": "user123"
}
```

输出:
```json
{
  "success": true,
  "message": "查询成功，用户 user123 有 5 条待审批任务",
  "data": {
    "userid": "user123",
    "count": 5
  }
}
```

### 示例 2: 通过手机号查询

输入:
```json
{
  "mobile": "13800138000"
}
```

输出:
```json
{
  "success": true,
  "message": "查询成功，用户 user456 有 3 条待审批任务",
  "data": {
    "userid": "user456",
    "count": 3
  }
}
```

## 错误处理

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| AUTH_FAILED | 认证失败 | 检查 AppKey/AppSecret 配置 |
| USER_NOT_FOUND | 用户不存在 | 检查用户ID或手机号是否正确 |
| INVALID_PARAMS | 参数错误 | 至少提供 userid 或 mobile |

## 注意事项

1. 必须至少提供 userid 或 mobile 之一
2. 通过手机号查询时，会先调用用户查询接口获取 userid
3. 如果同时提供 userid 和 mobile，优先使用 userid
