"""Headless browser fallback for pages that only build their content in JavaScript.

The proxy calls this only when a downgraded page came out with nothing
visible on it (see proxy._handle_page); normal pages never start a
browser. The engine is optional: it needs the `playwright` package and
its Chromium build (`python -m playwright install chromium`). Without
them, or if a render fails, render() returns None and the proxy serves
its usual "no content" notice.
"""

import logging
import threading
from urllib.parse import urlparse

from server.contracts import FetchedDocument

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0
# After the load event, how long to wait for late scripts and API calls
# to go quiet. Best-effort: a page that never goes quiet is taken as is.
SETTLE_TIMEOUT_SECONDS = 3.0
# Each render is a whole browser; cap how many run at once.
MAX_CONCURRENT_RENDERS = 2

_WEB_SCHEMES = {"http", "https"}


def engine_available() -> bool:
    """True if a headless browser can actually be started here."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as playwright:
            playwright.chromium.launch().close()
    except Exception:
        return False
    return True


def _render_with_engine(url: str, timeout: float) -> FetchedDocument | None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_context(accept_downloads=False).new_page()
            response = page.goto(url, wait_until="load", timeout=timeout * 1000)
            if response is None or not response.ok:
                return None
            try:
                page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_SECONDS * 1000)
            except PlaywrightTimeout:
                pass
            return FetchedDocument(url=page.url, html=page.content().encode("utf-8"))
        finally:
            browser.close()


class JsRenderer:
    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_concurrent: int = MAX_CONCURRENT_RENDERS,
    ):
        self.timeout = timeout
        self._slots = threading.BoundedSemaphore(max_concurrent)

    def render(self, url: str) -> FetchedDocument | None:
        """The page as a browser shows it after its scripts ran, or None."""
        if urlparse(url).scheme.lower() not in _WEB_SCHEMES:
            return None
        with self._slots:
            try:
                return _render_with_engine(url, self.timeout)
            except Exception as error:
                log.warning("JavaScript rendering failed for %s: %s", url, error)
                return None
