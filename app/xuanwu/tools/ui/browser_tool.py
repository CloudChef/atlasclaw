"""Browser automation tool powered by Playwright.

This module provides a lightweight browser manager and a single tool entry
point that supports common actions such as navigation, screenshots, clicks,
typing, evaluation, and DOM inspection during an agent run.
"""

from __future__ import annotations

import os
import tempfile
import time
from typing import Optional, Any, TYPE_CHECKING

from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


class BrowserManager:
    """
    Manage a lazily initialized browser instance for the current runtime.
    """

    def __init__(self, headless: bool = True) -> None:
        self._headless = headless
        self._playwright: Any = None
        self._browser: Any = None
        self._page: Any = None

    async def ensure_page(self) -> Any:
        """Return an active Playwright page, creating it on first use."""
        if self._page is not None:
            return self._page

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "playwright is not installed. Run: pip install playwright && playwright install chromium"
            )

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        self._page = await self._browser.new_page()
        return self._page

    async def cleanup(self) -> None:
        """Close any active page, browser, and Playwright runtime objects."""
        if self._page:
            try:
                await self._page.close()
            except Exception:
                pass
            self._page = None

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    @property
    def is_active(self) -> bool:
        return self._page is not None


# Module-level singleton used by the default browser tool entry point.
_default_browser_manager: Optional[BrowserManager] = None


def get_browser_manager(headless: bool = True) -> BrowserManager:
    """Return the default browser manager singleton."""
    global _default_browser_manager
    if _default_browser_manager is None:
        _default_browser_manager = BrowserManager(headless=headless)
    return _default_browser_manager


async def browser_tool(
    ctx: "RunContext[SkillDeps]",
    action: str,
    url: Optional[str] = None,
    selector: Optional[str] = None,
    text: Optional[str] = None,
    script: Optional[str] = None,
    timeout_ms: int = 30000,
) -> dict:
    """
    Dispatch a browser automation action.

    Args:
        ctx: PydanticAI `RunContext` dependency injection payload.
        action: Action name such as `navigate`, `click`, or `screenshot`.
        url: Target URL for `navigate`.
        selector: CSS or XPath selector used by DOM-oriented actions.
        text: Input text for `type` or attribute name for `get_attribute`.
        script: JavaScript source for `evaluate`.
        timeout_ms: Timeout in milliseconds for browser operations.

    Returns:
        Serialized `ToolResult` dictionary.
    """
    start = time.monotonic()
    manager = get_browser_manager()

    try:
        page = await manager.ensure_page()
    except RuntimeError as e:
        return ToolResult.error(str(e)).to_dict()

    try:
        result = await _dispatch_action(
            page, action, url=url, selector=selector,
            text=text, script=script, timeout_ms=timeout_ms,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        result.details["durationMs"] = duration_ms
        result.details["action"] = action
        return result.to_dict()
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolResult.error(
            str(e),
            details={"action": action, "durationMs": duration_ms, "status": "failed"},
        ).to_dict()


async def _dispatch_action(
    page: Any,
    action: str,
    *,
    url: Optional[str],
    selector: Optional[str],
    text: Optional[str],
    script: Optional[str],
    timeout_ms: int,
) -> ToolResult:
    """Execute a specific browser action on the active page."""

    if action == "navigate":
        if not url:
            return ToolResult.error("url is required for navigate action")
        await page.goto(url, timeout=timeout_ms)
        title = await page.title()
        return ToolResult.text(
            f"Navigated to: {url}",
            details={"status": "completed", "title": title, "url": url},
        )

    if action == "screenshot":
        fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="xuanwu_ss_")
        os.close(fd)
        if selector:
            element = await page.query_selector(selector)
            if element:
                await element.screenshot(path=tmp_path)
            else:
                return ToolResult.error(f"Element not found: {selector}")
        else:
            await page.screenshot(path=tmp_path, full_page=True)
        return ToolResult(
            content=[{"type": "image", "url": tmp_path}],
            details={"status": "completed", "path": tmp_path},
        )

    if action == "click":
        if not selector:
            return ToolResult.error("selector is required for click action")
        await page.click(selector, timeout=timeout_ms)
        return ToolResult.text(
            f"Clicked: {selector}",
            details={"status": "completed", "clicked": True, "selector": selector},
        )

    if action == "type":
        if not selector:
            return ToolResult.error("selector is required for type action")
        if text is None:
            return ToolResult.error("text is required for type action")
        await page.fill(selector, text, timeout=timeout_ms)
        return ToolResult.text(
            f"Typed into: {selector}",
            details={"status": "completed", "selector": selector},
        )

    if action == "evaluate":
        if not script:
            return ToolResult.error("script is required for evaluate action")
        eval_result = await page.evaluate(script)
        return ToolResult.text(
            str(eval_result),
            details={"status": "completed"},
        )

    if action == "get_text":
        if not selector:
            return ToolResult.error("selector is required for get_text action")
        element = await page.query_selector(selector)
        if not element:
            return ToolResult.error(f"Element not found: {selector}")
        element_text = await element.text_content()
        return ToolResult.text(
            element_text or "",
            details={"status": "completed", "selector": selector},
        )

    if action == "get_attribute":
        if not selector:
            return ToolResult.error("selector is required for get_attribute action")
        if not text:
            return ToolResult.error("text (attribute name) is required for get_attribute action")
        element = await page.query_selector(selector)
        if not element:
            return ToolResult.error(f"Element not found: {selector}")
        attr_val = await element.get_attribute(text)
        return ToolResult.text(
            attr_val or "",
            details={"status": "completed", "selector": selector, "attribute": text},
        )

    if action == "wait_for":
        if not selector:
            return ToolResult.error("selector is required for wait_for action")
        try:
            await page.wait_for_selector(selector, timeout=timeout_ms)
            return ToolResult.text(
                f"Element appeared: {selector}",
                details={"status": "completed", "selector": selector},
            )
        except Exception:
            return ToolResult.error(
                f"Timeout waiting for: {selector}",
                details={"status": "timeout", "selector": selector},
            )

    if action == "scroll":
        direction = text or "down"
        if direction == "down":
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
        elif direction == "up":
            await page.evaluate("window.scrollBy(0, -window.innerHeight)")
        elif direction == "top":
            await page.evaluate("window.scrollTo(0, 0)")
        elif direction == "bottom":
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        return ToolResult.text(
            f"Scrolled: {direction}",
            details={"status": "completed", "direction": direction},
        )

    return ToolResult.error(f"Unknown action: {action}")
