"""
web_search tool

Web search with support for multiple providers(`bing` / `duckduckgo` / `google`).
Fetches search engine HTML via `httpx` and parses the results.
"""

from __future__ import annotations

import os
import re
import logging
from typing import Optional, TYPE_CHECKING
from urllib.parse import quote_plus, urlsplit


from app.xuanwu.tools.base import ToolResult

if TYPE_CHECKING:
    from pydantic_ai import RunContext
    from app.xuanwu.core.deps import SkillDeps

logger = logging.getLogger(__name__)

# supportsearch Provider and
SEARCH_PROVIDERS = ["bing", "duckduckgo", "google"]


def _provider_base_url(provider: str) -> str:
    if provider == "bing":
        return "https://www.bing.com"
    if provider == "duckduckgo":
        return "https://html.duckduckgo.com"
    if provider == "google":
        return "https://www.google.com"
    return ""


def _provider_request_url(provider: str, query: str, limit: int) -> str:
    if provider == "bing":
        return f"https://www.bing.com/search?q={quote_plus(query)}&count={limit}"
    if provider == "duckduckgo":
        return f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    if provider == "google":
        return f"https://www.google.com/search?q={quote_plus(query)}&num={limit}"
    return ""


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
    proxy_keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
    ]
    return {
        key: _mask_proxy_url(value) if "proxy" in key.lower() and key.lower() != "no_proxy" else value
        for key in proxy_keys
        if (value := os.getenv(key))
    }


async def web_search_tool(

    ctx: "RunContext[SkillDeps]",
    query: str,
    provider: Optional[str] = None,
    limit: int = 10,
) -> dict:
    """

search

    Args:
        ctx:PydanticAI RunContext dependency injection
        query:search
        provider:searchprovider(bing / duckduckgo / google), default bing
        limit:multireturnitemcount

    Returns:
        Serialized `ToolResult` dictionary
    
"""
    try:
        import httpx
    except ImportError:
        return ToolResult.error("httpx is not installed").to_dict()

    used_provider = provider or "bing"

    # build Provider:>
    providers_to_try = [used_provider]
    for p in SEARCH_PROVIDERS:
        if p not in providers_to_try:
            providers_to_try.append(p)

    last_error = ""
    for prov in providers_to_try:
        try:
            results = await _search_with_provider(prov, query, limit)
            # for mat
            if not results:
                text = f"Search '{query}' returned no results"
            else:
                lines = []
                for i, r in enumerate(results[:limit], 1):
                    lines.append(f"{i}. [{r['title']}]({r['url']})")
                    if r.get("snippet"):
                        lines.append(f"   {r['snippet']}")
                text = "\n".join(lines)

            return ToolResult.text(
                text,
                details={
                    "provider": prov,
                    "query": query,
                    "count": len(results[:limit]),
                },
            ).to_dict()
        except Exception as e:
            base_url = _provider_base_url(prov)
            request_url = _provider_request_url(prov, query, limit)
            proxy_info = _proxy_debug_info()
            last_error = f"{prov}: {type(e).__name__}: {e!r}"
            logger.warning(
                "Search Provider request failed | provider=%s base_url=%s request_url=%s query=%r limit=%s proxy=%s error_type=%s error=%r",
                prov,
                base_url,
                request_url,
                query,
                limit,
                proxy_info,
                type(e).__name__,
                e,
                exc_info=True,
            )
            continue


    # Provider
    return ToolResult.error(
        f"All search Providers unavailable: {last_error}",
        details={
            "provider": used_provider,
            "base_url": _provider_base_url(used_provider),
            "query": query,
            "count": 0,
            "proxy": _proxy_debug_info(),
        },
    ).to_dict()



