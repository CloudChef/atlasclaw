# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.atlasclaw.tools.web import fetch_tool as fetch_tool_module
from app.atlasclaw.tools.web.fetch_tool import (
    _guarded_get_text_with_proxy_fallback,
    _html_to_text,
    fetch_web_content,
    web_fetch_tool,
)


@pytest.mark.asyncio
async def test_web_fetch_tool_returns_content_and_details(monkeypatch) -> None:
    async def _fake_fetch(url: str, *, extract_mode: str = "text", **kwargs):
        _ = kwargs
        return "上海明天多云，12℃-19℃。", {
            "url": url,
            "extract_mode": extract_mode,
            "status_code": 200,
            "truncated": False,
        }

    monkeypatch.setattr("app.atlasclaw.tools.web.fetch_tool.fetch_web_content", _fake_fetch)

    result = await web_fetch_tool(
        ctx=SimpleNamespace(),
        url="https://www.weather.com.cn/weather/101020100.shtml",
        extract_mode="text",
    )

    assert result["is_error"] is False
    assert "12℃-19℃" in result["content"][0]["text"]
    assert result["details"]["status_code"] == 200
    assert result["details"]["extract_mode"] == "text"


@pytest.mark.asyncio
async def test_web_fetch_tool_returns_error_payload_when_fetch_fails(monkeypatch) -> None:
    async def _failing_fetch(url: str, *, extract_mode: str = "text", **kwargs):
        _ = kwargs
        raise RuntimeError("connection timeout")

    monkeypatch.setattr("app.atlasclaw.tools.web.fetch_tool.fetch_web_content", _failing_fetch)

    result = await web_fetch_tool(
        ctx=SimpleNamespace(),
        url="https://www.weather.com.cn/weather/101020100.shtml",
        extract_mode="markdown",
    )

    assert result["is_error"] is True
    assert "connection timeout" in result["content"][0]["text"]
    assert result["details"]["url"] == "https://www.weather.com.cn/weather/101020100.shtml"
    assert result["details"]["extract_mode"] == "markdown"


def test_html_to_text_prefers_readable_blocks_over_navigation() -> None:
    pytest.importorskip("bs4")

    html = """
    <html>
      <body>
        <nav>
          <a href="/">Home</a>
          <a href="/radar">Radar</a>
          <a href="/map">Map</a>
          <a href="/city">Cities</a>
        </nav>
        <main>
          <section>
            <h1>Shanghai Forecast</h1>
            <p>Apr 2: Cloudy to light rain, 23C / 14C, east wind under level 3.</p>
            <p>Apr 3: Moderate rain to overcast, 16C / 13C.</p>
          </section>
        </main>
      </body>
    </html>
    """
    text = _html_to_text(html)
    assert "Shanghai Forecast" in text
    assert "Apr 2: Cloudy to light rain" in text
    assert "Radar Map Cities" not in text


@pytest.mark.asyncio
async def test_fetch_retries_without_proxy_when_proxy_path_fails(monkeypatch) -> None:
    calls: list[bool] = []

    async def _fake_fetch_with_client(**kwargs):
        calls.append(bool(kwargs["trust_env"]))
        if kwargs["trust_env"]:
            raise RuntimeError("proxy failure")
        return "ok", 200, "https://example.com", False

    monkeypatch.setenv("ATLASCLAW_WEB_USE_PROXY", "1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10792")
    monkeypatch.setattr(fetch_tool_module, "_fetch_with_client", _fake_fetch_with_client)

    payload, status, final_url, truncated, used_no_proxy = await _guarded_get_text_with_proxy_fallback(
        url="https://example.com",
        headers={"User-Agent": "AtlasClaw-Test"},
        timeout_seconds=3.0,
        max_redirects=2,
        max_response_bytes=4096,
    )

    assert payload == "ok"
    assert status == 200
    assert final_url == "https://example.com"
    assert truncated is False
    assert used_no_proxy is True
    assert calls == [True, False]


@pytest.mark.asyncio
async def test_fetch_does_not_retry_without_proxy_when_no_proxy_env(monkeypatch) -> None:
    async def _fake_fetch_with_client(**kwargs):
        _ = kwargs
        raise RuntimeError("proxy failure")

    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("ATLASCLAW_WEB_USE_PROXY", raising=False)
    monkeypatch.setattr(fetch_tool_module, "_fetch_with_client", _fake_fetch_with_client)

    with pytest.raises(RuntimeError, match="proxy failure"):
        await _guarded_get_text_with_proxy_fallback(
            url="https://example.com",
            headers={"User-Agent": "AtlasClaw-Test"},
            timeout_seconds=3.0,
            max_redirects=2,
            max_response_bytes=4096,
        )


@pytest.mark.asyncio
async def test_fetch_follows_client_side_redirect_page(monkeypatch) -> None:
    calls: list[str] = []

    async def _fake_guarded_get_text_with_proxy_fallback(**kwargs):
        calls.append(kwargs["url"])
        if len(calls) == 1:
            return (
                '<meta content="always" name="referrer">'
                '<script>window.location.replace("https://target.example/article")</script>',
                200,
                "https://www.sogou.com/link?url=abc",
                False,
                False,
            )
        return (
            "<html><body>target article content</body></html>",
            200,
            "https://target.example/article",
            False,
            False,
        )

    monkeypatch.setattr(
        fetch_tool_module,
        "_guarded_get_text_with_proxy_fallback",
        _fake_guarded_get_text_with_proxy_fallback,
    )

    content, details = await fetch_web_content(
        "https://www.sogou.com/link?url=abc",
        extract_mode="html",
        timeout_seconds=3.0,
        use_cache=False,
    )

    assert len(calls) == 2
    assert calls[1] == "https://target.example/article"
    assert "target article content" in content
    assert details["final_url"] == "https://target.example/article"
