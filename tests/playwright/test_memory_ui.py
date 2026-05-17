# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from urllib.parse import quote

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect
sync_playwright = sync_api.sync_playwright
PlaywrightTimeoutError = sync_api.TimeoutError


BASE_URL = (os.environ.get("ATLASCLAW_PLAYWRIGHT_BASE_URL") or "").rstrip("/")
USERNAME = os.environ.get("ATLASCLAW_PLAYWRIGHT_USER", "admin")
PASSWORD = os.environ.get("ATLASCLAW_PLAYWRIGHT_PASSWORD", "admin")
ARTIFACT_DIR = Path(os.environ.get("ATLASCLAW_PLAYWRIGHT_ARTIFACT_DIR", "test-results/playwright-memory"))

pytestmark = pytest.mark.skipif(
    not BASE_URL,
    reason="Set ATLASCLAW_PLAYWRIGHT_BASE_URL to run AtlasClaw memory UI tests",
)


def _url(path: str) -> str:
    """Build an absolute URL for the configured AtlasClaw Playwright server."""
    normalized = path if path.startswith("/") else f"/{path}"
    return f"{BASE_URL}{normalized}"


@pytest.fixture
def browser_page():
    """Open a fresh Chromium page and collect browser-side runtime errors."""
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 960})
        page = context.new_page()
        console_errors: list[str] = []

        def record_console_error(msg) -> None:
            text = msg.text
            if msg.type != "error":
                return
            if "401 (Unauthorized)" in text:
                return
            console_errors.append(text)

        page.on("console", record_console_error)
        page.on("pageerror", lambda exc: console_errors.append(str(exc)))
        yield page, console_errors
        context.close()
        browser.close()


def _login(page, redirect_path: str = "/admin/roles") -> None:
    """Log into the local AtlasClaw UI if the browser context is not authenticated."""
    page.goto(_url(f"/login.html?redirect={quote(redirect_path)}"))
    page.wait_for_load_state("networkidle")
    try:
        page.locator("#username").wait_for(state="visible", timeout=2500)
    except PlaywrightTimeoutError:
        page.goto(_url(redirect_path))
        page.wait_for_load_state("networkidle")
        return
    page.locator("#username").fill(USERNAME)
    page.locator("#password").fill(PASSWORD)
    page.locator("#submitBtn").click()
    page.wait_for_function(
        "() => window.sessionStorage && window.sessionStorage.getItem('atlasclaw_auth_token')",
        timeout=10000,
    )
    page.wait_for_load_state("networkidle")
    if not page.url.rstrip("/").endswith(redirect_path.rstrip("/")):
        page.goto(_url(redirect_path))
        page.wait_for_load_state("networkidle")


def _open_roles_and_search_memory(page) -> None:
    """Open role management and filter the skill list to memory permissions."""
    _login(page, "/admin/roles")
    if "/admin/roles" not in page.url:
        page.goto(_url("/admin/roles"))
        page.wait_for_load_state("networkidle")
    expect(page.locator("#skillsSearchInput")).to_be_visible(timeout=15000)
    page.locator("#skillsSearchInput").fill("memory")
    page.wait_for_load_state("networkidle")


def _toggle_skill_enabled(page, skill_id: str) -> None:
    """Click the visible custom switch for a skill row while asserting the input changes."""
    toggle = page.locator(f'[data-skill-id="{skill_id}"]')
    toggle.evaluate("el => el.closest('.role-skill-card').scrollIntoView({ block: 'center' })")
    label = toggle.locator("xpath=ancestor::label[contains(@class, 'role-inline-toggle')]")
    label.click()
    expect(toggle).to_be_checked(timeout=5000)


@pytest.mark.parametrize(
    ("viewport", "screenshot_name"),
    [
        ({"width": 1440, "height": 960}, "roles-memory-desktop.png"),
        ({"width": 390, "height": 844}, "roles-memory-mobile.png"),
    ],
)
def test_role_management_memory_permission_search_is_readable(
    browser_page,
    viewport,
    screenshot_name: str,
) -> None:
    page, console_errors = browser_page
    page.set_viewport_size(viewport)

    _open_roles_and_search_memory(page)

    body = page.locator("body")
    expect(body).to_contain_text(re.compile(r"memory", re.IGNORECASE), timeout=10000)
    expect(body).to_contain_text(re.compile(r"memory_search|memory_get|group:memory", re.IGNORECASE))
    page.screenshot(path=str(ARTIFACT_DIR / screenshot_name), full_page=True)
    assert console_errors == []


