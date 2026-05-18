# -*- coding: utf-8 -*-
# Copyright 2026  Qianyun, Inc., www.cloudchef.io, All rights reserved.

import pytest

from app.atlasclaw.tools.web.provider_adapters import (
    BingPageType,
    BingHtmlFallbackProvider,
    GoogleHtmlFallbackProvider,
    OpenRouterGroundingProvider,
    _classify_bing_html,
    _merge_search_results,
    _parse_bing_results,
)


@pytest.mark.asyncio
async def test_bing_adapter_returns_normalized_results(monkeypatch) -> None:
    provider = BingHtmlFallbackProvider()

    async def fake_fetch(*args, **kwargs):
        return '<li class="b_algo"><a href="https://example.com">Example</a><p>Snippet</p></li>'

    monkeypatch.setattr(provider, '_fetch_html', fake_fetch)
    results = await provider.search('example query', limit=5)
    assert results[0].title == 'Example'
    assert results[0].provider == 'bing_html_fallback'


@pytest.mark.asyncio
async def test_bing_adapter_accepts_generic_h2_result_blocks(monkeypatch) -> None:
    pytest.importorskip("bs4")

    provider = BingHtmlFallbackProvider(trust_env=True)

    async def fake_fetch(*args, **kwargs):
        return (
            '<div class="result-card"><h2><a href="https://example.com/hike">'
            'Hike Route</a></h2><div class="b_caption"><p>Nice loop trail</p></div></div>'
        )

    monkeypatch.setattr(provider, '_fetch_html', fake_fetch)
    results = await provider.search('hike query', limit=5)
    assert results[0].title == 'Hike Route'


def test_classify_bing_html_detects_normal_serp() -> None:
    html = (
        '<html><head><title>上海明天天气 - 搜索</title></head>'
        '<body><ol id="b_results">'
        '<li class="b_algo"><h2><a href="https://www.weather.com.cn/weather/101020100.shtml">'
        '上海天气预报</a></h2><div class="b_caption"><p>明天 14~23℃ 多云转小雨</p></div></li>'
        '</ol></body></html>'
    )

    inspection = _classify_bing_html(
        html,
        "https://www.bing.com/search?q=%E4%B8%8A%E6%B5%B7%E6%98%8E%E5%A4%A9%E5%A4%A9%E6%B0%94",
    )

    assert inspection.page_type is BingPageType.NORMAL_SERP
    assert inspection.usable is True
    assert inspection.signals.has_b_results is True
    assert inspection.signals.has_b_algo is True


def test_classify_bing_html_detects_alt_serp() -> None:
    html = (
        '<html><head><title>苏州徒步推荐 - Search</title></head><body>'
        '<form action="/search"></form>'
        '<main>'
        '<article class="result-card"><h2><a href="https://example.com/trail-1">苏州徒步路线一</a></h2>'
        '<p>环线 8 公里，适合周末出行。</p></article>'
        '<article class="result-card"><h2><a href="https://example.com/trail-2">苏州徒步路线二</a></h2>'
        '<p>山脊线视野开阔。</p></article>'
        '</main></body></html>'
    )

    inspection = _classify_bing_html(
        html,
        "https://www.bing.com/search?q=%E8%8B%8F%E5%B7%9E%E5%BE%92%E6%AD%A5%E6%8E%A8%E8%8D%90",
    )

    assert inspection.page_type is BingPageType.ALT_SERP
    assert inspection.usable is True
    assert inspection.signals.has_b_algo is False
    assert inspection.signals.external_anchor_count >= 2


def test_classify_bing_html_detects_challenge_page() -> None:
    html = (
        "<html><head><title>Please verify</title></head><body>"
        "<div>We have detected unusual traffic. Please complete the security check captcha.</div>"
        "</body></html>"
    )

    inspection = _classify_bing_html(html, "https://www.bing.com/search?q=openclaw")

    assert inspection.page_type is BingPageType.CHALLENGE
    assert inspection.usable is False
    assert inspection.signals.has_challenge_words is True


def test_classify_bing_html_detects_empty_shell() -> None:
    html = (
        '<html><head><title>Bing</title></head><body>'
        '<header>Header</header><form action="/search"><input name="q" value="上海明天天气"></form>'
        '<footer>Footer</footer></body></html>'
    )

    inspection = _classify_bing_html(
        html,
        "https://www.bing.com/search?q=%E4%B8%8A%E6%B5%B7%E6%98%8E%E5%A4%A9%E5%A4%A9%E6%B0%94",
    )

    assert inspection.page_type is BingPageType.EMPTY_SHELL
    assert inspection.usable is False


