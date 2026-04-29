# Webhook Skill Dispatch

AtlasClaw webhooks let an external system invoke one provider-qualified
Markdown skill through the agent runtime. This is intended for backend
automation, not for interactive chat entry points.

## Configuration

Webhook systems are configured in `atlasclaw.json`:

```json
{
  "providers_root": "../atlasclaw-providers/providers",
  "webhook": {
    "enabled": true,
    "header_name": "X-AtlasClaw-SK",
    "systems": [
      {
        "system_id": "external-review",
        "enabled": true,
        "sk_env": "ATLASCLAW_WEBHOOK_SK_EXTERNAL_REVIEW",
        "default_agent_id": "main",
        "allowed_skills": ["example_provider:backend-agent"]
      }
    ]
  }
}
```

The webhook secret is read from the environment variable named by `sk_env`.
Do not store webhook secrets directly in `atlasclaw.json`.

## Dispatch Request

Send a request to `POST /api/webhook/dispatch` with the configured secret
header:

```bash
curl -X POST "$ATLASCLAW_URL/api/webhook/dispatch" \
  -H "Content-Type: application/json" \
  -H "X-AtlasClaw-SK: $WEBHOOK_SECRET" \
  -d '{
    "skill": "example_provider:backend-agent",
    "args": {
      "provider_instance": "default",
      "request_id": "REQ-10001"
    }
  }'
```

The route accepts the task and runs it asynchronously. The target skill must be
a provider-qualified Markdown skill already loaded from `providers_root`.
Executable tool names are not accepted as direct webhook targets.

## Robot Profile Execution

Use a robot profile when a webhook-triggered backend skill must call a provider
with an administrator-owned or robot-owned credential instead of a browser user
credential.

Configure the robot profile under the provider instance:

```json
{
  "service_providers": {
    "example_provider": {
      "default": {
        "base_url": "${PROVIDER_URL}",
        "auth_type": "user_token",
        "robot_auth": {
          "backend_bot": {
            "auth_type": "provider_token",
            "provider_token": "${PROVIDER_ROBOT_TOKEN}",
            "allowed_skills": ["example_provider:backend-agent"]
          }
        }
      }
    }
  }
}
```

The `auth_type` inside `robot_auth.<profile>` is the provider authentication
mode selected for that robot profile. It must be a single auth mode supported
by the provider schema, and the profile must include the fields required by
that mode. `provider_token` is the recommended shape for administrator-managed
robot tokens, but a provider may also support other robot credential modes such
as username/password credentials:

```json
{
  "robot_auth": {
    "backend_bot": {
      "auth_type": "credential",
      "username": "${PROVIDER_ROBOT_USERNAME}",
      "password": "${PROVIDER_ROBOT_PASSWORD}",
      "allowed_skills": ["example_provider:backend-agent"]
    }
  }
}
```

Do not configure `robot_auth.<profile>.auth_type` as an ordered fallback chain.
Robot execution uses exactly one deterministic credential profile.

Then select it in the webhook payload:

```json
{
  "skill": "example_provider:backend-agent",
  "args": {
    "provider_instance": "default",
    "robot_profile": "backend_bot",
    "request_id": "REQ-10001"
  }
}
```

The validation path is:

1. Authenticate the webhook secret.
2. Confirm the requested skill is in the webhook system `allowed_skills`.
3. Resolve `args.provider_instance` against the target provider type.
4. Resolve `args.robot_profile` under that provider instance.
5. Confirm the robot profile allows the requested skill.
6. Build a runtime-only provider config for the selected instance and robot
   credential.

`args.instance` is not used for robot execution. Use `provider_instance`.

## Runtime Credential Flow

Robot credentials are not inserted into the prompt. For each tool execution,
AtlasClaw serializes the narrowed provider config into the child process
environment as `ATLASCLAW_PROVIDER_CONFIG`. The runtime also sets these
selectors when robot execution is active:

```text
ATLASCLAW_PROVIDER_TYPE
ATLASCLAW_PROVIDER_INSTANCE
ATLASCLAW_ROBOT_PROFILE
```

Provider scripts use those values to select the exact configured instance and
fail closed when it is missing. Traces and webhook responses redact token,
password, and cookie-like fields.

## Security Notes

- Keep webhook secrets and robot credentials in environment variables.
- Allow only provider-qualified backend skills in webhook `allowed_skills`.
- Keep each robot profile allowlist as small as possible.
- Use a provider-native robot credential whose upstream audit identity is
  acceptable for automated execution.
- Do not reuse an individual user's personal token as a webhook robot token.
