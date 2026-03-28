"""

web_fetch tool

content.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, TYPE_CHECKING
from urllib.parse import urlsplit


from app.xuanwu.tools.base import ToolResult
from app.xuanwu.tools.truncation import truncate_output, TruncationConfig

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps


logger = logging.getLogger(__name__)


def _mask_proxy_url(proxy_url: str) -> str:
    if not proxy_url:
        return ""
    try:
        parsed = urlsplit(proxy_url)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        auth = "***@" if parsed.username or parsed.password else ""
        return f"{parsed.scheme}://{auth}{host}{port}"
    except Exception:
        return "***"


def _proxy_debug_info() -> dict[str, str]:
    proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "NO_PROXY", "no_proxy"]
    result: dict[str, str] = {}
    for key in proxy_keys:
        value = os.getenv(key)
        if not value:
            continue
        if key.lower() == "no_proxy":
            result[key] = value
        else:
            result[key] = _mask_proxy_url(value)
    return result


async def web_fetch_tool(

    ctx: "RunContext[SkillDeps]",
    url: str,
    extract_mode: str = "text",
) -> dict:
    """

content

    Args:
        ctx:PydanticAI RunContext dependency injection
        url:URL
        extract_mode:mode(text / markdown / html)

    Returns:
        Serialized `ToolResult` dictionary
    
"""
    try:
        import httpx
    except ImportError:
        return ToolResult.error("httpx is not installed").to_dict()

    parsed = urlsplit(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0, trust_env=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            html = response.text
    except Exception as e:
        proxy_info = _proxy_debug_info()
        logger.warning(
            "Web fetch request failed | base_url=%s url=%s extract_mode=%s proxy=%s error_type=%s error=%r",
            base_url,
            url,
            extract_mode,
            proxy_info,
            type(e).__name__,
            e,
            exc_info=True,
        )
        return ToolResult.error(
            f"Invalid URL or connection error: {type(e).__name__}: {e!r}",
            details={
                "url": url,
                "base_url": base_url,
                "extract_mode": extract_mode,
                "proxy": proxy_info,
                "error_type": type(e).__name__,
            },
        ).to_dict()


    # mode
    if extract_mode == "html":
        content = html
    elif extract_mode == "markdown":
        content = _html_to_markdown(html)
    else:
        content = _html_to_text(html)

    # truncate
    original_len = len(content)
    content = truncate_output(content, TruncationConfig())
    truncated = len(content) < original_len

    return ToolResult.text(
        content,
        details={
            "url": url,
            "base_url": base_url,
            "extract_mode": extract_mode,
            "truncated": truncated,
            "status_code": response.status_code,
        },
    ).to_dict()



def _html_to_text(html: str) -> str:
    """HTML"""
    import re
    # script and style
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # 
    text = re.sub(r"<[^>]+>", " ", text)
    # 
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _html_to_markdown(html: str) -> str:
    """HTML Markdown"""
    import re
    text = html
    # h1-h6
    for i in range(1, 7):
        text = re.sub(
            rf"<h{i}[^>]*>(.*?)</h{i}>",
            lambda m, level=i: f"\n{'#' * level} {m.group(1).strip()}\n",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )
    # p -> paragraph
    text = re.sub(r"<p[^>]*>(.*?)</p>", r"\n\1\n", text, flags=re.DOTALL | re.IGNORECASE)
    # 
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text
