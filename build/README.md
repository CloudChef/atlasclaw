# AtlasClaw

This guide describes how to deploy AtlasClaw in your own environment using pre-built Docker images.

## Prerequisites

### System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 2 cores | 4+ cores |
| RAM | 4 GB | 8+ GB |
| Disk | 20 GB SSD | 100+ GB SSD |
| OS | Linux (CentOS 7+, Ubuntu 18.04+, or equivalent) | Latest LTS |

### Required Software

- **Docker** 20.10 or higher
- **Docker Compose** 2.0 or higher

### Install Docker

**CentOS/RHEL:**
```bash
sudo yum install -y yum-utils
sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl start docker
sudo systemctl enable docker
```

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

**Verify Installation:**
```bash
docker --version
docker compose version
```

---

## Quick Start

### 1. Create Deployment Directory

```bash
mkdir -p /opt/atlasclaw/{workspace,data,extensions/{providers,skills,channels}}
cd /opt/atlasclaw
```

**Directory Structure:**

```
/opt/atlasclaw/
├── docker-compose.yml      # Docker Compose orchestration file
├── workspace/              # Configuration, logs, user data
│   └── atlasclaw.json      # Main configuration file
├── data/                   # SQLite database and runtime data
└── extensions/
    ├── providers/          # Provider extensions
    ├── skills/             # Custom skills
    └── channels/           # Custom channels
```

### 2. Download Compose File

```bash
curl -o docker-compose.yml https://raw.githubusercontent.com/CloudChef/AtlasClaw-Core/main/build/docker-compose.yml
```

### 3. Download Extensions (Optional)

Download providers, skills, and channels from the official repository:

```bash
cd /opt/atlasclaw/extensions

# Clone the repository
git clone https://github.com/CloudChef/atlasclaw-providers.git src

# Copy providers (optional)
cp -r src/providers/* ./providers/ 2>/dev/null || true

# Copy skills (optional)
cp -r src/skills/* ./skills/ 2>/dev/null || true

# Copy channels (optional)
cp -r src/channels/* ./channels/ 2>/dev/null || true

# Remove source directory
rm -rf src
```

**Note:** Extensions are optional. AtlasClaw will start successfully even without any extensions installed.

### 4. Configure LLM Model (Required)

**⚠️ You MUST configure at least one LLM token before starting AtlasClaw.**

The service will fail to start without a valid model configuration. Tokens can be added via:
- Configuration file (atlasclaw.json) - for initial setup
- Web UI (Admin Panel) - for runtime management via CRUD

#### Supported LLM Providers

| Provider | Model Example | base_url | api_type |
|----------|---------------|----------|----------|
| DeepSeek | deepseek-chat | https://api.deepseek.com | openai |
| OpenAI | gpt-4 | https://api.openai.com/v1 | openai |
| Moonshot (Kimi) | kimi-k2.5 | https://api.moonshot.cn/v1 | openai |

### 4. Create Configuration

Create `/opt/atlasclaw/workspace/atlasclaw.json`:

```json
{
  "workspace": {
    "path": "/app/data"
  },
  "database": {
    "type": "sqlite",
    "sqlite": {
      "path": "/app/data/atlasclaw.db"
    }
  },
  "providers_root": "/app/extensions/providers",
  "model": {
    "primary": "deepseek-main",
    "fallbacks": [],
    "temperature": 0.2,
    "tokens": [
      {
        "id": "deepseek-main",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key": "YOUR_API_KEY_HERE",
        "api_type": "openai"
      }
    ]
  },
  "auth": {
    "provider": "api_key",
    "api_key": {
      "keys": {
        "sk-your-secret-key": {
          "user_id": "admin",
          "roles": ["admin"]
        }
      }
    }
  }
}
```

**⚠️ Critical Configuration Requirements:**

