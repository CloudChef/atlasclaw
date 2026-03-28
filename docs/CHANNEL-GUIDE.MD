# Xuanwu Channel Integration Guide

## Overview

Xuanwu Channel system provides a unified interface to integrate with various enterprise messaging platforms. Channels enable your AI agent to communicate with users through their preferred IM platforms.

### Supported Platforms

| Platform | Channel Type | Recommended Mode | Status |
|----------|--------------|------------------|--------|
| Feishu (Lark) | `feishu` | Long Connection (SDK) | Stable |
| DingTalk | `dingtalk` | Stream Mode | Stable |
| WeCom | `wecom` | WebSocket | Stable |

### Connection Modes

Xuanwu supports multiple connection modes for IM platform integration:

| Mode | Direction | Pros | Cons |
|------|-----------|------|------|
| **Long Connection** | Xuanwu -> Platform | Real-time, no public URL needed, auto-reconnect | Requires SDK dependency |
| **Webhook** | Platform -> Xuanwu | Simple setup, no SDK needed | Requires public URL, callback configuration |
| **Application API** | Bidirectional | Full API access, supports more features | Requires callback URL configuration |

**Recommendation:** Use **Long Connection** mode (WebSocket/Stream) whenever possible. It is more reliable, does not require exposing your server to the internet, and handles reconnection automatically.

---

## Feature Comparison

| Feature | Feishu | DingTalk | WeCom |
|---------|--------|----------|-------|
| Long Connection / Stream | Yes (SDK) | Yes (Stream) | Yes (WebSocket) |
| Webhook | Planned | Yes | Yes |
| Application API | No | No | Yes |
| Streaming Reply | No | No | Yes |
| Markdown Support | Limited | No | Yes |
| File Messages (Inbound) | No | No | Yes (metadata) |
| Image Messages (Inbound) | No | No | Yes (metadata) |
| Voice Messages (Inbound) | No | No | Yes (metadata) |
| Multiprocessing Isolation | Yes | Yes | No |

**Notes:**
- Feishu and DingTalk handlers use multiprocessing to isolate the SDK from FastAPI's event loop
- WeCom runs in the same process using async WebSocket
- Markdown support in Feishu is limited to text-only replies
- WeCom sends replies in Markdown format when using WebSocket mode

---

## Quick Start

The general workflow for setting up any channel:

```
+-----------------------------------------------------------------------+
|                    Channel Setup Workflow                              |
+-----------------------------------------------------------------------+
|                                                                        |
|  Step 1: Create App          Create application on platform console    |
|              |                                                         |
|              v                                                         |
|  Step 2: Configure Perms     Enable required permissions               |
|              |                                                         |
|              v                                                         |
|  Step 3: Get Credentials     Obtain App ID, Secret, etc.               |
|              |                                                         |
|              v                                                         |
|  Step 4: Create Connection   POST /api/channels/{type}/connections     |
|              |                                                         |
|              v                                                         |
|  Step 5: Verify Connection   POST .../connections/{id}/verify          |
|              |                                                         |
|              v                                                         |
|  Step 6: Enable Connection   POST .../connections/{id}/enable          |
|              |                                                         |
|              v                                                         |
|  Step 7: Test                Send a test message to your bot           |
|                                                                        |
+-----------------------------------------------------------------------+
```

### Session Scope Rules

Channel traffic must build canonical `SessionKey` values instead of ad-hoc strings.

- `user_id` is the Xuanwu user who owns the channel connection
- `account_id` is the concrete channel connection ID
- Direct messages use the external sender as `peer_id`
- Group chats use the group or conversation ID as `peer_id`
- Group chats are therefore shared by group, while direct messages remain isolated per external user

### Step 5: Verify Connection

Before enabling a connection, you can verify that your credentials are correct:

```bash
curl -X POST "http://localhost:8000/api/channels/{type}/connections/{id}/verify"
```

**Response (Success):**
```json
{
  "valid": true,
  "errors": []
}
```

**Response (Failure):**
```json
{
  "valid": false,
  "errors": ["app_id is required for Long Connection mode"]
}
```

This tests your configuration without fully connecting, helping you catch credential issues early.

---

## Web UI - Channel Management

Xuanwu provides a web-based interface for managing channel connections without using the API directly.

### Accessing the Web UI

Navigate to:
```
http://your-host:8000/channels.html
```

### Features

#### Channel Type Selection

The main page displays all available channel types (Feishu, DingTalk, WeCom) as cards. Each card shows:
- Channel name
- Number of existing connections (or "Not configured" if none)

Click a channel type to view and manage its connections.

#### Connection List View

After selecting a channel type, you see:
- **Connection name** - The display name you assigned
- **Status indicator** - Green (connected) or gray (disconnected)
- **Config preview** - First configuration field as a preview
- **Created date** - When the connection was created

#### Creating a New Connection