async def _search_with_provider(
    provider: str,
    query: str,
    limit: int,
) -> list[dict]:
    """

search Provider

    Returns:
        [{"title":str, "url":str, "snippet":str},...]
    
"""
    if provider == "bing":
        return await _search_bing(query, limit)
    elif provider == "duckduckgo":
        return await _search_duckduckgo(query, limit)
    elif provider == "google":
        return await _search_google(query, limit)
    else:
        raise ValueError(f"Unsupported search Provider: {provider}")


async def _search_bing(query: str, limit: int) -> list[dict]:
    """Bing search(HTML fetch-and-parse)"""
    import httpx

    url = f"https://www.bing.com/search?q={quote_plus(query)}&count={limit}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, trust_env=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text

    return _parse_bing_results(html, limit)



def _parse_bing_results(html: str, limit: int) -> list[dict]:
    """parse Bing Search results HTML"""
    results: list[dict] = []

    # <li class="b_algo"> in <a href="...">title</a> and snippet
    blocks = re.findall(
        r'<li\s+class="b_algo"[^>]*>(.*?)</li>',
        html,
        re.DOTALL | re.IGNORECASE,
    )

    for block in blocks[:limit]:
        # linkandheading
        link_match = re.search(r'<a\s+[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not link_match:
            continue
        url = link_match.group(1)
        title = re.sub(r'<[^>]+>', '', link_match.group(2)).strip()

        # 
        snippet = ""
        snippet_match = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        if snippet_match:
            snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()

        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})

    return results


async def _search_duckduckgo(query: str, limit: int) -> list[dict]:
    """DuckDuckGo search(HTML fetch-and-parse)"""
    import httpx

    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, trust_env=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text

    return _parse_duckduckgo_results(html, limit)



def _parse_duckduckgo_results(html: str, limit: int) -> list[dict]:
    """Parse DuckDuckGo HTML results into normalized search records."""
    results: list[dict] = []

    # Parse result blocks from DuckDuckGo's HTML layout.
    blocks = re.findall(
        r'<div\s+[^>]*class="[^"]*result[^"]*"[^>]*>(.*?)</div>\s*(?=<div\s+[^>]*class="[^"]*result)',
        html,
        re.DOTALL | re.IGNORECASE,
    )
    if not blocks:
        # Fallback to extracting direct result links.
        links = re.findall(
            r'<a\s+[^>]*class="result__a"[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        for href, title_html in links[:limit]:
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            if title and href:
                results.append({"title": title, "url": href, "snippet": ""})
        return results

    for block in blocks[:limit]:
        link_match = re.search(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not link_match:
            continue
        url = link_match.group(1)
        title = re.sub(r'<[^>]+>', '', link_match.group(2)).strip()

        snippet = ""
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</[at]', block, re.DOTALL)
        if snippet_match:
            snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()

        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})

    return results


async def _search_google(query: str, limit: int) -> list[dict]:
    """Google search(HTML fetch-and-parse)"""
    import httpx

    url = f"https://www.google.com/search?q={quote_plus(query)}&num={limit}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, trust_env=True) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        html = resp.text

    return _parse_google_results(html, limit)



def _parse_google_results(html: str, limit: int) -> list[dict]:
    """parse Google Search results HTML"""
    results: list[dict] = []

    # Google at <div class="g"> in
    blocks = re.findall(
        r'<div\s+class="g"[^>]*>(.*?)</div>',
        html,
        re.DOTALL | re.IGNORECASE,
    )

    for block in blocks[:limit]:
        link_match = re.search(r'<a\s+[^>]*href="(https?://[^"]+)"', block)
        if not link_match:
            continue
        url = link_match.group(1)

        title_match = re.search(r'<h3[^>]*>(.*?)</h3>', block, re.DOTALL)
        title = re.sub(r'<[^>]+>', '', title_match.group(1)).strip() if title_match else ""

        snippet = ""
        snippet_match = re.search(r'<span[^>]*>(.*?)</span>', block, re.DOTALL)
        if snippet_match:
            snippet = re.sub(r'<[^>]+>', '', snippet_match.group(1)).strip()

        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})

    return results