1. **You MUST replace `YOUR_API_KEY_HERE`** with your actual LLM API key (e.g., DeepSeek, OpenAI)
2. **`model.tokens` cannot be empty** - At least one token entry is **required** for startup
3. **`providers_root`** can be set to `/app/extensions/providers` (container path) or left empty
4. Database path uses container path `/app/data/atlasclaw.db`
5. `workspace.path` uses container path `/app/data`

**Example with real API key:**
```json
{
  "model": {
    "primary": "deepseek-main",
    "tokens": [
      {
        "id": "deepseek-main",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key": "sk-abc123xyz...",
        "api_type": "openai"
      }
    ]
  }
}
```

Set proper permissions:
```bash
chmod 600 /opt/atlasclaw/workspace/atlasclaw.json
```

### 5. Start AtlasClaw

```bash
cd /opt/atlasclaw
docker compose up -d
```

### 6. Verify Deployment

```bash
curl http://localhost:8000/api/health
```

Expected response:
```json
{"status": "healthy", "timestamp": "2026-03-23T10:00:00+00:00"}
```

Access the web UI at: `http://your-server-ip:8000`

---

## Optional: Skills & Channels Configuration

Skills and channels in `/opt/atlasclaw/extensions/` are automatically loaded on startup.

### Skill Structure

**Markdown Skill:**
```
/opt/atlasclaw/extensions/skills/
└── deployment/
    ├── SKILL.md             # Skill definition
    ├── requirements.txt     # Dependencies (optional)
    └── scripts/
        └── deploy.sh        # Helper scripts (optional)
```

**Executable Skill:**
```
/opt/atlasclaw/extensions/skills/
└── monitoring/
    ├── __init__.py
    ├── skill.py             # Python implementation
    ├── requirements.txt     # Dependencies
    └── config.json
```

### Channel Configuration

Add to `/opt/atlasclaw/workspace/atlasclaw.json`:

```json
{
  "channels": {
    "slack-bot": {
      "type": "slack",
      "config": {
        "token": "xoxb-your-bot-token",
        "signing_secret": "your-signing-secret"
      }
    }
  }
}
```

### Reload Extensions

```bash
# Reload without restart
docker compose exec atlasclaw atlasclaw reload all

# Or restart
docker compose restart atlasclaw
```

---

## Operations

### View Logs

```bash
docker compose logs -f atlasclaw
```

### Stop Services

```bash
docker compose down
```

### Update to Latest Version

```bash
docker compose pull
docker compose up -d
```

### Backup

```bash
# Backup data and config
tar -czf atlasclaw-backup-$(date +%Y%m%d).tar.gz /opt/atlasclaw/data /opt/atlasclaw/workspace
```

---

## Configuration Reference

### LLM Provider

```json
{
  "model": {
    "primary": "deepseek-main",
    "tokens": [
      {
        "id": "deepseek-main",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key": "your-api-key"
      }
    ]
  }
}
```

### Authentication (API Key)

```json
{
  "auth": {
    "provider": "api_key",
    "api_key": {
      "keys": {
        "sk-your-key": {
          "user_id": "admin",
          "roles": ["admin"]
        }
      }
    }
  }
}
```

---

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker compose logs atlasclaw

# Verify config syntax
docker run --rm -v /opt/atlasclaw/workspace/atlasclaw.json:/app/atlasclaw.json:ro registry.cn-shanghai.aliyuncs.com/atlasclaw/atlasclaw:latest python -c "import json; json.load(open('/app/atlasclaw.json'))"
```

### Providers Not Loading

```bash
# Check providers directory exists
ls -la /opt/atlasclaw/extensions/providers/

# Verify providers_root in config
cat /opt/atlasclaw/workspace/atlasclaw.json | grep -A 1 providers_root
```

### Port Already in Use

Edit `docker-compose.yml`:

```yaml
ports:
  - "8080:8000"  # Change 8080 to your preferred port
```

### Permission Denied

```bash
chmod 600 /opt/atlasclaw/workspace/atlasclaw.json
chown -R $(id -u):$(id -g) /opt/atlasclaw/data
```

---

## Support

For technical support, contact your AtlasClaw representative or refer to the full documentation at [docs link].
