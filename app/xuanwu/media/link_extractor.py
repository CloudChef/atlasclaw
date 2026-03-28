# -*- coding: utf-8 -*-
"""


link

implement URL content and.
corresponds to tasks.md P2.2.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field


@dataclass
class ExtractedLink:
    """

link
    
    Attributes:
        url:raw URL
        domain:
        title:heading
        description:description
        content:content
        content_type:contenttype
        status_code:HTTP
        error:
    
"""
    url: str
    domain: str = ""
    title: str = ""
    description: str = ""
    content: str = ""
    content_type: str = ""
    status_code: int = 0
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        if not self.domain and self.url:
            parsed = urlparse(self.url)
            self.domain = parsed.netloc


class LinkExtractorConfig(BaseModel):
    """link configuration"""
    # /
    whitelist: list[str] = Field(default_factory=list)  # 
    blacklist: list[str] = Field(default_factory=list)  # 
    
    # configuration
    max_content_length: int = 50000  # content
    timeout_seconds: int = 30  # 
    follow_redirects: bool = True  # 
    max_redirects: int = 5  # count
    
    # contenthandle
    extract_metadata: bool = True  # metadata
    convert_to_markdown: bool = True  # Markdown
    remove_scripts: bool = True  # 
    remove_styles: bool = True  # 
    
    def is_allowed(self, url: str) -> bool:
        """check URL"""
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        
        # check
        for pattern in self.blacklist:
            if self._match_domain(pattern, domain):
                return False
        
        # such as, in
        if self.whitelist:
            for pattern in self.whitelist:
                if self._match_domain(pattern, domain):
                    return True
            return False
        
        return True
    
    def _match_domain(self, pattern: str, domain: str) -> bool:
        """mode"""
        pattern = pattern.lower()
        if pattern.startswith("*."):
            return domain.endswith(pattern[1:])
        return pattern == domain


class LinkExtractor:
    """

link
    
    from in URL.
    
"""
    
    # URL
    URL_PATTERN = re.compile(
        r'https?://[^\s<>\[\]{}|\\^`\'"]+',
        re.IGNORECASE
    )
    
    @classmethod
    def extract_urls(cls, text: str) -> list[str]:
        """

from in URL
        
        Args:
            text:input text
            
        Returns:
            URL list
        
"""
        urls = cls.URL_PATTERN.findall(text)
        
        # URL
        cleaned = []
        for url in urls:
            # 
            while url and url[-1] in ".,;:!?)'\"]":
                url = url[:-1]
            if url:
                cleaned.append(url)
        
        # 
        seen = set()
        unique = []
        for url in cleaned:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        
        return unique
    
    @classmethod
    def is_valid_url(cls, url: str) -> bool:
        """check URL"""
        try:
            parsed = urlparse(url)
            return bool(parsed.scheme and parsed.netloc)
        except Exception:
            return False


class LinkUnderstandingHandler:
    """

link handle
    
    and URL content.
    
    Example usage::
    
        handler = LinkUnderstandingHandler()
        
        # singlelink
        result = await handler.fetch("https://example.com")
        print(result.content)
        
        # from in handle link
        results = await handler.process_text(" https://example.com multi")
    
"""
    
    def __init__(
        self,
        config: Optional[LinkExtractorConfig] = None,
    ) -> None:
        """


initializehandle
 
 Args:
 config:configuration
 
"""
        self.config = config or LinkExtractorConfig()
    
    async def fetch(self, url: str) -> ExtractedLink:
        """

URL content
        
        Args:
            url:URL
            
        Returns:
            
        
