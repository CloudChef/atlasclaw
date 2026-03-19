---
provider_type: dingtalk
display_name: DingTalk
version: "1.0.0"

keywords:
  - approval
  - workflow
  - oa
  - dingtalk
  - expense
  - leave
  - reimbursement

capabilities:
  - Initiate approval workflow instances
  - Query approval instance status and details
  - List pending approval tasks
  - Look up users by mobile number

use_when:
  - User wants to submit an approval request (expense, leave, purchase, etc.)
  - User needs to check approval progress or status
  - User wants to see pending approval tasks
  - User mentions DingTalk OA or workflow

avoid_when:
  - User wants to send instant messages (use dingtalk channel instead)
  - User wants to manage DingTalk groups or contacts
  - User is asking about other ITSM systems like Jira or ServiceNow
---

# DingTalk OA Approval Provider

## Overview

The DingTalk Provider enables the AI Agent to interact with DingTalk's OA (Office Automation) approval system. This integration allows users to submit, query, and manage approval workflows directly through the Agent.

DingTalk (钉钉) is a widely-used enterprise collaboration platform in China that provides comprehensive OA capabilities including approval workflows, attendance, and more.

## Connection Parameters

| Parameter | Description | Required |
|-----------|-------------|----------|
| `base_url` | DingTalk API base URL | Yes |
| `app_key` | Application AppKey | Yes |
| `app_secret` | Application AppSecret | Yes |
| `agent_id` | DingTalk AgentId | Yes |

### Parameter Details

- **base_url**: The DingTalk Open API endpoint. Default: `https://oapi.dingtalk.com`
- **app_key**: The AppKey of your DingTalk enterprise application
- **app_secret**: The AppSecret of your DingTalk enterprise application
- **agent_id**: The AgentId of your DingTalk enterprise application (used for some API calls)

## Authentication

DingTalk uses an AppKey + AppSecret authentication mechanism to obtain access tokens.

### Authentication Flow

1. Use `app_key` and `app_secret` to request an `access_token`
2. The `access_token` is valid for **2 hours**
3. Include the `access_token` in subsequent API requests
4. Implement token refresh logic before expiration

### Token Endpoint

```
POST https://oapi.dingtalk.com/gettoken?appkey={appkey}&appsecret={appsecret}
```

## Configuration Example

Add the following configuration to your `atlasclaw.json`:

```json
{
  "service_providers": {
    "dingtalk": {
      "default": {
        "base_url": "https://oapi.dingtalk.com",
        "app_key": "${DINGTALK_APP_KEY}",
        "app_secret": "${DINGTALK_APP_SECRET}",
        "agent_id": "${DINGTALK_AGENT_ID}"
      }
    }
  }
}
```

Set the environment variables in your `.env` file:

```bash
DINGTALK_APP_KEY=your_app_key_here
DINGTALK_APP_SECRET=your_app_secret_here
DINGTALK_AGENT_ID=your_agent_id_here
```

## Skills Provided

This provider includes the following skills for OA approval operations:

| Skill | Description |
|-------|-------------|
| `approval-create` | Create and submit a new approval workflow instance |
| `approval-query` | Query approval instance status and details, or list instances by template |
| `approval-todo` | Get pending approval task count for a user |

## Required Permissions

Ensure your DingTalk application has the following permissions enabled:

| Permission Code | Description |
|-----------------|-------------|
| `Workflow.Process.Read` | Read workflow process definitions |
| `Workflow.Instance.Write` | Create and manage workflow instances |
| `Contact.User.Read` | Read user contact information |

### How to Configure Permissions

1. Log in to the [DingTalk Developer Console](https://open-dev.dingtalk.com/)
2. Navigate to your application
3. Go to "Permission Management" (权限管理)
4. Search and enable the required permissions
5. Submit for approval if needed

## Error Handling

### Common Error Codes

| Error Code | Description | Resolution |
|------------|-------------|------------|
| `40014` | Invalid access token | Refresh the access token |
| `40035` | Invalid parameter | Check request parameters |
| `60011` | No permission | Verify application permissions |
| `88001` | User not found | Check user ID or mobile number |

## Security Considerations

- Store `app_key` and `app_secret` securely using environment variables
- Never log or expose credentials
- Implement token refresh before expiration
- Use HTTPS for all API communications
- Audit approval operations for compliance