1. Click the **"+ New Connection"** button
2. Fill in the dynamically generated form:
   - **Connection Name** (required) - A descriptive name for this connection
   - **Connection Mode** dropdown - Selecting a mode changes which fields are visible
   - Mode-specific fields appear/disappear based on your selection
3. Click **"Save"** to create the connection

#### Connection Mode Dropdown

When you change the connection mode dropdown:
- Fields relevant to the selected mode become visible
- Fields for other modes are hidden
- Required field indicators update automatically

For example, selecting "Long Connection" mode for Feishu shows `app_id` and `app_secret` fields, while selecting "Webhook" mode shows `webhook_url` field.

#### Managing Existing Connections

Each connection has action buttons:
- **Edit** - Modify connection name or configuration
- **Enable/Disable** - Toggle the connection on/off
- **Delete** - Remove the connection (with confirmation dialog)

#### Verify Connection Button

When editing an existing connection, click **"Verify"** to test your credentials without enabling the connection.

### Web UI vs API

You can use **either** the Web UI **or** the API to manage connections - both work with the same underlying data. Changes made in the Web UI are immediately reflected in API calls and vice versa.

---

## Feishu (Lark)

### Overview

Feishu (also known as Lark internationally) integration currently supports Long Connection mode only.

| Mode | Config Key | Description | Status |
|------|------------|-------------|--------|
| **Long Connection** (Recommended) | `longconnection` | WebSocket-based, real-time, no callback URL needed | Supported |
| **Webhook** | `webhook` | HTTP callback, requires public URL | **Planned - Not Yet Implemented** |

> **Note:** Webhook mode is planned but not yet implemented in the current codebase. The schema includes webhook fields for forward compatibility, but only Long Connection mode is functional. If you need webhook functionality, please use DingTalk or WeCom.

**Dependency:** `lark-oapi >= 1.5.0` (for Long Connection mode)

### Prerequisites

Before configuring Xuanwu, you need to create and configure an application on Feishu Open Platform.

#### Step 1: Create Application