"""
        import httpx
        
        # check
        if not self.config.is_allowed(url):
            return ExtractedLink(
                url=url,
                error="URL not allowed by policy",
            )
        
        try:
            async with httpx.AsyncClient(
                follow_redirects=self.config.follow_redirects,
                timeout=self.config.timeout_seconds,
                trust_env=True,
            ) as client:

                response = await client.get(url)
                
                content_type = response.headers.get("content-type", "")
                
                result = ExtractedLink(
                    url=url,
                    status_code=response.status_code,
                    content_type=content_type,
                )
                
                if response.status_code >= 400:
                    result.error = f"HTTP {response.status_code}"
                    return result
                
                # handle HTML content
                if "text/html" in content_type:
                    html = response.text
                    result = await self._process_html(result, html)
                
                # handle
                elif "text/plain" in content_type:
                    result.content = response.text[:self.config.max_content_length]
                
                # handle JSON
                elif "application/json" in content_type:
                    import json
                    try:
                        data = response.json()
                        result.content = json.dumps(data, indent=2, ensure_ascii=False)
                        if len(result.content) > self.config.max_content_length:
                            result.content = result.content[:self.config.max_content_length] + "\n..."
                    except Exception:
                        result.content = response.text[:self.config.max_content_length]
                
                return result
                
        except httpx.TimeoutException:
            return ExtractedLink(url=url, error="Request timeout")
        except httpx.TooManyRedirects:
            return ExtractedLink(url=url, error="Too many redirects")
        except Exception as e:
            return ExtractedLink(url=url, error=str(e))
    
    async def _process_html(
        self,
        result: ExtractedLink,
        html: str,
    ) -> ExtractedLink:
        """handle HTML content"""
        try:
            from bs4 import BeautifulSoup
            
            soup = BeautifulSoup(html, "html.parser")
            
            # heading
            title_tag = soup.find("title")
            if title_tag:
                result.title = title_tag.get_text(strip=True)
            
            # description
            if self.config.extract_metadata:
                meta_desc = soup.find("meta", attrs={"name": "description"})
                if meta_desc and meta_desc.get("content"):
                    result.description = meta_desc["content"]
                
                # Open Graph metadata
                og_title = soup.find("meta", property="og:title")
                if og_title and og_title.get("content"):
                    result.metadata["og_title"] = og_title["content"]
                
                og_desc = soup.find("meta", property="og:description")
                if og_desc and og_desc.get("content"):
                    result.metadata["og_description"] = og_desc["content"]
            
            # 
            if self.config.remove_scripts:
                for script in soup.find_all("script"):
                    script.decompose()
            
            if self.config.remove_styles:
                for style in soup.find_all("style"):
                    style.decompose()
            
            # Remove common layout and navigation elements.
            for tag in soup.find_all(["nav", "header", "footer", "aside", "iframe"]):
                tag.decompose()
            
            # Pick the most likely main-content container.
            main_content = (
                soup.find("main") or
                soup.find("article") or
                soup.find("div", class_=re.compile(r"content|main|article", re.I)) or
                soup.find("body")
            )
            
            if main_content:
                if self.config.convert_to_markdown:
                    result.content = self._html_to_markdown(main_content)
                else:
                    result.content = main_content.get_text(separator="\n", strip=True)
            
            # Truncate overly long extracted content.
            if len(result.content) > self.config.max_content_length:
                result.content = result.content[:self.config.max_content_length] + "\n...[Content truncated]"
            
        except ImportError:
            # Fall back to a simple extractor when BeautifulSoup is unavailable.
            result.content = self._simple_html_extract(html)
        
        return result
    
    def _html_to_markdown(self, element: Any) -> str:
        """Convert HTML into Markdown using a simplified renderer."""
        lines = []
        
        for child in element.children:
            if isinstance(child, str):
                text = child.strip()
                if text:
                    lines.append(text)
                continue
            
            tag_name = getattr(child, "name", None)
            if not tag_name:
                continue
            
            text = child.get_text(strip=True)
            if not text:
                continue
            
            # heading
            if tag_name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = int(tag_name[1])
                lines.append("#" * level + " " + text)
            # paragraph
            elif tag_name == "p":
                lines.append(text)
                lines.append("")
            # list
            elif tag_name in ("ul", "ol"):
                for li in child.find_all("li", recursive=False):
                    lines.append("- " + li.get_text(strip=True))
            # link
            elif tag_name == "a":
                href = child.get("href", "")
                if href:
                    lines.append(f"[{text}]({href})")
                else:
                    lines.append(text)
            # code
            elif tag_name == "pre":
                lines.append("```")
                lines.append(text)
                lines.append("```")
            # 
            else:
                lines.append(text)
        
        return "\n".join(lines)
    
    def _simple_html_extract(self, html: str) -> str:
        """HTML"""
        # and
        html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
        
        # 
        text = re.sub(r'<[^>]+>', ' ', html)
        
        # 
        text = re.sub(r'\s+', ' ', text)
        
        return text.strip()[:self.config.max_content_length]
    
    async def process_text(
        self,
        text: str,
        *,
        max_links: int = 5,
    ) -> list[ExtractedLink]:
        """

handle in link
        
        Args:
            text:input text
            max_links:handlelinkcount
            
        Returns:
            list
        
"""
        urls = LinkExtractor.extract_urls(text)[:max_links]
        
        if not urls:
            return []
        
        # 
        tasks = [self.fetch(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        extracted = []
        for url, result in zip(urls, results):
            if isinstance(result, Exception):
                extracted.append(ExtractedLink(url=url, error=str(result)))
            else:
                extracted.append(result)
        
        return extracted
    
    def inject_link_content(
        self,
        text: str,
        links: list[ExtractedLink],
    ) -> str:
        """


convertlinkcontentinject into the messagein
 
 Args:
 text:raw text
 links:linklist
 
 Returns:
 inject
 
"""
        if not links:
            return text
        
        # build link
        summaries = []
        for link in links:
            if link.error:
                continue
            
            summary_parts = [f"### {link.title or link.url}"]
            if link.description:
                summary_parts.append(link.description)
            if link.content:
                # 500 characters
                preview = link.content[:500]
                if len(link.content) > 500:
                    preview += "..."
                summary_parts.append(f"\n{preview}")
            
            summaries.append("\n".join(summary_parts))
        
        if not summaries:
            return text
        
        # inject into the message
        link_section = "\n\n---\n**Link Content:**\n\n" + "\n\n".join(summaries)
        return text + link_section