def test_classify_bing_html_treats_footer_only_shell_with_scripts_as_empty_shell() -> None:
    html = (
        '<html><head><title>搜索 - Microsoft 必应</title>'
        '<style>.x{color:red}</style><script>var big = "' + ("x" * 4000) + '";</script>'
        '</head><body>'
        '<form action="/search"><input name="q" value="上海周末天气"></form>'
        '<footer>'
        '<a href="https://beian.miit.gov.cn">京ICP备10036305号-7</a>'
        '<a href="https://beian.mps.gov.cn/#/query/webSearch?code=11010802047360">京公网安备11010802047360号</a>'
        '<a href="https://go.microsoft.com/fwlink/?LinkId=521839">隐私与 Cookie</a>'
        '<a href="https://support.microsoft.com/topic/82d20721-2d6f-4012-a13d-d1910ccf203f">帮助</a>'
        '</footer></body></html>'
    )

    inspection = _classify_bing_html(
        html,
        "https://cn.bing.com/search?q=%E4%B8%8A%E6%B5%B7%E5%91%A8%E6%9C%AB%E5%A4%A9%E6%B0%94&setlang=zh-Hans&cc=cn&mkt=zh-CN",
    )

    assert inspection.page_type is BingPageType.EMPTY_SHELL
    assert inspection.usable is False


def test_parse_bing_results_uses_loose_parser_for_alt_serp() -> None:
    pytest.importorskip("bs4")

    html = (
        '<html><head><title>苏州徒步推荐 - Search</title></head><body><main>'
        '<section class="card"><h2><a href="https://example.com/trails/suzhou-a">苏州灵岩山徒步</a></h2>'
        '<p>3 小时环线，沿途可看古寺与山林。</p></section>'
        '<section class="card"><h2><a href="https://example.com/trails/suzhou-b">苏州天平山徒步</a></h2>'
        '<p>秋季枫叶路线，坡度适中。</p></section>'
        '</main></body></html>'
    )

    results = _parse_bing_results(html, limit=5, query="苏州徒步推荐")

    assert [item["title"] for item in results[:2]] == ["苏州灵岩山徒步", "苏州天平山徒步"]
    assert "3 小时环线" in results[0]["snippet"]


def test_merge_search_results_reranks_secondary_candidates_instead_of_appending_blindly() -> None:
    merged = _merge_search_results(
        query="苏州徒步推荐",
        primary=[
            {
                "title": "旅行网站首页",
                "url": "https://travel.example.com/home",
                "snippet": "",
            }
        ],
        secondary=[
            {
                "title": "苏州徒步推荐路线合集",
                "url": "https://guide.example.com/suzhou-hiking",
                "snippet": "包含难度、距离和周末线路。",
            }
        ],
        limit=5,
    )

    assert merged[0]["title"] == "苏州徒步推荐路线合集"


@pytest.mark.asyncio
async def test_bing_adapter_falls_back_to_rss_when_html_has_no_results(monkeypatch) -> None:
    provider = BingHtmlFallbackProvider(trust_env=False)
    responses = iter([
        '<html><head><title>No direct results</title></head><body></body></html>',
        (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<rss version="2.0"><channel>'
            '<item><title>苏州徒步路线</title>'
            '<link>https://example.com/suzhou-trail</link>'
            '<description>经典线路</description>'
            '</item>'
            '</channel></rss>'
        ),
    ])

    async def fake_fetch(*args, **kwargs):
        return next(responses)

    monkeypatch.setattr(provider, '_fetch_html', fake_fetch)
    results = await provider.search('苏州周边徒步路线推荐', limit=5)
    assert results[0].title == '苏州徒步路线'
    assert results[0].url == 'https://example.com/suzhou-trail'


@pytest.mark.asyncio
async def test_bing_adapter_retries_without_proxy_when_proxy_parse_is_empty(monkeypatch) -> None:
    provider = BingHtmlFallbackProvider(trust_env=True)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10792")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10792")
    calls = {'proxy': 0, 'direct': 0}

    async def fake_fetch(url, headers=None):
        _ = headers
        calls['proxy'] += 1
        if 'format=rss' in url:
            return '<?xml version="1.0" encoding="utf-8" ?><rss version="2.0"><channel></channel></rss>'
        return '<html><body>No parseable result</body></html>'

    async def fake_fetch_no_proxy(url, headers=None):
        _ = headers
        calls['direct'] += 1
        if 'format=rss' in url:
            return (
                '<?xml version="1.0" encoding="utf-8" ?>'
                '<rss version="2.0"><channel>'
                '<item><title>上海天气预报</title>'
                '<link>https://example.com/shanghai-weather</link>'
                '<description>明天多云，14~23℃</description>'
                '</item>'
                '</channel></rss>'
            )
        return '<html><body>No parseable result</body></html>'

    monkeypatch.setattr(provider, '_fetch_html', fake_fetch)
    monkeypatch.setattr(provider, '_fetch_html_no_proxy', fake_fetch_no_proxy)
    results = await provider.search('上海明天天气', limit=5)
    assert results
    assert results[0].title == '上海天气预报'
    assert calls['proxy'] >= 1
    assert calls['direct'] >= 1


