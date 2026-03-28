# Xuanwu Enterprise Deployment Guide

> **For Enterprise Customers**: This guide provides instructions for deploying Xuanwu using Docker Compose with MySQL 8.5.

---

## Prerequisites

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| CPU | 4 cores | 8+ cores |
| RAM | 8 GB | 16+ GB |
| Disk | 50 GB SSD | 200+ GB SSD |
| Docker | 20.10+ | Latest |
| Docker Compose | 2.0+ | Latest |
| MySQL | 8.5 LTS | 8.5 LTS |

---

## Quick Start

### 1. Prepare Directory Structure

```bash
mkdir -p /opt/xuanwu/{config,data,logs,backups}
cd /opt/xuanwu
```

### 2. Create Configuration File

Create `/opt/xuanwu/config/xuanwu.json`:

```json
{
  "workspace": {
    "path": "./data"
  },
  "database": {
    "type": "mysql",
    "mysql": {
      "host": "mysql",
      "port": 3306,
      "database": "xuanwu",
      "user": "xuanwu",
      "password": "change-to-secure-password",
      "charset": "utf8mb4"
    },
    "pool_size": 20,
    "max_overflow": 30
  },
  "model": {
    "primary": "deepseek-main",
    "fallbacks": [],
    "temperature": 0.2,
    "selection_strategy": "health",
    "tokens": [
      {
        "id": "deepseek-main",
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
        "api_key": "your-api-key-here",
        "api_type": "openai",
        "priority": 100,
        "weight": 100
      }
    ]
  },
  "service_providers": {},
  "auth": {
    "provider": "local",
    "jwt": {
      "secret_key": "${JWT_SECRET_KEY}",
      "expires_minutes": 1440,
      "issuer": "xuanwu",
      "header_name": "Xuanwu-Authenticate",
      "cookie_name": "Xuanwu-Auth"
    }
  },
  "encryption": {
    "key": "${XUANWU_ENCRYPTION_KEY}"
  }
}
```

### 3. Create Docker Compose File

Create `/opt/xuanwu/docker-compose.yml`:

```yaml
version: '3.8'

services:
  xuanwu:
    image: xuanwu-core:latest
    container_name: xuanwu
    ports:
      - "8000:8000"
    volumes:
      - ./config/xuanwu.json:/app/xuanwu.json:ro
      - ./data:/app/data
      - ./logs:/app/logs
    depends_on:
      mysql:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 60s

  mysql:
    image: mysql:8.5
    container_name: xuanwu-mysql
    environment:
      MYSQL_ROOT_PASSWORD: change-to-secure-root-password
      MYSQL_DATABASE: xuanwu
      MYSQL_USER: xuanwu
      MYSQL_PASSWORD: change-to-secure-password
    volumes:
      - ./mysql-data:/var/lib/mysql
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "root", "-pchange-to-secure-root-password"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 60s
    command:
      - --character-set-server=utf8mb4
      - --collation-server=utf8mb4_unicode_ci
      - --default-authentication-plugin=mysql_native_password
```

### 4. Start Services

```bash
cd /opt/xuanwu
docker-compose up -d
```

### 5. Run Database Migrations

```bash
docker-compose exec xuanwu alembic upgrade head
```

### 6. Verify Deployment

```bash
# Check container status
docker-compose ps

# Health check
curl http://localhost:8000/api/health
# Expected: {"status": "healthy", "timestamp": "..."}
```

---

## Configuration Reference

### Database

```json
{
  "database": {
    "type": "mysql",
    "mysql": {
      "host": "mysql",
      "port": 3306,
      "database": "xuanwu",
      "user": "xuanwu",
      "password": "secure-password",
      "charset": "utf8mb4"
    },
    "pool_size": 20,
    "max_overflow": 30
  }
}
```

### LLM Provider

```json
{
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
        "api_key": "your-api-key",
        "api_type": "openai"
      }
    ]
  }
}
```

### Authentication

**Local (Username/Password):**
```json
{
  "auth": {
    "provider": "local",
    "jwt": {
      "secret_key": "${JWT_SECRET_KEY}",
      "expires_minutes": 1440,
      "issuer": "xuanwu",
      "header_name": "Xuanwu-Authenticate",
      "cookie_name": "Xuanwu-Auth"
    }
  }
}
```

