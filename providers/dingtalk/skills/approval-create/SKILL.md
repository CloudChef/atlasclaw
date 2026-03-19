---
name: approval-create
description: 发起钉钉审批实例（报销、请假、采购等）
category: provider:dingtalk
provider_type: dingtalk
instance_required: true
version: "1.0"
author: SmartCMP

# Tool Entry Declaration
tool_create_name: dingtalk_approval_create
tool_create_entrypoint: scripts/handler.py:handler
tool_create_description: 发起钉钉审批实例，支持报销、请假、采购等审批类型

# LLM Context Fields
triggers:
  - 发起审批
  - 提交审批
  - 创建审批
  - 报销申请
  - 请假申请
  - 采购申请

use_when:
  - 用户需要发起新的审批流程
  - 用户需要提交报销单、请假单、采购单等
  - 用户需要创建审批实例

avoid_when:
  - 用户需要查询审批状态
  - 用户需要查看待办任务

examples:
  - "帮我发起一个报销审批"
  - "提交一个请假申请"
  - "创建采购审批"

related:
  - approval-query
  - approval-todo
---

# approval-create - 发起钉钉审批

## 描述

发起钉钉 OA 审批实例，支持报销、请假、采购等多种审批类型。通过指定审批模板编号和表单数据来创建新的审批流程。

## 使用场景

- 员工提交报销申请
- 员工提交请假申请
- 采购部门提交采购申请
- 任何需要走审批流程的业务场景

## 参数

### 输入参数

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| process_code | string | 是 | 审批模板编号（从钉钉管理后台获取） |
| originator_user_id | string | 是 | 发起人用户ID |
| dept_id | integer | 是 | 发起人部门ID |
| form_component_values | list | 是 | 表单组件值列表，每项包含 name 和 value |
| approvers | string | 否 | 审批人用户ID，多个用逗号分隔（不填则使用模板默认流程） |

### 输出响应

| 字段 | 类型 | 描述 |
|------|------|------|
| success | boolean | 操作是否成功 |
| message | string | 操作结果描述 |
| data | object | 返回数据，包含 process_instance_id |

## 示例

### 示例 1: 提交报销审批

输入:
```json
{
  "process_code": "PROC-FF6YR2IQO2-XXXXX",
  "originator_user_id": "user123",
  "dept_id": 12345,
  "form_component_values": [
    {"name": "报销类型", "value": "差旅费"},
    {"name": "报销金额", "value": "1500.00"},
    {"name": "报销事由", "value": "出差北京客户拜访"}
  ]
}
```

输出:
```json
{
  "success": true,
  "message": "审批实例创建成功",
  "data": {
    "process_instance_id": "abc123-def456-ghi789"
  }
}
```

### 示例 2: 提交请假申请

输入:
```json
{
  "process_code": "PROC-LEAVE-XXXXX",
  "originator_user_id": "user456",
  "dept_id": 67890,
  "form_component_values": [
    {"name": "请假类型", "value": "年假"},
    {"name": "开始时间", "value": "2026-03-20 09:00"},
    {"name": "结束时间", "value": "2026-03-21 18:00"},
    {"name": "请假事由", "value": "个人事务"}
  ],
  "approvers": "manager001,hr001"
}
```

## 错误处理

| 错误 | 原因 | 解决方案 |
|------|------|----------|
| AUTH_FAILED | 认证失败 | 检查 AppKey/AppSecret 配置 |
| INVALID_PROCESS_CODE | 审批模板编号无效 | 确认模板编号是否正确 |
| USER_NOT_FOUND | 用户不存在 | 检查用户ID是否正确 |
| DEPT_NOT_FOUND | 部门不存在 | 检查部门ID是否正确 |

## 注意事项

1. process_code 需要从钉钉管理后台获取
2. 表单字段名称必须与模板定义一致
3. 如果不指定 approvers，将使用模板的默认审批流程