@pytest.mark.asyncio
async def test_bing_adapter_does_not_retry_without_proxy_when_proxy_results_are_non_empty(monkeypatch) -> None:
    provider = BingHtmlFallbackProvider(trust_env=True)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10792")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10792")
    calls = {'proxy': 0, 'direct': 0}

    async def fake_fetch(url, headers=None):
        _ = headers
        calls['proxy'] += 1
        if 'format=rss' in url:
            return (
                '<?xml version="1.0" encoding="utf-8" ?>'
                '<rss version="2.0"><channel>'
                '<item><title>城市论坛热榜</title>'
                '<link>https://www.zhihu.com/topic/19550818/hot</link>'
                '<description>城市讨论</description>'
                '</item>'
                '</channel></rss>'
            )
        return (
            '<li class="b_algo"><a href="https://www.zhihu.com/topic/19550818/hot">'
            '城市论坛热榜</a><p>城市讨论</p></li>'
        )

    async def fake_fetch_no_proxy(url, headers=None):
        _ = headers
        calls['direct'] += 1
        if 'format=rss' in url:
            return (
                '<?xml version="1.0" encoding="utf-8" ?>'
                '<rss version="2.0"><channel>'
                '<item><title>上海天气预报</title>'
                '<link>https://www.weather.com.cn/weather/101020100.shtml</link>'
                '<description>2日（明天）多云转小雨，23℃/14℃。</description>'
                '</item>'
                '</channel></rss>'
            )
        return (
            '<li class="b_algo"><a href="https://www.weather.com.cn/weather/101020100.shtml">'
            '上海天气预报</a><p>2日（明天）多云转小雨，23℃/14℃。</p></li>'
        )

    monkeypatch.setattr(provider, '_fetch_html', fake_fetch)
    monkeypatch.setattr(provider, '_fetch_html_no_proxy', fake_fetch_no_proxy)
    results = await provider.search('上海明天天气', limit=5)
    assert results
    assert results[0].url == 'https://www.zhihu.com/topic/19550818/hot'
    assert calls['direct'] == 0


@pytest.mark.asyncio
async def test_bing_adapter_retries_without_proxy_when_only_rss_results_exist(monkeypatch) -> None:
    provider = BingHtmlFallbackProvider(trust_env=True)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10792")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10792")
    calls = {'proxy': 0, 'direct': 0}

    async def fake_fetch(url, headers=None):
        _ = headers
        calls['proxy'] += 1
        if 'format=rss' in url:
            return (
                '<?xml version="1.0" encoding="utf-8" ?>'
                '<rss version="2.0"><channel>'
                '<item><title>上海 - 知乎</title>'
                '<link>https://www.zhihu.com/topic/19550818/hot</link>'
                '<description>上海城市介绍。</description>'
                '</item>'
                '</channel></rss>'
            )
        return '<html><body><div id="b_results"></div></body></html>'

    async def fake_fetch_no_proxy(url, headers=None):
        _ = headers
        calls['direct'] += 1
        if 'format=rss' in url:
            return (
                '<?xml version="1.0" encoding="utf-8" ?>'
                '<rss version="2.0"><channel>'
                '<item><title>上海天气预报</title>'
                '<link>https://www.weather.com.cn/weather/101020100.shtml</link>'
                '<description>2日（明天）多云转小雨，23℃/14℃。</description>'
                '</item>'
                '</channel></rss>'
            )
        return (
            '<li class="b_algo"><a href="https://www.weather.com.cn/weather/101020100.shtml">'
            '上海天气预报</a><p>2日（明天）多云转小雨，23℃/14℃。</p></li>'
        )

    monkeypatch.setattr(provider, '_fetch_html', fake_fetch)
    monkeypatch.setattr(provider, '_fetch_html_no_proxy', fake_fetch_no_proxy)
    results = await provider.search('上海明天天气', limit=5)
    assert results
    assert results[0].url == 'https://www.weather.com.cn/weather/101020100.shtml'
    assert calls['proxy'] >= 1
    assert calls['direct'] >= 1


