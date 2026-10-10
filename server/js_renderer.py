"""Headless browser fallback for pages that only build their content in JavaScript.

The proxy calls this only when a downgraded page came out with nothing
visible on it (see proxy._handle_page); normal pages never start a
browser. The engine is optional: it needs the `playwright` package and
its Chromium build (`python -m playwright install --with-deps chromium`).
Without them, or if a render fails, render() returns None and the proxy
serves its usual "no content" notice.

This runs a third party's JavaScript on the proxy's own machine, so:
- each render is a separate process with a hard deadline, killed
  (browser and all) when it runs over;
- every request the page makes, including the page itself, redirects,
  workers and WebSockets, is refused unless its host resolves only to
  public addresses, so pages can't reach the LAN, loopback services or
  cloud metadata. (The check resolves the name separately from the
  browser, so a DNS-rebinding host could still slip through.)
- Chromium's own sandbox is used where the system allows it (see
  engine_mode()).
"""

import ipaddress
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urljoin, urlparse

from server.contracts import FetchedDocument
from server.fetcher import DEFAULT_USER_AGENT

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0
# After the load event, how long to wait for late scripts and API calls
# to go quiet. Best-effort: a page that never goes quiet is taken as is.
SETTLE_TIMEOUT_SECONDS = 3.0
# Browser start-up and shutdown, on top of the page's own time.
STARTUP_ALLOWANCE_SECONDS = 10.0
# Each render is a whole browser; cap how many run at once.
MAX_CONCURRENT_RENDERS = 2
# A small page can build an enormous DOM; don't hand that to the downgrader.
MAX_RENDERED_BYTES = 5_000_000
MAX_REDIRECTS = 10

_WEB_SCHEMES = {"http", "https"}
_SOCKET_SCHEMES = {"ws": "http", "wss": "https"}
# Keep WebRTC from opening direct UDP connections, which no route sees.
_BROWSER_ARGS = [
    "--force-webrtc-ip-handling-policy",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
]

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def destination_allowed(url: str, allowed_hosts=frozenset()) -> bool:
    """True if a request to url may leave the browser.

    Only http(s) to hosts whose every address is public. allowed_hosts
    ("host:port" as in the URL) skips the address check; tests use it
    for their loopback fixture server.
    """
    parsed = urlparse(url)
    scheme = _SOCKET_SCHEMES.get(parsed.scheme.lower(), parsed.scheme.lower())
    if scheme not in _WEB_SCHEMES or not parsed.hostname:
        return False
    if parsed.netloc in allowed_hosts:
        return True
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
        addresses = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except (OSError, ValueError, UnicodeError):
        return False
    if not addresses:
        return False
    return all(ipaddress.ip_address(info[4][0].split("%")[0]).is_global for info in addresses)


