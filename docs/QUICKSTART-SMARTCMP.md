# AtlasClaw + SmartCMP SaaS Quick Start Guide

This guide walks you through deploying AtlasClaw from scratch and integrating it with the SmartCMP SaaS platform, enabling AI-powered conversations for viewing CMP pending approvals, approving/rejecting tickets, requesting resources, and more.

---

## 1. Prerequisites

| Item | Requirement |
|------|-------------|
| OS | Linux / macOS / Windows |
| Python | 3.11+ |
| Git | 2.x |
| LLM API Key | A model that supports **Function Calling** (e.g., DeepSeek-Chat, GPT-4) |

> **Note**: Reasoning models like `deepseek-reasoner` do **not** support tool calling. Use `deepseek-chat` or similar models instead.

---

## 2. Clone Repositories

```bash
# Clone the main project
git clone https://github.com/CloudChef/atlasclaw.git
cd atlasclaw

# Clone the providers repository (must be in the same parent directory)
cd ..
git clone https://github.com/CloudChef/atlasclaw-providers.git
cd atlasclaw
```

Expected directory structure:

```
your-workspace/
├── atlasclaw/                  # Main project
└── atlasclaw-providers/        # Extensions (Providers, Skills, Channels)
    ├── providers/
    │   └── SmartCMP-Provider/  # SmartCMP integration
    ├── skills/
    └── channels/
```

---

## 3. Install Dependencies

```bash
pip install -r requirements.txt
```

> If `wecom-aibot-sdk` fails to install, you can safely ignore it — it does not affect SmartCMP functionality.

---

## 4. Configure SmartCMP Provider

AtlasClaw supports 3 authentication modes for connecting to SmartCMP. The system **auto-detects** the mode based on which fields are configured — no explicit `auth_type` field is needed.

### Authentication Modes Overview

| Mode | Scenario | How it works | Config fields needed |
|------|----------|--------------|----------------------|
| **SSO** | Embedded in CMP via Nginx reverse-proxy | Browser `CloudChef-Authenticate` cookie is automatically passed through | `base_url` only |
| **Cookie** | Have a valid CMP session cookie | Directly use a pre-obtained cookie value | `base_url` + `cookie` |
| **Credential** | Username/password login | Auto-login to CMP API to obtain a session | `base_url` + `username` + `password` |

> **Auto-detection priority**: SSO (browser cookie) > Static Cookie > Credential.
> Only fill the fields for your chosen mode. Leave others empty.

### Option A: SSO Mode (CMP Embedded)

When AtlasClaw is deployed behind the same Nginx as CMP, the browser automatically sends `CloudChef-Authenticate` cookie. No static credentials are needed.

**`atlasclaw.json`:**
```json
"service_providers": {
  "smartcmp": {
    "default": {
      "base_url": "https://172.16.0.81"
    }
  }
}
```

> **Important**: In SSO mode, `base_url` should be a **hardcoded URL** (not `${CMP_URL}`). Do NOT set `CMP_COOKIE`, `CMP_USERNAME`, or `CMP_PASSWORD` in `.env`.

### Option B: Cookie Mode

Use a pre-obtained CMP session cookie for server-to-server integrations or testing.

**`atlasclaw.json`:**
```json
"service_providers": {
  "smartcmp": {
    "default": {
      "base_url": "https://cmp.example.com",
      "cookie": "${CMP_COOKIE}"
    }
  }
}
```

**`.env`:**
```ini
CMP_COOKIE=eyJhbGciOiJIUzI1NiJ9...
```

### Option C: Credential Mode

The system logs in to CMP using username and MD5-hashed password to obtain a session.

**`atlasclaw.json`:**
```json
"service_providers": {
  "smartcmp": {
    "default": {
      "base_url": "${CMP_URL}",
      "username": "${CMP_USERNAME}",
      "password": "${CMP_PASSWORD}"
    }
  }
}
```

**`.env`:**
```ini
CMP_URL=https://console.smartcmp.cloud
CMP_USERNAME=your-cmp-username
CMP_PASSWORD=your-cmp-password-md5-hash
```

---

## 5. Configure Environment Variables

Copy `.env.example` to `.env` and update the key settings:

```bash
cp .env.example .env
```

Edit the `.env` file:

```ini
# ============================================
# LLM Model Configuration (Required)
# ============================================
# Example using DeepSeek-Chat (must support Function Calling)
TOKEN_2_PROVIDER=deepseek
TOKEN_2_MODEL=deepseek-chat
TOKEN_2_BASE_URL=https://api.deepseek.com
TOKEN_2_API_KEY=<your-deepseek-api-key>

LLM_TEMPERATURE=0.2

# ============================================
# SmartCMP Configuration (choose ONE mode)
# ============================================
# --- Credential Mode: fill all three ---
CMP_URL=https://console.smartcmp.cloud
CMP_USERNAME=your-cmp-username
CMP_PASSWORD=your-cmp-password-md5-hash

# --- Cookie Mode: fill cookie only ---
# CMP_COOKIE=eyJhbGciOiJIUzI1NiJ9...

# --- SSO Mode: no CMP env vars needed (hardcode base_url in atlasclaw.json) ---

# ============================================
# Authentication Configuration
# ============================================
ATLASCLAW_JWT_SECRET=change-me-to-a-secure-secret-key
```

