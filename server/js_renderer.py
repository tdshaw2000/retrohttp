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
- the browser's only way out is a GuardProxy (see guard_proxy), which
  refuses any host that resolves to a non-public address and connects
  to exactly the address it checked; WebRTC, QUIC and service workers,
  which could go around it, are switched off;
- Chromium's own sandbox is used where the system allows it (see
  engine_mode()).
"""

import json
import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

from server.contracts import FetchedDocument
from server.fetcher import DEFAULT_USER_AGENT
from server.guard_proxy import GuardProxy

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
STDERR_TAIL_CHARS = 2000

_WEB_SCHEMES = {"http", "https"}
_SOCKET_SCHEMES = {"ws": "http", "wss": "https"}
# Nothing may leave the browser except through the guard proxy: no
# direct WebRTC UDP, no QUIC.
_BROWSER_ARGS = [
    "--force-webrtc-ip-handling-policy",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-quic",
]
# WebRTC can also reach hosts over TURN/TCP; pages don't get it at all.
_DISABLE_DIRECT_CONNECTIONS = """
for (const name of ["RTCPeerConnection", "webkitRTCPeerConnection", "RTCDataChannel",
                    "RTCSessionDescription", "RTCIceCandidate", "WebTransport"]) {
  try { Object.defineProperty(window, name, { value: undefined, configurable: false }); }
  catch (e) {}
}
"""

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def destination_allowed(url: str, allowed_hosts=frozenset()) -> bool:
    """True if the guard proxy would let a request to url through."""
    parsed = urlparse(url)
    scheme = _SOCKET_SCHEMES.get(parsed.scheme.lower(), parsed.scheme.lower())
    if scheme not in _WEB_SCHEMES or not parsed.hostname:
        return False
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return False
    return GuardProxy(allowed_hosts).checked_address(parsed.hostname, port) is not None


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
    url: str, timeout: float, sandbox: bool, allowed_hosts, max_rendered_bytes: int
) -> FetchedDocument | None:
    """Runs in the child process (see _render_in_subprocess)."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    with GuardProxy(allowed_hosts) as proxy_port, sync_playwright() as playwright:
        # Playwright adds <-loopback> to the bypass list, so loopback
        # addresses go through the proxy too.
        browser = playwright.chromium.launch(
            chromium_sandbox=sandbox,
            args=_BROWSER_ARGS,
            proxy={"server": f"http://127.0.0.1:{proxy_port}"},
        )
        try:
            probe = browser.new_page()
            browser_agent = probe.evaluate("navigator.userAgent")
            probe.close()
            context = browser.new_context(
                accept_downloads=False,
                service_workers="block",
                user_agent=f"{browser_agent} {DEFAULT_USER_AGENT}",
            )
            context.add_init_script(_DISABLE_DIRECT_CONNECTIONS)
            page = context.new_page()
            response = page.goto(url, wait_until="load", timeout=timeout * 1000)
            if response is None or not response.ok:
                return None
            try:
                page.wait_for_load_state("networkidle", timeout=SETTLE_TIMEOUT_SECONDS * 1000)
            except PlaywrightTimeout:
                pass
            html = page.content().encode("utf-8")
            if len(html) > max_rendered_bytes:
                return None
            return FetchedDocument(url=page.url, html=html)
        finally:
            browser.close()


def _render_in_subprocess(
    url: str, timeout: float, sandbox: bool, allowed_hosts, max_rendered_bytes: int
) -> FetchedDocument | None:
    """Render in a child process, killed with its browser if it overruns.

    A page script that never yields can stall every call into the
    browser, so no in-process timeout can be relied on.
    """
    request = json.dumps(
        {
            "url": url,
            "timeout": timeout,
            "sandbox": sandbox,
            "allowed_hosts": sorted(allowed_hosts),
            "max_rendered_bytes": max_rendered_bytes,
        }
    )
    child = subprocess.Popen(
        [sys.executable, "-m", "server.js_renderer"],
        cwd=_PACKAGE_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    deadline = timeout + SETTLE_TIMEOUT_SECONDS + STARTUP_ALLOWANCE_SECONDS
    try:
        output, errors = child.communicate(request.encode("utf-8"), timeout=deadline)
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
        if errors:
            # The last lines say why (launch failure, page error...).
            tail = errors.decode("utf-8", "replace")[-STDERR_TAIL_CHARS:]
            log.warning("JavaScript rendering of %s failed:\n%s", url, tail)
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
        max_rendered_bytes: int = MAX_RENDERED_BYTES,
    ):
        self.timeout = timeout
        self.sandbox = sandbox
        self.allowed_hosts = frozenset(allowed_hosts)
        self.max_rendered_bytes = max_rendered_bytes
        self._slots = threading.BoundedSemaphore(max_concurrent)

    def render(self, url: str) -> FetchedDocument | None:
        """The page as a browser shows it after its scripts ran, or None."""
        if urlparse(url).scheme.lower() not in _WEB_SCHEMES:
            return None
        if not self._slots.acquire(timeout=self.timeout):
            log.warning("JavaScript rendering busy; serving %s without it", url)
            return None
        try:
            return _render_in_subprocess(
                url, self.timeout, self.sandbox, self.allowed_hosts, self.max_rendered_bytes
            )
        except Exception as error:
            log.warning("JavaScript rendering failed for %s: %s", url, error)
            return None
        finally:
            self._slots.release()


def _child_main() -> int:
    request = json.loads(sys.stdin.read())
    rendered = _render_with_engine(
        request["url"],
        request["timeout"],
        request["sandbox"],
        set(request["allowed_hosts"]),
        request["max_rendered_bytes"],
    )
    if rendered is None:
        return 1
    json.dump({"url": rendered.url, "html": rendered.html.decode("utf-8")}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(_child_main())
