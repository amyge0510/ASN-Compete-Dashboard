"""Headless-browser fetching for JavaScript-rendered pages (AWS Skill Builder).

Playwright is an optional dependency — install with:
    pip install playwright && playwright install chromium
"""
from scraper.fetch import USER_AGENT


def fetch_rendered(url: str, timeout: float = 45.0) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError(
            "This source needs a browser to render (render: true in sources.yaml). "
            "Install with: pip install playwright && playwright install chromium"
        ) from e

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
            return page.content()
        finally:
            browser.close()