def _memory_role_permissions(enabled: bool) -> dict:
    """Return a minimal role permission payload containing the memory group."""
    return {
        "skills": {
            "module_permissions": {
                "view": True,
                "enable_disable": True,
                "manage_permissions": True,
            },
            "allow_all": False,
            "skill_permissions": [
                {
                    "skill_id": "group:memory",
                    "skill_name": "group:memory",
                    "authorized": enabled,
                    "enabled": enabled,
                }
            ],
        },
        "providers": {
            "module_permissions": {"manage_permissions": False},
            "allow_all": False,
            "provider_permissions": [],
        },
        "channels": {
            "module_permissions": {"manage_permissions": False},
            "allow_all": False,
            "channel_permissions": [],
        },
    }


def _create_test_role(page, *, enabled: bool) -> dict:
    """Create a throwaway role for mutation-oriented memory UI checks."""
    suffix = int(time.time() * 1000)
    payload = {
        "name": f"Memory UI {suffix}",
        "identifier": f"memory-ui-{suffix}",
        "description": "Temporary Playwright memory permission role.",
        "is_active": True,
        "permissions": _memory_role_permissions(enabled),
    }
    response = page.request.post(_url("/api/roles"), data=payload)
    if response.status >= 400:
        pytest.skip(f"Cannot create temporary role for memory UI test: HTTP {response.status}")
    return response.json()


def _delete_test_role(page, role_id: str) -> None:
    """Best-effort cleanup for temporary role records created by Playwright."""
    if role_id:
        page.request.delete(_url(f"/api/roles/{role_id}"))


@pytest.mark.skipif(
    os.environ.get("ATLASCLAW_PLAYWRIGHT_MUTATE_ROLES") != "1",
    reason="Set ATLASCLAW_PLAYWRIGHT_MUTATE_ROLES=1 to run role persistence mutation test",
)
def test_memory_permission_toggle_persists_after_save_and_reload(browser_page) -> None:
    page, console_errors = browser_page
    _login(page, "/admin/roles")
    role = _create_test_role(page, enabled=False)

    try:
        page.goto(_url("/admin/roles"))
        page.wait_for_load_state("networkidle")
        page.locator("#roleSearchInput").fill(role["identifier"])
        page.locator(f'[data-role-select="{role["id"]}"]').click()
        page.locator("#skillsSearchInput").fill("memory")

        memory_toggle = page.locator('[data-skill-id="group:memory"]')
        expect(memory_toggle).to_be_visible(timeout=10000)
        expect(memory_toggle).not_to_be_checked()
        _toggle_skill_enabled(page, "group:memory")

        with page.expect_response(lambda resp: "/api/roles/" in resp.url and resp.request.method == "PUT"):
            page.locator("#saveRoleChanges").click()
        page.reload()
        page.wait_for_load_state("networkidle")
        page.locator("#roleSearchInput").fill(role["identifier"])
        page.locator(f'[data-role-select="{role["id"]}"]').click()
        page.locator("#skillsSearchInput").fill("memory")
        expect(page.locator('[data-skill-id="group:memory"]')).to_be_checked(timeout=10000)
        page.screenshot(path=str(ARTIFACT_DIR / "roles-memory-toggle-persisted.png"), full_page=True)
        assert console_errors == []
    finally:
        _delete_test_role(page, role.get("id", ""))


def _create_test_user(page, *, username: str, role_identifier: str) -> dict:
    """Create a local throwaway user assigned to one temporary role."""
    response = page.request.post(
        _url("/api/users"),
        data={
            "username": username,
            "display_name": username,
            "email": f"{username}@example.invalid",
            "password": PASSWORD,
            "roles": {role_identifier: True},
            "auth_type": "local",
            "is_active": True,
        },
    )
    if response.status >= 400:
        pytest.skip(f"Cannot create temporary user for memory UI test: HTTP {response.status}")
    return response.json()


def _delete_test_user(page, user_id: str) -> None:
    """Best-effort cleanup for temporary local users created by Playwright."""
    if user_id:
        page.request.delete(_url(f"/api/users/{user_id}"))


def _new_logged_in_page(browser, username: str):
    """Create a separate context and log it in as a temporary user."""
    context = browser.new_context(viewport={"width": 1280, "height": 860})
    page = context.new_page()
    page.goto(_url("/login.html?redirect=/"))
    page.wait_for_load_state("networkidle")
    page.locator("#username").fill(username)
    page.locator("#password").fill(PASSWORD)
    page.locator("#submitBtn").click()
    page.wait_for_function(
        "() => window.sessionStorage && window.sessionStorage.getItem('atlasclaw_auth_token')",
        timeout=10000,
    )
    page.wait_for_load_state("networkidle")
    return context, page


