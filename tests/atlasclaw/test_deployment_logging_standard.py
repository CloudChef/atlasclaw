# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_compose_services_use_docker_log_driver_with_rotation() -> None:
    """Container deployments keep AtlasClaw logs in Docker-managed stdout/stderr storage."""
    for relative_path in ("build/docker-compose.yml", "build/docker-compose.enterprise.yml"):
        compose_source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")

        assert "logging:" in compose_source
        assert "driver: json-file" in compose_source
        assert 'max-size: "100m"' in compose_source
        assert 'max-file: "10"' in compose_source
        assert "/app/workspace/logs:/app/workspace/logs" not in compose_source


def test_systemd_templates_standardize_journald_and_file_log_modes() -> None:
    """VM deployments use journald by default and a fixed drop-in for file logging."""
    service_source = (PROJECT_ROOT / "build/systemd/atlasclaw.service").read_text(
        encoding="utf-8"
    )
    file_log_source = (PROJECT_ROOT / "build/systemd/atlasclaw-file-log.conf").read_text(
        encoding="utf-8"
    )

    assert "StandardOutput=journal" in service_source
    assert "StandardError=journal" in service_source
    assert "StandardOutput=append:/opt/atlasclaw/logs/atlasclaw.log" in file_log_source
    assert "StandardError=append:/opt/atlasclaw/logs/atlasclaw.log" in file_log_source


def test_vm_file_log_rotation_matches_container_retention_shape() -> None:
    """VM file logs rotate at 100 MB and keep 10 files, matching container log limits."""
    logrotate_source = (PROJECT_ROOT / "build/systemd/atlasclaw.logrotate").read_text(
        encoding="utf-8"
    )
    timer_source = (PROJECT_ROOT / "build/systemd/atlasclaw-logrotate.timer").read_text(
        encoding="utf-8"
    )
    service_source = (
        PROJECT_ROOT / "build/systemd/atlasclaw-logrotate.service"
    ).read_text(encoding="utf-8")

    assert "/opt/atlasclaw/logs/*.log" in logrotate_source
    assert "size 100M" in logrotate_source
    assert "rotate 10" in logrotate_source
    assert "copytruncate" in logrotate_source
    assert "daily" not in logrotate_source
    assert "rotate 14" not in logrotate_source
    assert "OnUnitActiveSec=1min" in timer_source
    assert "ExecStart=/usr/sbin/logrotate /etc/logrotate.d/atlasclaw" in service_source


def test_deployment_docs_name_standard_log_locations() -> None:
    """Deployment docs must point operators to the standard container and VM log surfaces."""
    doc_sources = [
        (PROJECT_ROOT / "build/README.md").read_text(encoding="utf-8"),
        (PROJECT_ROOT / "build/README_ENT.md").read_text(encoding="utf-8"),
        (PROJECT_ROOT / "docs/DEPLOYMENT.md").read_text(encoding="utf-8"),
        (PROJECT_ROOT / "docs/DEVELOPMENT-SPEC.MD").read_text(encoding="utf-8"),
    ]
    joined_docs = "\n".join(doc_sources)

    assert "docker compose logs -f atlasclaw" in joined_docs
    assert "journalctl -u atlasclaw -f" in joined_docs
    assert "/opt/atlasclaw/logs/atlasclaw.log" in joined_docs
    assert "size 100M" in joined_docs
    assert "rotate 10" in joined_docs
    assert "atlasclaw-logrotate.timer" in joined_docs
    assert "workspace/logs/` directory is only used" in joined_docs


def test_deployment_commands_use_compose_v2_binary() -> None:
    """Deployment commands use the Docker Compose v2 plugin syntax."""
    checked_paths = [
        "build/DOCKER_BUILD_GUIDE.md",
        "docs/DEPLOYMENT.md",
        "docs/QUICKSTART-SMARTCMP.md",
        "build/remote-deploy.sh",
    ]
    legacy_command = re.compile(r"\bdocker-compose\s+(up|down|exec|logs|ps|pull|restart)\b")

    for relative_path in checked_paths:
        source = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        assert not legacy_command.search(source), relative_path