@pytest.mark.asyncio
async def test_bing_adapter_retries_directly_when_proxy_page_is_empty_shell(monkeypatch) -> None:
    provider = BingHtmlFallbackProvider(trust_env=True)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10792")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10792")
    calls: list[tuple[str, str]] = []

    async def fake_fetch(url, headers=None):
        _ = headers
        calls.append(("proxy", url))
        return (
            '<html><head><title>Bing</title></head><body>'
            '<form action="/search"><input name="q" value="上海明天天气"></form>'
            "</body></html>"
        )

    async def fake_fetch_no_proxy(url, headers=None):
        _ = headers
        calls.append(("direct", url))
        return (
            '<html><body><ol id="b_results">'
            '<li class="b_algo"><h2><a href="https://www.weather.com.cn/weather/101020100.shtml">'
            '上海天气预报</a></h2><div class="b_caption"><p>明天 14~23℃ 多云转小雨</p></div></li>'
            '</ol></body></html>'
        )

    monkeypatch.setattr(provider, "_fetch_html", fake_fetch)
    monkeypatch.setattr(provider, "_fetch_html_no_proxy", fake_fetch_no_proxy)
    results = await provider.search("上海明天天气", limit=5)

    assert results[0].url == "https://www.weather.com.cn/weather/101020100.shtml"
    assert calls[:2] == [
        (
            "proxy",
            "https://cn.bing.com/search?q=%E4%B8%8A%E6%B5%B7%E6%98%8E%E5%A4%A9%E5%A4%A9%E6%B0%94&count=5&setlang=zh-Hans&cc=cn&mkt=zh-CN",
        ),
        (
            "direct",
            "https://cn.bing.com/search?q=%E4%B8%8A%E6%B5%B7%E6%98%8E%E5%A4%A9%E5%A4%A9%E6%B0%94&count=5&setlang=zh-Hans&cc=cn&mkt=zh-CN",
        ),
    ]


@pytest.mark.asyncio
async def test_google_adapter_unwraps_google_redirect_links(monkeypatch) -> None:
    provider = GoogleHtmlFallbackProvider(trust_env=True)

    async def fake_fetch(*args, **kwargs):
        return (
            '<div class="g">'
            '<a href="/url?q=https://example.com/suzhou-trail&sa=U&ved=0ah">'
            '<h3>苏州徒步路线</h3></a>'
            '<span>经典环线</span>'
            '</div>'
        )

    monkeypatch.setattr(provider, '_fetch_html', fake_fetch)
    results = await provider.search('苏州周边徒步路线推荐', limit=5)
    assert results[0].title == '苏州徒步路线'
    assert results[0].url == 'https://example.com/suzhou-trail'


@pytest.mark.asyncio
async def test_google_adapter_retries_without_proxy_when_proxy_parse_is_empty(monkeypatch) -> None:
    provider = GoogleHtmlFallbackProvider(trust_env=True)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:10792")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:10792")
    calls = {'proxy': 0, 'direct': 0}

    async def fake_fetch(url, headers=None):
        _ = (url, headers)
        calls['proxy'] += 1
        return '<html><body>Proxy content without result blocks</body></html>'

    async def fake_fetch_no_proxy(url, headers=None):
        _ = (url, headers)
        calls['direct'] += 1
        return (
            '<div class="g">'
            '<a href="/url?q=https://example.com/weather&sa=U&ved=0ah">'
            '<h3>上海明天天气预报</h3></a>'
            '<span>明天 14~23℃ 多云转小雨</span>'
            '</div>'
        )

    monkeypatch.setattr(provider, '_fetch_html', fake_fetch)
    monkeypatch.setattr(provider, '_fetch_html_no_proxy', fake_fetch_no_proxy)
    results = await provider.search('上海明天天气', limit=5)
    assert results
    assert results[0].url == 'https://example.com/weather'
    assert calls['proxy'] >= 1
    assert calls['direct'] >= 1


def test_provider_uses_environment_proxy_settings_by_default() -> None:
    provider = BingHtmlFallbackProvider(trust_env=True)
    kwargs = provider._build_client_kwargs()
    assert 'proxy' not in kwargs
    assert 'mounts' not in kwargs
    assert kwargs['trust_env'] is True


@pytest.mark.asyncio
async def test_openrouter_grounding_adapter_parses_summary_and_citations(monkeypatch) -> None:
    provider = OpenRouterGroundingProvider(
        api_key='sk-or-test',
        model='perplexity/sonar-pro',
        base_url='https://openrouter.ai/api/v1',
    )

    async def fake_post_json(*, url, payload, headers):
        _ = (url, payload, headers)
        return {
            'choices': [
                {
                    'message': {
                        'content': (
                            '{"summary":"明天上海多云转小雨，14℃到23℃。",'
                            '"citations":[{"title":"上海天气预报","url":"https://www.weather.com.cn/weather/101020100.shtml","snippet":"2日（明天）多云转小雨，23℃/14℃。"}]}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(provider, '_post_json', fake_post_json)
    grounded = await provider.search_grounded('明天上海天气', limit=5)
    assert grounded is not None
    assert grounded.summary.startswith('明天上海')
    assert grounded.citations[0].url == 'https://www.weather.com.cn/weather/101020100.shtml'
    assert grounded.results[0].title == '上海天气预报'
