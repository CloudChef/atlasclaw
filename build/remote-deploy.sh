#!/bin/bash
# Xuanwu remote deploy helper
# Usage: ./remote-deploy.sh root@192.168.16.21 /opt/xuanwu

set -e

REMOTE_HOST="$1"
REMOTE_DIR="${2:-/opt/xuanwu}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR"

echo "[deploy] Syncing code to $REMOTE_HOST..."
rsync -avz --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
    -e "ssh $SSH_OPTS" \
    . "$REMOTE_HOST:$REMOTE_DIR/"

echo "[deploy] Building on remote..."
ssh $SSH_OPTS "$REMOTE_HOST" "cd $REMOTE_DIR/build && ./build.sh --mode opensource"

echo "[deploy] Starting services..."
ssh $SSH_OPTS "$REMOTE_HOST" "cd $REMOTE_DIR/build && docker-compose down && docker-compose up -d"

echo "[deploy] Done!"