**OIDC JWT (API Bearer Tokens):**
```json
{
  "auth": {
    "provider": "oidc_jwt",
    "oidc": {
      "issuer": "https://keycloak.example.com/realms/myrealm",
      "client_id": "xuanwu",
      "jwks_uri": "https://keycloak.example.com/realms/myrealm/protocol/openid-connect/certs"
    }
  }
}
```

**OIDC Login (Browser SSO):**
```json
{
  "auth": {
    "provider": "oidc_login",
    "oidc": {
      "issuer": "https://keycloak.example.com/realms/myrealm",
      "client_id": "xuanwu",
      "client_secret": "${OIDC_CLIENT_SECRET}",
      "redirect_uri": "https://xuanwu.example.com/api/auth/callback",
      "authorization_endpoint": "https://keycloak.example.com/realms/myrealm/protocol/openid-connect/auth",
      "token_endpoint": "https://keycloak.example.com/realms/myrealm/protocol/openid-connect/token",
      "userinfo_endpoint": "https://keycloak.example.com/realms/myrealm/protocol/openid-connect/userinfo",
      "jwks_uri": "https://keycloak.example.com/realms/myrealm/protocol/openid-connect/certs",
      "scopes": ["openid", "profile", "email"],
      "pkce_enabled": true,
      "pkce_method": "S256"
    }
  }
}
```

**OIDC/OAuth2:**
```json
{
  "auth": {
    "provider": "oidc",
    "oidc": {
      "issuer": "https://auth.company.com",
      "client_id": "xuanwu-client",
      "client_secret": "client-secret",
      "redirect_uri": "https://xuanwu.company.com/api/auth/callback"
    }
  }
}
```

---

## Operations

### View Logs

```bash
docker-compose logs -f xuanwu
docker-compose logs -f mysql
```

### Backup

```bash
#!/bin/bash
# /opt/xuanwu/backup.sh

DATE=$(date +%Y%m%d_%H%M%S)

# Backup database
docker exec xuanwu-mysql mysqldump -u root -p'root-password' xuanwu | gzip > ./backups/xuanwu_${DATE}.sql.gz

# Backup config and data
tar -czf ./backups/xuanwu_data_${DATE}.tar.gz ./config ./data

# Keep only last 30 days
find ./backups -name "*.gz" -mtime +30 -delete
```

### Update

```bash
# Pull latest image
docker-compose pull xuanwu

# Restart with migrations
docker-compose up -d
docker-compose exec xuanwu alembic upgrade head
```

### Stop

```bash
docker-compose down
```

---

## Troubleshooting

### Service Not Starting

```bash
# Check logs
docker-compose logs xuanwu

# Check config syntax
docker-compose exec xuanwu python -c "import json; json.load(open('xuanwu.json'))"
```

### Database Connection Failed

```bash
# Check MySQL is healthy
docker-compose ps mysql

# Test connection manually
docker-compose exec mysql mysql -u xuanwu -p -e "SELECT 1"
```

---

## Security Notes

### Data Encryption

Xuanwu uses **AES-256-GCM** encryption for all sensitive data at rest:

1. **Encryption Key**: Set `XUANWU_ENCRYPTION_KEY` environment variable (base64-encoded 32-byte key)
   ```bash
   # Generate a secure key
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```

2. **Encrypted Fields**:
   - LLM API keys (`model_token_configs` table)
   - Service provider configurations (`service_provider_configs` table)
   - Channel connection credentials (`channels` table)

3. **Config File Encryption**: Sensitive values in `xuanwu.json` can use `enc:` prefix:
   ```json
   {
     "model": {
       "tokens": {
         "my-token": {
           "api_key": "enc:v1:default:AbCdEfGh..."
         }
       }
     }
   }
   ```

### General Security

1. Change all default passwords in `xuanwu.json` and `docker-compose.yml`
2. Restrict file permissions: `chmod 600 config/xuanwu.json`
3. Use HTTPS in production (place a reverse proxy in front)
4. Regularly backup data directory and database
5. Never commit encryption keys or API keys to version control

---

For detailed configuration options, refer to `xuanwu.json.example` in the source repository.
