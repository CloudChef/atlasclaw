# AtlasClaw OpenSource Edition
# Lightweight build with SQLite for single-node deployment

FROM python:3.11-slim-bookworm

# Prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install runtime dependencies (curl, wget, and Node.js for skills)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    wget \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY app/ ./app/
COPY migrations/ ./migrations/
COPY alembic.ini .

# Create necessary directories (all under /app in container)
# - /app/workspace: user data, config, logs
# - /app/data: database files
# - /app/extensions: custom providers, skills, channels
RUN mkdir -p /app/workspace /app/data /app/workspace/logs \
    /app/extensions/providers /app/extensions/skills /app/extensions/channels

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

# Start server (SQLite auto-creates on startup)
CMD ["uvicorn", "app.atlasclaw.main:app", "--host", "0.0.0.0", "--port", "8000"]