1. Visit [Feishu Open Platform](https://open.feishu.cn/) (or [Lark Developer](https://open.larksuite.com/) for international)
2. Log in with your Feishu account
3. Click **"Create Custom App"**
4. Fill in:
   - **App Name:** Your bot name (e.g., "Xuanwu Bot")
   - **App Description:** Brief description
   - **App Icon:** Upload an icon (optional)
5. Click **"Create"**

#### Step 2: Enable Bot Capability

1. In your app dashboard, go to **"Add Capabilities"**
2. Find and enable **"Bot"**
3. Configure bot settings:
   - **Bot Name:** Display name in chats
   - **Bot Description:** What the bot does

#### Step 3: Configure Permissions

Navigate to **"Permissions & Scopes"** and add these permissions:

| Permission | Scope ID | Description |
|------------|----------|-------------|
| Read chat info | `im:chat:readonly` | Access group/chat metadata |
| Send messages | `im:message:send_as_bot` | Send messages as bot |
| Receive group messages | `im:message.group_msg` | Receive messages in groups |
| Receive private messages | `im:message.p2p_msg` | Receive direct messages |
| Event subscription | `event:message` | Subscribe to message events |

#### Step 4: Publish Application

1. Go to **"Version Management"**
2. Click **"Create Version"**
3. Fill in version notes and submit for review
4. Once approved (or self-approved in development), **install the app** in your workspace

#### Step 5: Get Credentials

From your app's **"Credentials & Basic Info"** page, copy:
- **App ID**: Starts with `cli_`
- **App Secret**: 32-character string

### Configuration

#### Long Connection Mode (Only Supported Mode Currently)

Create a connection using the API:

```bash
curl -X POST "http://localhost:8000/api/channels/feishu/connections" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My Feishu Bot",
    "config": {
      "connection_mode": "longconnection",
      "app_id": "cli_xxxxxxxxxxxxxxxx",
      "app_secret": "your-app-secret-here"
    }
  }'
```

**Response:**
```json
{
  "id": "conn_abc123",
  "name": "My Feishu Bot",
  "channel_type": "feishu",
  "enabled": false,
  "config": {
    "connection_mode": "longconnection",
    "app_id": "cli_xxxxxxxxxxxxxxxx"
  }
}
```

**Configuration Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `connection_mode` | Yes | Set to `longconnection` |
| `app_id` | Yes | Feishu App ID (starts with `cli_`) |
| `app_secret` | Yes | Feishu App Secret |

#### Webhook Mode (Planned - Not Yet Implemented)

> **Warning:** This mode is not yet functional. The configuration is documented here for future reference only.

For webhook mode, you would need a publicly accessible URL:

```bash
curl -X POST "http://localhost:8000/api/channels/feishu/connections" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Feishu Webhook Bot",
    "config": {
      "connection_mode": "webhook",
      "webhook_url": "https://your-domain.com/api/channel-hooks/feishu/conn_abc123"
    }
  }'
```

**Configuration Fields (Future):**

| Field | Required | Description |
|-------|----------|-------------|
| `connection_mode` | Yes | Set to `webhook` |
| `webhook_url` | Yes | Your public webhook URL |

### Configuration Schema

The complete schema returned by `GET /api/channels/feishu/schema`:

```json
{
  "type": "object",
  "title": "Feishu",
  "description": "Feishu bot configuration",
  "properties": {
    "connection_mode": {
      "type": "string",
      "title": "Connection Mode",
      "description": "Select connection mode",
      "enum": ["longconnection", "webhook"],
      "enumLabels": {
        "longconnection": "Long Connection (Enterprise App)",
        "webhook": "Webhook (Custom Bot)"
      },
      "default": "longconnection"
    },
    "app_id": {
      "type": "string",
      "title": "App ID",
      "description": "Feishu application App ID",
      "placeholder": "cli_xxxxxxxxxx",
      "showWhen": {"connection_mode": "longconnection"}
    },
    "app_secret": {
      "type": "string",
      "title": "App Secret",
      "description": "Feishu application App Secret",
      "placeholder": "Your app secret",
      "showWhen": {"connection_mode": "longconnection"}
    },
    "webhook_url": {
      "type": "string",
      "title": "Webhook URL",
      "description": "Custom bot Webhook address",
      "placeholder": "https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
      "showWhen": {"connection_mode": "webhook"}
    }
  },
  "required_by_mode": {
    "longconnection": ["app_id", "app_secret"],
    "webhook": ["webhook_url"]
  }
}
```

### Message Types

**Inbound (Receiving):**
- Text messages only
- Content is extracted from JSON payload (`{"text": "message content"}`)

**Outbound (Sending):**
- Text messages only (no Markdown support in replies)
- Format: `{"text": "reply content"}`

### Enable & Test

#### Enable the Connection

```bash
curl -X POST "http://localhost:8000/api/channels/feishu/connections/{connection_id}/enable"
```

**Response:**
```json
{
  "status": "ok",
  "message": "Connection enabled"
}
```

#### Test Message Sending

1. In Feishu, add your bot to a group or start a direct chat with it
2. Send a message: `@YourBot Hello`
3. Check Xuanwu logs for incoming message
4. The agent should respond automatically

#### Verify Connection Status

```bash
curl "http://localhost:8000/api/channels/feishu/connections/{connection_id}"
```

### Troubleshooting

| Problem | Possible Cause | Solution |
|---------|----------------|----------|
| Connection fails immediately | Invalid App ID/Secret | Double-check credentials from Feishu console |
| Connection fails | App not published | Publish and install the app in your workspace |
| No messages received | Missing permissions | Ensure all required permissions are granted |
| No messages received | Bot not in chat | Add bot to the group or start DM with it |
| Cannot send replies | Missing send permission | Add `im:message:send_as_bot` permission |
| WebSocket disconnects | Network issues | Check firewall, Xuanwu will auto-reconnect |
| "App not installed" error | App not installed | Install app in workspace after publishing |
| "Multiprocessing error" | Environment restrictions | Check if running in Docker or cloud environments that restrict multiprocessing; ensure `spawn` or `fork` is available |
| SDK process dies immediately | Python version mismatch | Ensure Python 3.9+ and compatible `lark-oapi` version |

---

## DingTalk

### Overview

DingTalk integration supports two connection modes with a session-based webhook system for message replies.

| Mode | Config Key | Description |
|------|------------|-------------|
| **Stream Mode** (Recommended) | `stream` | WebSocket-based, no callback URL needed |
| **Webhook Mode** | `webhook` | HTTP callback, requires public URL |

**Dependency:** `dingtalk-stream >= 0.24.0` (for Stream mode)

#### Session Webhook Lifecycle

When using Stream mode, each incoming message carries a temporary `session_webhook` URL in its metadata. This webhook has important characteristics:

- **Validity Period:** The session webhook is only valid for approximately **15 minutes** after the message is received
- **One-time Use:** Designed for replying to the specific message that triggered it
- **Automatic Fallback:** If the session webhook expires, Xuanwu will attempt to use your configured custom webhook URL

**Recommendation:** If your agent processing may take longer than 15 minutes, configure a custom `webhook_url` as a fallback.

### Prerequisites

#### Step 1: Create Application

1. Visit [DingTalk Open Platform](https://open.dingtalk.com/)
2. Log in with your DingTalk account
3. Go to **"Developer Console"**
4. Click **"Create Application"** -> **"Enterprise Internal App"**
5. Fill in:
   - **App Name:** Your bot name
   - **App Description:** Brief description
   - **App Icon:** Upload an icon
6. Click **"Create"**

#### Step 2: Enable Robot Capability

1. In your app settings, go to **"App Features"**
2. Click **"Robot"** -> **"Create Robot"**
3. Configure:
   - **Robot Name:** Display name in chats
   - **Message Receive Mode:** Select **"Stream Mode"** for long connection
   - **Robot Introduction:** Description shown to users

#### Step 3: Configure Permissions

Navigate to **"Permission Management"** and add:

| Permission | Scope | Description |
|------------|-------|-------------|
| Chat info | `chat:read` | Read group information |
| Send to me | `im:send_to:me` | Bot can send to user |
| Send to conversation | `im:send_to:conversation` | Bot can send to groups |
| Receive messages | `message.receive` | Receive user messages |

#### Step 4: Publish Application

1. Go to **"Version Management"**
2. Create a new version
3. Submit for deployment
4. Grant required permissions when prompted

#### Step 5: Get Credentials

From your app's **"Basic Information"** or **"Robot"** settings, copy:
- **AppKey** (also called Client ID): Your app key
- **AppSecret** (also called Client Secret): Your app secret

### Configuration

#### Stream Mode (Recommended)

```bash
curl -X POST "http://localhost:8000/api/channels/dingtalk/connections" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My DingTalk Bot",
    "config": {
      "connection_mode": "stream",
      "client_id": "your-app-key",
      "client_secret": "your-app-secret"
    }
  }'
```

**Configuration Fields:**

| Field | Required | Description | Aliases |
|-------|----------|-------------|---------|
| `connection_mode` | Yes | Set to `stream` | - |
| `client_id` | Yes | DingTalk AppKey | Also accepts `app_key` |
| `client_secret` | Yes | DingTalk AppSecret | Also accepts `app_secret` |

> **Note:** For backward compatibility, `app_key` and `app_secret` are accepted as aliases for `client_id` and `client_secret` respectively.

#### Webhook Mode

For webhook mode with optional signature verification:

```bash
curl -X POST "http://localhost:8000/api/channels/dingtalk/connections" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "DingTalk Webhook Bot",
    "config": {
      "connection_mode": "webhook",
      "webhook_url": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
      "secret": "SEC_your_secret_for_signature"
    }
  }'
```

**Configuration Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `connection_mode` | Yes | Set to `webhook` |
| `webhook_url` | Yes | DingTalk robot webhook URL |
| `secret` | No | Secret for HMAC-SHA256 signature verification (starts with `SEC`) |

### Configuration Schema

The complete schema returned by `GET /api/channels/dingtalk/schema`:

```json
{
  "type": "object",
  "title": "DingTalk",
  "description": "DingTalk bot configuration",
  "properties": {
    "connection_mode": {
      "type": "string",
      "title": "Connection Mode",
      "description": "Select connection mode",
      "enum": ["stream", "webhook"],
      "enumLabels": {
        "stream": "Stream Mode (Enterprise Bot)",
        "webhook": "Webhook Robot"
      },
      "default": "stream"
    },
    "client_id": {
      "type": "string",
      "title": "Client ID (AppKey)",
      "description": "Application AppKey for Stream mode",
      "placeholder": "dingxxxxxxxxxx",
      "showWhen": {"connection_mode": "stream"}
    },
    "client_secret": {
      "type": "string",
      "title": "Client Secret (AppSecret)",
      "description": "Application AppSecret",
      "placeholder": "Application secret",
      "showWhen": {"connection_mode": "stream"}
    },
    "webhook_url": {
      "type": "string",
      "title": "Webhook URL",
      "description": "Custom bot Webhook address",
      "placeholder": "https://oapi.dingtalk.com/robot/send?access_token=xxx",
      "showWhen": {"connection_mode": "webhook"}
    },
    "secret": {
      "type": "string",
      "title": "Signing Secret",
      "description": "Webhook signing secret (optional)",
      "placeholder": "SEC...",
      "showWhen": {"connection_mode": "webhook"}
    }
  },
  "required_by_mode": {
    "stream": ["client_id", "client_secret"],
    "webhook": ["webhook_url"]
  }
}
```

### Message Types

**Inbound (Receiving):**
- Text messages
- Content extracted from `text.content` field

**Outbound (Sending):**
- Text messages only
- Format: `{"msgtype": "text", "text": {"content": "reply"}}`

### Reply Priority

When sending a reply, DingTalk handler uses this priority order:

1. **Session Webhook** - If available from the incoming message metadata and not expired (~15 min TTL)
2. **Custom Webhook** - If configured in the connection settings
3. **DingTalk API** - Fallback using access token (not fully implemented)

If the session webhook has expired and no custom webhook is configured, the reply will fail.

### Enable & Test

#### Enable the Connection

```bash
curl -X POST "http://localhost:8000/api/channels/dingtalk/connections/{connection_id}/enable"
```

#### Test Message Flow

1. In DingTalk, create a group and add your robot
2. Or start a direct chat with the robot
3. Send: `@YourBot Hello`
4. The agent should respond

### Troubleshooting

| Problem | Possible Cause | Solution |
|---------|----------------|----------|
| Stream connection fails | Invalid credentials | Verify AppKey and AppSecret |
| Stream connection fails | Proxy blocking WebSocket | Set `XUANWU_BYPASS_WS_PROXY=true` |
| Stream connection drops immediately | Incorrect client_id/secret format | Ensure client_id starts with `ding` and is correct |
| Messages not received | Robot not in Stream mode | Check robot settings in DingTalk console, enable Stream mode |
| Cannot reply | Session webhook expired | Session webhooks expire after ~15 minutes; configure a custom webhook as fallback |
| Reply not sent | Session webhook may have expired | Check if processing took >15 minutes |
| Messages sent but no reply visible | Reply sent to wrong endpoint | Verify session_webhook in metadata; check Xuanwu logs |
| "Permission denied" | Missing permissions | Add required permissions in DingTalk console |
| Robot not responding | Robot not activated | Publish app version and grant permissions |

#### Environment Variables

| Variable | Description |
|----------|-------------|
| `XUANWU_BYPASS_WS_PROXY` | Set to `true` to bypass WebSocket proxy (useful in corporate networks) |

---

## WeCom

### Overview

WeCom (WeChat Work) integration supports three connection modes:

| Mode | Config Key | Description |
|------|------------|-------------|
| **WebSocket Mode** (Recommended) | `websocket` | Long connection via AI Bot SDK |
| **Webhook Mode** | `webhook` | Group robot HTTP webhook |
| **Application API Mode** | `app` | Full bidirectional API with callback |

**Dependency:** `wecom-aibot-sdk >= 1.0.0` (for WebSocket mode)

**Supported Features (WebSocket Mode):**
- Text messages
- Image messages (receive metadata, URL)
- File messages (receive metadata, filename)
- Voice messages (receive metadata, URL)
- `enter_chat` events (user joins conversation)
- Streaming replies (`reply_stream`)
- Markdown formatting in outbound messages

### Prerequisites

#### Step 1: Access WeCom Admin Console

1. Visit [WeCom Admin Console](https://work.weixin.qq.com/wework_admin/frame)
2. Log in with admin account

#### Step 2: Create AI Bot (for WebSocket Mode)

1. Go to **"App Management"**
2. Navigate to **"Customer Service"** -> **"AI Bot"** or **"Internal Bot"**
3. Configure bot name and settings

#### Step 3: Get Bot Credentials

For WebSocket mode, you need:
- **Bot ID:** The unique identifier for your bot (starts with `aib`)
- **Bot Secret:** The secret key for authentication

Location varies by bot type:
- For AI Bot: Found in bot settings page
- For Custom App Bot: In app credentials section

#### Step 4: Configure Callback (Application API Mode Only)

If using Application API mode:
1. Go to your app's settings
2. Find **"Receive Messages"** settings
3. Configure callback URL: `https://your-domain.com/api/channel-hooks/wecom/{connection_id}`
4. Set Token and EncodingAESKey

### Configuration

#### WebSocket Mode (Recommended)

```bash
curl -X POST "http://localhost:8000/api/channels/wecom/connections" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "My WeCom Bot",
    "config": {
      "connection_mode": "websocket",
      "bot_id": "your-bot-id",
      "bot_secret": "your-bot-secret"
    }
  }'
```

**Configuration Fields:**

| Field | Required | Description | Aliases |
|-------|----------|-------------|---------|
| `connection_mode` | Yes | Set to `websocket` | - |
| `bot_id` | Yes | WeCom Bot ID | - |
| `bot_secret` | Yes | WeCom Bot Secret | Also accepts `secret` |

> **Note:** For backward compatibility, `secret` is accepted as an alias for `bot_secret`.

#### Webhook Mode

```bash
curl -X POST "http://localhost:8000/api/channels/wecom/connections" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "WeCom Webhook Bot",
    "config": {
      "connection_mode": "webhook",
      "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
    }
  }'
```

**Configuration Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `connection_mode` | Yes | Set to `webhook` |
| `webhook_url` | Yes | WeCom group bot webhook URL |

#### Application API Mode

For full bidirectional messaging via the WeCom API:

```bash
curl -X POST "http://localhost:8000/api/channels/wecom/connections" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "WeCom App Bot",
    "config": {
      "connection_mode": "app",
      "corpid": "your-corp-id",
      "corpsecret": "your-corp-secret",
      "agentid": "1000001"
    }
  }'
```

**Configuration Fields:**

| Field | Required | Description |
|-------|----------|-------------|
| `connection_mode` | Yes | Set to `app` |
| `corpid` | Yes | WeCom Corp ID (enterprise ID) |
| `corpsecret` | Yes | Application secret |
| `agentid` | Yes | Application Agent ID |

### Configuration Schema

The complete schema returned by `GET /api/channels/wecom/schema`:

```json
{
  "type": "object",
  "title": "WeCom",
  "description": "WeCom bot configuration",
  "properties": {
    "connection_mode": {
      "type": "string",
      "title": "Connection Mode",
      "description": "Select connection mode",
      "enum": ["websocket", "webhook"],
      "enumLabels": {
        "websocket": "Long Connection (Intelligent Robot)",
        "webhook": "Webhook Robot"
      },
      "default": "websocket"
    },
    "bot_id": {
      "type": "string",
      "title": "Bot ID",
      "description": "Intelligent robot Bot ID",
      "placeholder": "aib...",
      "showWhen": {"connection_mode": "websocket"}
    },
    "bot_secret": {
      "type": "string",
      "title": "Bot Secret",
      "description": "Intelligent robot Secret",
      "placeholder": "Bot secret",
      "showWhen": {"connection_mode": "websocket"}
    },
    "webhook_url": {
      "type": "string",
      "title": "Webhook URL",
      "description": "Group bot Webhook address",
      "placeholder": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx",
      "showWhen": {"connection_mode": "webhook"}
    }
  },
  "required_by_mode": {
    "websocket": ["bot_id", "bot_secret"],
    "webhook": ["webhook_url"]
  }
}
```

> **Note:** Application API mode (`app`) fields are validated internally but not shown in the default schema UI. Use the API directly for app mode configuration.

### Message Types

**Inbound (Receiving) - WebSocket Mode:**

| Type | Content | Metadata |
|------|---------|----------|
| Text | Message text | `req_id`, `chat_type`, `frame` |
| Image | Image URL | `req_id`, `chat_type`, `frame` |
| File | Filename | `req_id`, `chat_type`, `frame` |
| Voice | Voice URL | `req_id`, `chat_type`, `frame` |

**Inbound (Receiving) - Application API Mode:**

| Type | Content | Metadata |
|------|---------|----------|
| Text | Message content | `AgentID`, `CreateTime` |
| Image | Picture URL | `AgentID`, `CreateTime` |
| Voice | Media ID | `AgentID`, `CreateTime` |

**Outbound (Sending):**

| Mode | Format | Notes |
|------|--------|-------|
| WebSocket | Markdown | Sent via `reply_stream` with streaming support |
| Webhook | Text or Markdown | Configurable via `msgtype` in metadata |
| Application API | Text | Sent via WeCom message API |

**Supported Markdown in WeCom:**
- Bold: `**text**`
- Links: `[text](url)`
- Inline code: `` `code` ``
- Line breaks

> **Note:** Full markdown (headers, lists, code blocks) may not render correctly in all WeCom clients.

### Enable & Test

#### Enable the Connection

```bash
curl -X POST "http://localhost:8000/api/channels/wecom/connections/{connection_id}/enable"
```

#### Test Message Flow

1. In WeCom, find your bot in contacts or add it to a group
2. Send a test message
3. Check Xuanwu logs for incoming message
4. The agent should respond

#### Test Streaming Response

WeCom WebSocket mode supports streaming replies. When enabled, long responses will be sent progressively using the `stream_id` mechanism.

### Troubleshooting

| Problem | Possible Cause | Solution |
|---------|----------------|----------|
| WebSocket connection fails | Invalid credentials | Verify Bot ID and Secret |
| WebSocket connection fails | Proxy blocking | Set `XUANWU_BYPASS_WS_PROXY=true` |
| SDK import error | Missing dependency | Run `pip install wecom-aibot-sdk>=1.0.0` |
| Messages not received | Bot not properly configured | Check WeCom admin console settings |
| Cannot send messages | Invalid bot status | Ensure bot is active and published |
| Markdown not rendering | Limited markdown support | Use only basic formatting: bold, links, inline code |
| Streaming reply broken | Invalid stream_id | Check `stream_id` in metadata; ensure frame is available |
| Application API token error | Invalid credentials | Verify corpid and corpsecret |
| Messages sent but user sees nothing | Wrong touser value | Check that recipient user ID is correct |

#### Environment Variables

| Variable | Description |
|----------|-------------|
| `XUANWU_BYPASS_WS_PROXY` | Set to `true` to bypass WebSocket proxy |

---

## FAQ

### General Questions

**Q: Can I have multiple connections of the same channel type?**

A: Yes. You can create multiple connections for the same platform (e.g., multiple Feishu bots). Each connection has its own configuration and can be enabled/disabled independently.

**Q: How do I test without sending real messages?**

A: Use the `/verify` endpoint to test your credentials:
```bash
curl -X POST "http://localhost:8000/api/channels/{type}/connections/{id}/verify"
```
This validates your configuration without connecting or sending messages.

**Q: What if message send fails? Is there automatic retry?**

A: No, there is no automatic retry mechanism. If a send fails, the error is returned immediately. You should implement retry logic in your agent if needed.

**Q: How are credentials stored?**

A: All sensitive credentials (app secrets, tokens) are encrypted using AES-256-GCM before being stored in the database. The encryption key is derived from your Xuanwu configuration.

**Q: Can I switch connection modes without recreating the connection?**

A: Yes. Use the PATCH API or the Web UI Edit function to update the configuration:
```bash
curl -X PATCH "http://localhost:8000/api/channels/{type}/connections/{id}" \
  -H "Content-Type: application/json" \
  -d '{
    "config": {
      "connection_mode": "stream",
      "client_id": "new-client-id",
      "client_secret": "new-secret"
    }
  }'
```
You will need to disable and re-enable the connection for changes to take effect.

### Platform-Specific Questions

**Q: Why does my Feishu connection fail with "multiprocessing error"?**

A: Feishu handler uses Python multiprocessing to isolate the SDK. This may fail in:
- Some Docker configurations (use `--privileged` or appropriate security settings)
- Cloud functions/serverless environments
- Systems where `fork` or `spawn` is restricted

**Q: Why does my DingTalk reply fail after a long processing time?**

A: DingTalk session webhooks expire after approximately 15 minutes. If your agent takes longer to process:
1. Configure a custom `webhook_url` as fallback
2. Optimize your agent to respond faster
3. Send interim "processing" messages within the time window

**Q: Does WeCom support receiving images and files?**

A: In WebSocket mode, WeCom receives image, file, and voice messages. However, only metadata (URL or filename) is provided in the `InboundMessage.content`. The actual file must be downloaded separately using the provided URL.

---

## API Reference

### Channel Management APIs

All channel operations follow a REST pattern:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/channels` | List all available channel types |
| GET | `/api/channels/{type}/schema` | Get configuration schema for a channel type |
| GET | `/api/channels/{type}/connections` | List all connections for a channel type |
| POST | `/api/channels/{type}/connections` | Create a new connection |
| PATCH | `/api/channels/{type}/connections/{id}` | Update connection configuration |
| DELETE | `/api/channels/{type}/connections/{id}` | Delete a connection |
| POST | `/api/channels/{type}/connections/{id}/verify` | Verify connection configuration |
| POST | `/api/channels/{type}/connections/{id}/enable` | Enable (connect) a connection |
| POST | `/api/channels/{type}/connections/{id}/disable` | Disable (disconnect) a connection |

### Webhook Endpoint

For platforms using webhook mode, incoming webhooks are received at:

```
POST /api/channel-hooks/{channel_type}/{connection_id}
GET  /api/channel-hooks/{channel_type}/{connection_id}  (for verification)
```

The GET endpoint handles challenge verification for platforms like Feishu that require URL verification.

### Request/Response Examples

#### List Channel Types

```bash
curl "http://localhost:8000/api/channels"
```

**Response:**
```json
[
  {
    "type": "feishu",
    "name": "Feishu",
    "icon": "feishu",
    "mode": "bidirectional",
    "connection_count": 2
  },
  {
    "type": "dingtalk",
    "name": "DingTalk",
    "icon": "dingtalk",
    "mode": "bidirectional",
    "connection_count": 1
  },
  {
    "type": "wecom",
    "name": "WeCom",
    "icon": "wecom",
    "mode": "bidirectional",
    "connection_count": 0
  }
]
```

#### Get Configuration Schema

```bash
curl "http://localhost:8000/api/channels/feishu/schema"
```

**Response:** See platform-specific Configuration Schema sections above.

#### Create Connection

```bash
curl -X POST "http://localhost:8000/api/channels/feishu/connections" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production Bot",
    "config": {
      "connection_mode": "longconnection",
      "app_id": "cli_xxx",
      "app_secret": "secret"
    }
  }'
```

**Response:**
```json
{
  "id": "conn_a1b2c3d4",
  "name": "Production Bot",
  "channel_type": "feishu",
  "enabled": false,
  "is_default": false,
  "config": {
    "connection_mode": "longconnection",
    "app_id": "cli_xxx"
  }
}
```

#### Verify Connection

```bash
curl -X POST "http://localhost:8000/api/channels/feishu/connections/conn_a1b2c3d4/verify"
```

**Response (Success):**
```json
{
  "valid": true,
  "errors": []
}
```

**Response (Failure):**
```json
{
  "valid": false,
  "errors": ["app_secret is required for Long Connection mode"]
}
```

#### Enable Connection

```bash
curl -X POST "http://localhost:8000/api/channels/feishu/connections/conn_a1b2c3d4/enable"
```

**Response:**
```json
{
  "status": "ok",
  "message": "Connection enabled"
}
```

#### Disable Connection

```bash
curl -X POST "http://localhost:8000/api/channels/feishu/connections/conn_a1b2c3d4/disable"
```

**Response:**
```json
{
  "status": "ok",
  "message": "Connection disabled"
}
```

#### Update Connection

```bash
curl -X PATCH "http://localhost:8000/api/channels/feishu/connections/conn_a1b2c3d4" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Renamed Bot",
    "config": {
      "connection_mode": "longconnection",
      "app_id": "cli_new",
      "app_secret": "new_secret"
    }
  }'
```

#### Delete Connection

```bash
curl -X DELETE "http://localhost:8000/api/channels/feishu/connections/conn_a1b2c3d4"
```

**Response:**
```json
{
  "status": "ok",
  "message": "Connection deleted"
}
```

---

## Architecture

### Core Components

```
+-----------------------------------------------------------------------------+
|                       Xuanwu Channel Architecture                         |
+-----------------------------------------------------------------------------+
|                                                                              |
|  +----------------+     +----------------+     +----------------------+      |
|  | ChannelHandler |     | ChannelManager |     |    ChannelStore      |      |
|  |   (Interface)  |---->|   (Lifecycle)  |---->|   (User Config)      |      |
|  +----------------+     +----------------+     +----------------------+      |
|          |                      |                       |                    |
|          v                      v                       v                    |
|  +----------------+     +----------------+     +----------------------+      |
|  |  Built-in      |     |  Connection    |     |  Database Storage    |      |
|  |  Handlers      |     |  State Machine |     |  (AES-256 encrypted) |      |
|  |                |     |                |     |                      |      |
|  |  * Feishu      |     |  * CONNECTING  |     |  * Connection list   |      |
|  |  * DingTalk    |     |  * CONNECTED   |     |  * Config per conn   |      |
|  |  * WeCom       |     |  * DISCONNECTED|     |  * Enable/disable    |      |
|  +----------------+     +----------------+     +----------------------+      |
|                                                                              |
|  +----------------------------------------------------------------------+   |
|  |                    Extension Channels (Handlers)                      |   |
|  |  +------------+  +------------+  +------------+  +-----------+       |   |
|  |  |   Feishu   |  |  DingTalk  |  |   WeCom    |  |    ...    |       |   |
|  |  | (Multiproc)|  | (Multiproc)|  | (Async WS) |  |           |       |   |
|  |  +------------+  +------------+  +------------+  +-----------+       |   |
|  +----------------------------------------------------------------------+   |
|                                                                              |
+-----------------------------------------------------------------------------+
```

### ChannelHandler Interface

All channel handlers implement this interface:

**Lifecycle Methods:**
- `setup(connection_config)` - Initialize with configuration
- `start(context)` - Start the channel handler
- `connect()` - Establish long connection (WebSocket/Stream)
- `disconnect()` - Close long connection
- `stop()` - Stop and cleanup resources

**Message Methods:**
- `handle_inbound(request)` - Process incoming webhook messages
- `send_message(outbound)` - Send outgoing messages
- `set_message_callback(callback)` - Set callback for long connection messages

**Configuration Methods:**
- `validate_config(config)` - Validate connection settings
- `describe_schema()` - Return configuration schema

### Connection States

```
                    +----------------+
                    |  DISCONNECTED  |
                    +-------+--------+
                            | enable()
                            v
                    +----------------+
                    |   CONNECTING   |
                    +-------+--------+
                            | success
                            v
                    +----------------+
         error ---->|   CONNECTED    |<---- auto-reconnect
                    +-------+--------+
                            | disable() / error
                            v
                    +----------------+
                    |  DISCONNECTED  |
                    +----------------+
```

### Message Flow

**Inbound Message Flow (User -> Agent):**

```
+---------+     +-------------+     +----------------+     +---------+
|  User   |---->|  Platform   |---->| ChannelHandler |---->|  Agent  |
|         |     |  (Feishu/   |     |                |     |         |
|         |     |  DingTalk/  |     | * Parse msg    |     | * Route |
|         |     |  WeCom)     |     | * Create       |     | * Think |
|         |     |             |     |   InboundMsg   |     | * Reply |
+---------+     +-------------+     +----------------+     +---------+
```

**Outbound Message Flow (Agent -> User):**

```
+---------+     +----------------+     +-------------+     +---------+
|  Agent  |---->| ChannelHandler |---->|  Platform   |---->|  User   |
|         |     |                |     |  API        |     |         |
| * Reply |     | * Format msg   |     |             |     |         |
|         |     | * Send via API |     |             |     |         |
+---------+     +----------------+     +-------------+     +---------+
```

---

## Summary

Xuanwu Channel system provides:

1. **Unified Interface** - All channels implement the same ChannelHandler interface
2. **Multiple Platforms** - Support for Feishu, DingTalk, WeCom out of the box
3. **Flexible Connection** - Choose between long connection, webhook, or application API mode
4. **Web UI Management** - Full-featured browser interface for non-API users
5. **Auto-Reconnect** - Long connection modes handle disconnection gracefully
6. **User Isolation** - Each user has independent channel configurations
7. **Secure Storage** - Credentials encrypted with AES-256-GCM
8. **Easy Extension** - Add new platform integrations by implementing ChannelHandler

For any platform integration, follow the same pattern:
1. Create app on platform console
2. Configure permissions
3. Get credentials
4. Create connection in Xuanwu (via API or Web UI)
5. Verify configuration
6. Enable and test
