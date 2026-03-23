# AtlasClaw Enterprise Build

This directory contains the build scripts and configuration for AtlasClaw enterprise deployment.

## Files

| File | Description |
|------|-------------|
| `Dockerfile` | Multi-stage Docker build for AtlasClaw |
| `docker-compose.yml` | Production deployment with MySQL 8.5 |
| `build.sh` | Automated build script |
| `config/` | Configuration directory (auto-generated) |
| `secrets/` | Secrets directory (auto-generated) |
| `data/` | Data persistence directory |
| `logs/` | Log files directory |
| `mysql-data/` | MySQL data directory |

## Quick Start

### 1. Build Docker Image

```bash
./build.sh [version_tag]
```

Example:
```bash
./build.sh v1.0.0
```

This script will:
- Check prerequisites (Docker, Python)
- Install and validate Python dependencies
- Generate secure passwords and configuration
- Build the Docker image `atlasclaw-enterprise:latest`

### 2. Configure

Edit `config/atlasclaw.json` to add your LLM API key and other settings.

### 3. Deploy

```bash
docker-compose up -d
```

### 4. Run Migrations

```bash
docker-compose exec atlasclaw alembic upgrade head
```

### 5. Verify

```bash
curl http://localhost:8000/api/health
```

## Build Script Details

The `build.sh` script automates the following steps:

1. **Prerequisites Check**: Verifies Docker, Docker Compose, and Python are installed
2. **Dependency Installation**: Installs Python packages from `requirements.txt` for validation
3. **Configuration Generation**: Creates `atlasclaw.json` and secure MySQL passwords
4. **File Preparation**: Copies necessary project files to build directory
5. **Docker Build**: Builds the `atlasclaw-enterprise` image
6. **Cleanup**: Removes temporary build artifacts

## Configuration

### atlasclaw.json

Main configuration file auto-generated at `config/atlasclaw.json`. You must edit this to:

- Add your LLM API key
- Configure service providers (Jira, ServiceNow, etc.)
- Set up authentication (OIDC or API key)

### Secrets

Passwords are auto-generated and stored in:

- `secrets/mysql_root_password.txt` - MySQL root password
- `secrets/mysql_password.txt` - MySQL atlasclaw user password

## Docker Compose Services

### atlasclaw

- Image: `atlasclaw-enterprise:latest`
- Port: `8000`
- Volumes: config, data, logs
- Resources: 4 CPU / 8GB RAM (limit), 2 CPU / 4GB RAM (reservation)

### mysql

- Image: `mysql:8.5`
- Port: `3306` (internal)
- Volumes: mysql-data
- Resources: 4 CPU / 4GB RAM (limit), 2 CPU / 2GB RAM (reservation)

## Operations

### View Logs

```bash
docker-compose logs -f atlasclaw
docker-compose logs -f mysql
```

### Stop Services

```bash
docker-compose down
```

### Update

```bash
# Pull latest image
docker-compose pull

# Or rebuild
./build.sh

# Restart with migrations
docker-compose up -d
docker-compose exec atlasclaw alembic upgrade head
```

### Backup

```bash
# Backup database
docker exec atlasclaw-mysql mysqldump -u root -p atlasclaw > backup.sql

# Backup data
tar -czf atlasclaw-backup.tar.gz config/ data/ logs/
```

## Troubleshooting

### Port Conflicts

If port 8000 is in use, modify `docker-compose.yml`:

```yaml
ports:
  - "8080:8000"
```

### Permission Issues

Ensure proper permissions:

```bash
chmod 600 config/atlasclaw.json
chmod 600 secrets/*.txt
```

### Build Failures

Check Docker daemon is running:

```bash
docker info
```

Clear Docker build cache if needed:

```bash
docker builder prune
```
