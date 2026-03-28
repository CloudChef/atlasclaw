# Xuanwu Build

This directory contains build scripts and configurations for both OpenSource and Enterprise editions of Xuanwu.

## Container Directory Structure

Xuanwu container uses the following internal directory structure:

```
/app/
├── workspace/          # Configuration, user data, logs, agents
│   ├── xuanwu.json  # Main configuration file
│   └── logs/           # Application logs
├── data/               # Database files
└── extensions/         # Custom extensions
    ├── providers/      # Service provider integrations
    ├── skills/         # Custom skills
    └── channels/       # Channel integrations
```

**Host Mount Points:**
- Host `/opt/xuanwu/workspace` → Container `/app/workspace`
- Host `/opt/xuanwu/data` → Container `/app/data`
- Host `/opt/xuanwu/extensions/providers` → Container `/app/extensions/providers`
- Host `/opt/xuanwu/extensions/skills` → Container `/app/extensions/skills`
- Host `/opt/xuanwu/extensions/channels` → Container `/app/extensions/channels`

## Quick Comparison

| Feature | OpenSource | Enterprise |
|---------|------------|------------|
| Database | SQLite (built-in) | MySQL 8.4 |
| Deployment | Single container | Multi-container |
| Best for | Development / Small teams | Production / Large organizations |
| Resources | Minimal | Configurable limits |
| High Availability | No | Yes (with external MySQL) |
| Base Image | python:3.11-slim | python:3.11-slim |

## Files

### Dockerfiles

| File | Description |
|------|-------------|
| `Dockerfile.opensource` | Lightweight build with SQLite |
| `Dockerfile.enterprise` | Multi-stage build with MySQL support |

### Compose Files

| File | Description |
|------|-------------|
| `docker-compose.yml` | Single Xuanwu container |
| `docker-compose.enterprise.yml` | Xuanwu + MySQL 8.5 + Secrets |

### Scripts

| File | Description |
|------|-------------|
| `build.sh` | Automated build script with mode selection |

## Usage

### Build Options

```bash
./build.sh --mode opensource|enterprise [--tag VERSION] [--push] [--username USER] [--password PASS]
```

| Option | Description |
|--------|-------------|
| `--mode opensource` | Build OpenSource edition (image: `xuanwu`) |
| `--mode enterprise` | Build Enterprise edition (image: `xuanwu-official`) |
| `--tag, -t` | Version tag (default: `v0.6.1`) |
| `--push` | Push image to registry after build |
| `--username, -u` | Registry username (required with --push) |
| `--password, -p` | Registry password (required with --push) |
| `--registry` | Custom registry URL (default: `registry.cn-shanghai.aliyuncs.com`) |
| `--namespace` | Custom namespace (default: `xuanwu`) |

### Default Registry

By default, images are tagged for Aliyun Container Registry (ACR) Shanghai:
- **Registry**: `registry.cn-shanghai.aliyuncs.com`
- **Namespace**: `xuanwu`
- **Full image path**: `registry.cn-shanghai.aliyuncs.com/xuanwu/xuanwu:{tag}`

### OpenSource Edition

```bash
# Build locally
./build.sh --mode opensource --tag v0.6.1

# Build and push to ACR
./build.sh --mode opensource --tag v0.6.1 --push --username your-user --password your-pass

# Creates image: registry.cn-shanghai.aliyuncs.com/xuanwu/xuanwu:v0.6.1
```

**Features:**
- Image name: `xuanwu`
- Single Docker container
- SQLite database (auto-created)
- Minimal resource usage
- Quick start

**Deploy:**
```bash
cd build

# Create host directories
mkdir -p /opt/xuanwu/{workspace,data,extensions/{providers,skills,channels}}

# Copy config to workspace
cp config/xuanwu.json /opt/xuanwu/workspace/

# Start container
docker-compose up -d
```

### Enterprise Edition

```bash
# Build locally
./build.sh --mode enterprise --tag v0.6.1

# Build and push to ACR
./build.sh --mode enterprise --tag v0.6.1 --push --username your-user --password your-pass

# Creates image: registry.cn-shanghai.aliyuncs.com/xuanwu/xuanwu-official:v0.6.1
```

**Features:**
- Image name: `xuanwu-official`
- MySQL 8.4 LTS database
- Docker secrets for passwords
- Resource limits (4 CPU / 8GB RAM)
- Health checks
- Persistent volumes

**Deploy:**
```bash
cd build

# Create host directories
mkdir -p /opt/xuanwu/{workspace,data,extensions/{providers,skills,channels}}

# Copy config to workspace
cp config/xuanwu.json /opt/xuanwu/workspace/

# Start container
docker-compose up -d

# Run database migrations
docker-compose exec xuanwu alembic upgrade head
```

## Build Script

The `build.sh` script automates:

1. **Prerequisites check** - Docker
2. **Configuration generation** - Creates `xuanwu.json` with correct paths
3. **Secret generation** (Enterprise) - Auto-generates MySQL passwords
4. **Docker build** - Builds the appropriate image with tags
5. **Image push** (optional) - Pushes to registry with credentials
6. **Cleanup** - Removes temporary files

### Generated Files

After running build script:

```
build/
├── config/
│   └── xuanwu.json          # Main configuration (with /app paths)
├── secrets/                    # Enterprise only
│   ├── mysql_root_password.txt
│   └── mysql_password.txt
└── docker-compose.yml -> docker-compose.{mode}.yml
```

**Note:** Data directories are created on the host at `/opt/xuanwu/` during first deployment.

## Configuration

### OpenSource

Edit `config/xuanwu.json`:

```json
{
  "database": {
    "type": "sqlite",
    "sqlite": {
      "path": "./data/xuanwu.db"
    }
  }
}
```

### Enterprise

Edit `config/xuanwu.json`:

```json
{
  "database": {
    "type": "mysql",
    "mysql": {
      "host": "mysql",
      "port": 3306,
      "database": "xuanwu",
      "user": "xuanwu",
      "password": "auto-generated",
      "charset": "utf8mb4"
    }
  }
}
```

**Passwords are auto-generated in `secrets/` directory.**

## Operations

### View Logs

```bash
docker-compose logs -f xuanwu
docker-compose logs -f mysql    # Enterprise only
```

### Stop

```bash
docker-compose down
```

### Backup

**OpenSource:**
```bash
tar -czf backup.tar.gz config/ data/ logs/
```

**Enterprise:**
```bash
# Backup database
docker exec xuanwu-mysql mysqldump -u root -p xuanwu > db_backup.sql

# Backup files
tar -czf backup.tar.gz config/ data/ logs/
```

## Troubleshooting

### Port Already in Use

Edit `docker-compose.yml`:

```yaml
ports:
  - "8080:8000"
```

### Permission Denied

```bash
chmod 600 config/xuanwu.json
chmod 600 secrets/*.txt  # Enterprise
```

### Build Failures

```bash
# Clear Docker cache
docker builder prune

# Retry build
./build.sh --mode {opensource|enterprise}
```