### Configuration Reference

| Variable | Description | Used by Mode |
|----------|-------------|---------------|
| `TOKEN_2_MODEL` | LLM model name, **must support Function Calling** | All |
| `TOKEN_2_API_KEY` | LLM API Key | All |
| `CMP_URL` | SmartCMP platform URL | Credential |
| `CMP_USERNAME` | SmartCMP login username | Credential |
| `CMP_PASSWORD` | SmartCMP login password (MD5 hash) | Credential |
| `CMP_COOKIE` | Static CMP session cookie | Cookie |

---

## 6. Start the Service

```bash
uvicorn app.atlasclaw.main:app --host 0.0.0.0 --port 8000
```

On successful startup, the logs will show:

```
[AtlasClaw] Agent created with model: openai/deepseek-chat
[AtlasClaw] Skills loaded: 30 executable, 19 markdown
[AtlasClaw] Application started successfully
```

> Verify that **Skills loaded** shows an executable count > 13 (which includes the SmartCMP script tools).

To run in the background (Linux):

```bash
nohup uvicorn app.atlasclaw.main:app --host 0.0.0.0 --port 8000 > atlasclaw.log 2>&1 &
```

---

## 7. Access the Web UI

Open your browser and navigate to:

```
http://<server-ip>:8000
```

Default login credentials:

| Username | Password |
|----------|----------|
| `admin` | `admin` |

> It is recommended to change the password after the first login.

---

## 8. Test: View CMP Pending Approvals

After logging in, enter the following message in the chat interface:

```
List CMP pending approvals
```

The agent will automatically:
1. Recognize the intent and invoke the `smartcmp_list_pending` tool
2. Authenticate with the SmartCMP platform using the configured CMP credentials
3. Retrieve the pending tickets and display them in a table

### Expected Response Example

The agent will return something like:

```
Pending Approvals - 3 items

| # | Priority | Name                                  | Ticket ID         | Type            | Requester      | Waiting   |
|---|----------|---------------------------------------|-------------------|-----------------|----------------|-----------|
| 1 | High     | Test ticket for build verification    | TIC20260316000001 | Incident Ticket | user@email.com | 431 hours |
| 2 | High     | Urgent request                        | TIC20260313000006 | Problem Ticket  | user@email.com | 503 hours |
| 3 | High     | N/A                                   | TIC20260313000004 | Problem Ticket  | user@email.com | 504 hours |
```

### More Conversation Examples

```
# Approve a ticket
Approve ticket TIC20260316000001

# Reject a ticket
Reject ticket TIC20260313000006, reason: incomplete information

# View service catalog
List available CMP services

# Query cloud resource costs
Show cloud resource cost optimization suggestions for this month
```

---

## 9. Troubleshooting

### Q: The agent searches the web instead of calling SmartCMP tools?

**Cause**: The configured model does not support Function Calling (e.g., `deepseek-reasoner`).

**Solution**: Ensure the model in `.env` supports tool calling:
```ini
TOKEN_2_MODEL=deepseek-chat    # ✅ Supports Function Calling
# TOKEN_1_MODEL=deepseek-reasoner  # ❌ Not supported
```

Also verify that `model.primary` in `atlasclaw.json` points to the correct token:
```json
"model": {
  "primary": "model-2"
}
```

### Q: SmartCMP returns a 401 authentication error?

**Check** (depends on your authentication mode):

For **Credential** mode:
1. Verify `CMP_USERNAME` and `CMP_PASSWORD` in `.env` are correct
2. Confirm the account can log in at your CMP URL

For **SSO** mode:
1. Verify Nginx is correctly proxying cookies to AtlasClaw
2. Check that `CloudChef-Authenticate` cookie is present in browser

For **Cookie** mode:
1. Verify the cookie value in `CMP_COOKIE` is still valid (not expired)

### Q: Installation fails with `wecom-aibot-sdk` error?

This can be safely ignored, or install with:
```bash
pip install -r requirements.txt --ignore-installed wecom-aibot-sdk || true
```

---

## 10. Docker Deployment (Optional)

If you prefer not to install a Python environment, you can deploy with Docker:

```bash
# Prepare directories
mkdir -p /opt/atlasclaw/{workspace,data,extensions/{providers,skills,channels}}

# Copy configuration files
cp atlasclaw.json /opt/atlasclaw/workspace/
cp .env /opt/atlasclaw/workspace/

# Copy extensions
cp -r ../atlasclaw-providers/providers/* /opt/atlasclaw/extensions/providers/
cp -r ../atlasclaw-providers/skills/* /opt/atlasclaw/extensions/skills/

# Start
cd build
docker-compose up -d
```

Access `http://<server-ip>:8000` to start using the application.

---

## 11. Support

If you encounter any issues, please provide the following information:
- The `Skills loaded` and `Agent created with model` lines from the startup logs
- Model configuration from `.env` (with API keys redacted)
- Error messages from the browser developer console (F12)