@pytest.mark.skipif(
    os.environ.get("ATLASCLAW_PLAYWRIGHT_MEMORY_RBAC") != "1",
    reason="Set ATLASCLAW_PLAYWRIGHT_MEMORY_RBAC=1 to run multi-user memory RBAC tests",
)
def test_memory_write_api_denied_for_role_without_memory_permission(browser_page) -> None:
    admin_page, _console_errors = browser_page
    _login(admin_page, "/admin/roles")
    role = _create_test_role(admin_page, enabled=False)
    username = f"mem-denied-{int(time.time() * 1000)}"
    user = _create_test_user(admin_page, username=username, role_identifier=role["identifier"])

    try:
        browser = admin_page.context.browser
        user_context, user_page = _new_logged_in_page(browser, username)
        try:
            response = user_page.request.post(
                _url("/api/memory/write"),
                data={"content": "should not write", "memory_type": "daily"},
            )
            assert response.status == 403
        finally:
            user_context.close()
    finally:
        _delete_test_user(admin_page, user.get("id", ""))
        _delete_test_role(admin_page, role.get("id", ""))


@pytest.mark.skipif(
    os.environ.get("ATLASCLAW_PLAYWRIGHT_MEMORY_RBAC") != "1",
    reason="Set ATLASCLAW_PLAYWRIGHT_MEMORY_RBAC=1 to run multi-user memory RBAC tests",
)
def test_enabled_memory_user_can_write_and_recall_preference(browser_page) -> None:
    admin_page, _console_errors = browser_page
    _login(admin_page, "/admin/roles")
    role = _create_test_role(admin_page, enabled=True)
    username = f"mem-enabled-{int(time.time() * 1000)}"
    user = _create_test_user(admin_page, username=username, role_identifier=role["identifier"])

    try:
        browser = admin_page.context.browser
        user_context, user_page = _new_logged_in_page(browser, username)
        try:
            write = user_page.request.post(
                _url("/api/memory/write"),
                data={
                    "content": f"{username} prefers concise Chinese replies.",
                    "memory_type": "long_term",
                    "section": "Preferences",
                },
            )
            assert write.status == 200
            search = user_page.request.post(
                _url("/api/memory/search"),
                data={"query": "concise Chinese replies", "top_k": 3},
            )
            assert search.status == 200
            assert any(username in item["snippet"] for item in search.json()["results"])
        finally:
            user_context.close()
    finally:
        _delete_test_user(admin_page, user.get("id", ""))
        _delete_test_role(admin_page, role.get("id", ""))


@pytest.mark.skipif(
    os.environ.get("ATLASCLAW_PLAYWRIGHT_MEMORY_RBAC") != "1",
    reason="Set ATLASCLAW_PLAYWRIGHT_MEMORY_RBAC=1 to run multi-user memory RBAC tests",
)
def test_second_user_cannot_search_first_user_memory(browser_page) -> None:
    admin_page, _console_errors = browser_page
    _login(admin_page, "/admin/roles")
    role = _create_test_role(admin_page, enabled=True)
    suffix = int(time.time() * 1000)
    user_one = _create_test_user(
        admin_page,
        username=f"mem-one-{suffix}",
        role_identifier=role["identifier"],
    )
    user_two = _create_test_user(
        admin_page,
        username=f"mem-two-{suffix}",
        role_identifier=role["identifier"],
    )

    try:
        browser = admin_page.context.browser
        context_one, page_one = _new_logged_in_page(browser, user_one["username"])
        context_two, page_two = _new_logged_in_page(browser, user_two["username"])
        try:
            marker = f"private preference marker {suffix}"
            assert page_one.request.post(
                _url("/api/memory/write"),
                data={"content": marker, "memory_type": "long_term"},
            ).status == 200
            search = page_two.request.post(
                _url("/api/memory/search"),
                data={"query": marker, "top_k": 5},
            )
            assert search.status == 200
            assert search.json()["results"] == []
        finally:
            context_one.close()
            context_two.close()
    finally:
        _delete_test_user(admin_page, user_one.get("id", ""))
        _delete_test_user(admin_page, user_two.get("id", ""))
        _delete_test_role(admin_page, role.get("id", ""))