def engine_mode() -> str | None:
    """"sandboxed" or "unsandboxed" if a headless browser starts here, else None.

    Chromium's sandbox needs kernel support that WSL, containers and
    root users often lack; then the browser runs without it and the
    network rules above are the protection that remains.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    for sandbox, mode in ((True, "sandboxed"), (False, "unsandboxed")):
        try:
            with sync_playwright() as playwright:
                playwright.chromium.launch(chromium_sandbox=sandbox, args=_BROWSER_ARGS).close()
        except Exception:
            continue
        return mode
    return None


def engine_available() -> bool:
    return engine_mode() is not None


def _render_with_engine(
    url: str, timeout: float, sandbox: bool, allowed_hosts
) -> FetchedDocument | None:
    """Runs in the child process (see _render_in_subprocess)."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    # Where the top-level page really came from, after redirects.
    final_url = []

    def guard_request(route):
        # A redirect handed back to the browser would be followed without
        # coming through here again, so follow redirects here, checking
        # every hop, and hand the browser only the final response.
        request = route.request
        url, method = request.url, request.method
        for _ in range(MAX_REDIRECTS + 1):
            if not destination_allowed(url, allowed_hosts):
                route.abort("blockedbyclient")
                return
            if method == request.method:
                response = route.fetch(url=url, max_redirects=0)
            else:
                response = route.fetch(url=url, method=method, post_data="", max_redirects=0)
            location = response.headers.get("location")
            if not (300 <= response.status < 400 and location):
                if request.is_navigation_request() and request.frame.parent_frame is None:
                    final_url[:] = [url]
                route.fulfill(response=response)
                return
            if response.status in (301, 302, 303) and method not in ("GET", "HEAD"):
                method = "GET"
            url = urljoin(url, location)
        route.abort("blockedbyclient")

    def guard_socket(ws):
        # Not connecting leaves the socket talking to nobody.
        if destination_allowed(ws.url, allowed_hosts):
            ws.connect_to_server()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(chromium_sandbox=sandbox, args=_BROWSER_ARGS)
        try:
            probe = browser.new_page()
            browser_agent = probe.evaluate("navigator.userAgent")
            probe.close()
            context = browser.new_context(
                accept_downloads=False,
                service_workers="block",
                user_agent=f"{browser_agent} {DEFAULT_USER_AGENT}",
            )
            context.route(re.compile(".*"), guard_request)
            context.route_web_socket(re.compile(".*"), guard_socket)
            page = context.new_page()
            response = page.goto(url, wait_until="load", timeout=timeout * 1000)
            if response is None or not response.ok:
                return None
            try:
                page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_SECONDS * 1000)
            except PlaywrightTimeout:
                pass
            html = page.content().encode("utf-8")
            if len(html) > MAX_RENDERED_BYTES:
                return None
            return FetchedDocument(url=(final_url or [page.url])[-1], html=html)
        finally:
            browser.close()


def _render_in_subprocess(
    url: str, timeout: float, sandbox: bool, allowed_hosts
) -> FetchedDocument | None:
    """Render in a child process, killed with its browser if it overruns.

    A page script that never yields can stall every call into the
    browser, so no in-process timeout can be relied on.
    """
    request = json.dumps(
        {"url": url, "timeout": timeout, "sandbox": sandbox, "allowed_hosts": sorted(allowed_hosts)}
    )
    child = subprocess.Popen(
        [sys.executable, "-m", "server.js_renderer"],
        cwd=_PACKAGE_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = timeout + SETTLE_TIMEOUT_SECONDS + STARTUP_ALLOWANCE_SECONDS
    try:
        output, _ = child.communicate(request.encode("utf-8"), timeout=deadline)
    except subprocess.TimeoutExpired:
        log.warning("JavaScript rendering of %s took over %.0fs; stopped it", url, deadline)
        return None
    finally:
        # The browser runs in the child's session; take it all down.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()
    if child.returncode != 0 or not output:
        return None
    result = json.loads(output)
    return FetchedDocument(url=result["url"], html=result["html"].encode("utf-8"))


class JsRenderer:
    def __init__(
        self,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_concurrent: int = MAX_CONCURRENT_RENDERS,
        sandbox: bool = True,
        allowed_hosts=frozenset(),
    ):
        self.timeout = timeout
        self.sandbox = sandbox
        self.allowed_hosts = frozenset(allowed_hosts)
        self._slots = threading.BoundedSemaphore(max_concurrent)

    def render(self, url: str) -> FetchedDocument | None:
        """The page as a browser shows it after its scripts ran, or None."""
        if urlparse(url).scheme.lower() not in _WEB_SCHEMES:
            return None
        if not self._slots.acquire(timeout=self.timeout):
            log.warning("JavaScript rendering busy; serving %s without it", url)
            return None
        try:
            return _render_in_subprocess(url, self.timeout, self.sandbox, self.allowed_hosts)
        except Exception as error:
            log.warning("JavaScript rendering failed for %s: %s", url, error)
            return None
        finally:
            self._slots.release()


def _child_main() -> int:
    request = json.loads(sys.stdin.read())
    rendered = _render_with_engine(
        request["url"], request["timeout"], request["sandbox"], set(request["allowed_hosts"])
    )
    if rendered is None:
        return 1
    json.dump({"url": rendered.url, "html": rendered.html.decode("utf-8")}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(_child_main())
