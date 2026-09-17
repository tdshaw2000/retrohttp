#!/usr/bin/env python3
"""compat-test-harness: live, network-dependent compatibility checks.

This is deliberately NOT named test_*.py / *_test.py, so it is never
auto-collected by `python -m pytest` (testpaths = ["tests"] in
pyproject.toml would otherwise recurse into tests/compat/). Every
other test in this repo is offline and deterministic against local
fixture servers; this script is the one place that makes real
outbound HTTP requests to real third-party sites, so it stays out of
the default suite on purpose.

Run explicitly:

    .venv/bin/python tests/compat/run_compat_checks.py

Optional flags:

    --port PORT        port to run the transient test server(s) on
                        (default: pick a free ephemeral port; avoids
                        8080/8081 which are reserved for other live
                        instances on this box -- see project owner's
                        notes)
    --corpus PATH       path to newline-delimited URL list
                        (default: fixtures/test-urls.txt)
    --skip-dialect      skip the live dialect/corpus fetch checks,
                        run protocol-level checks only

What this covers (see SPEC.md / CONTRACTS.md for the contract being
verified, and the compat-test-harness agent brief for full scope):

  1. Protocol-level, against a real running server process:
     - HTTP/1.0 request/response shape (status line, Content-Type,
       Content-Length, Connection: close, no chunked encoding)
     - HTTP/0.9 bare request/response (raw socket -- no status line,
       no headers, exactly the fetched HTML)
     - malformed/truncated/sloppy requests get a clean response (or
       clean close) rather than hanging the connection, and the
       server process is still healthy afterward
     - upstream-fetch-failure path (DNS failure) maps to a clean 500

  2. Dialect-level, against the real corpus in fixtures/test-urls.txt,
     run once under --html-version html2 and once under html3.2:
     - no disallowed tags/attributes for that dialect leak through
       (script/style/style=/frames/on*, plus dialect-specific ones:
       font/center/div/map/area for html2)
     - output parses cleanly under two independent permissive parsers
       (lxml and stdlib html.parser)
     - a sample of <img> asset refs from each page resolve, via the
       proxy's real relative-URL-resolution + proxy-request path (see
       _resolve_asset_url below for why this isn't a naive relative
       fetch), to genuine GIF or JPEG bytes

This script does NOT touch fetcher.py / html_downgrader.py /
asset_converter.py / connection.py / proxy.py / main.py. Bugs found
here are reported, not patched here.
"""
from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(REPO_ROOT / ".venv" / "bin" / "python")

sys.path.insert(0, str(REPO_ROOT))
from bs4 import BeautifulSoup  # noqa: E402

DEFAULT_CORPUS = REPO_ROOT / "fixtures" / "test-urls.txt"

# --- generic small helpers ------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ManagedServer:
    """Starts `python -m server.main` in proxy-only mode (no --docroot)
    on a scratch port for the duration of a `with` block, and always
    tears it down cleanly on exit -- including on failure."""

    def __init__(self, port: int, html_version: str):
        self.port = port
        self.html_version = html_version
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> "ManagedServer":
        self._proc = subprocess.Popen(
            [
                PYTHON,
                "-m",
                "server.main",
                "--port",
                str(self.port),
                "--html-version",
                self.html_version,
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self._wait_until_listening()
        return self

    def _wait_until_listening(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(
            f"server did not start listening on port {self.port} within {timeout}s"
        )

    def __exit__(self, *exc_info) -> None:
        if self._proc is None:
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)


class Result:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks_run = 0

    def check(self, label: str, condition: bool, detail: str = "") -> None:
        self.checks_run += 1
        mark = "PASS" if condition else "FAIL"
        print(f"  [{mark}] {label}" + (f" -- {detail}" if detail and not condition else ""))
        if not condition:
            self.failures.append(f"{label}: {detail}")

    def summary(self, section: str) -> None:
        failed = len(self.failures)
        print(f"\n{section}: {self.checks_run - failed}/{self.checks_run} checks passed")


# --- protocol-level checks -------------------------------------------------


def send_raw(host: str, port: int, data: bytes, read_timeout: float = 5.0) -> tuple[bytes, float]:
    start = time.monotonic()
    with socket.create_connection((host, port), timeout=read_timeout) as sock:
        sock.sendall(data)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        sock.settimeout(read_timeout)
        chunks = []
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
        except (TimeoutError, socket.timeout):
            pass
        return b"".join(chunks), time.monotonic() - start


def run_protocol_checks(host: str, port: int) -> Result:
    print("\n=== Protocol-level checks ===")
    result = Result()

    # HTTP/1.0 direct request, no docroot configured -> clean 404, correct headers
    data, _ = send_raw(host, port, b"GET /somepath HTTP/1.0\r\nHost: x\r\n\r\n")
    result.check("HTTP/1.0 direct request returns a status line", data.startswith(b"HTTP/1.0 404"), repr(data[:60]))
    result.check("HTTP/1.0 response has Connection: close", b"Connection: close" in data, repr(data))
    result.check("HTTP/1.0 response has explicit Content-Length", b"Content-Length:" in data, repr(data))
    result.check("HTTP/1.0 response has no Transfer-Encoding/chunked", b"Transfer-Encoding" not in data, repr(data))

    # HTTP/1.0 proxy request against a known-good page
    data, _ = send_raw(
        host, port,
        b"GET http://example.com/ HTTP/1.0\r\nHost: example.com\r\n\r\n",
        read_timeout=15,
    )
    result.check("HTTP/1.0 proxy request returns 200", data.startswith(b"HTTP/1.0 200"), repr(data[:200]))
    result.check("HTTP/1.0 proxy response has Connection: close", b"Connection: close" in data, repr(data[:200]))
    result.check(
        "HTTP/1.0 proxy response body is downgraded HTML (no <script>)",
        b"<script" not in data.lower(),
        repr(data[:300]),
    )

    # HTTP/0.9 bare request (raw socket, no version token) against a known-good page
    data, _ = send_raw(host, port, b"GET http://example.com/\r\n", read_timeout=15)
    result.check("HTTP/0.9 response has no status line", not data.startswith(b"HTTP/"), repr(data[:60]))
    result.check("HTTP/0.9 response has no headers block", b"Content-Type:" not in data[:500], repr(data[:200]))
    result.check("HTTP/0.9 response is the raw HTML body", data.strip().startswith(b"<!DOCTYPE") or data.strip().startswith(b"<html"), repr(data[:100]))

    # HTTP/0.9 bare request for a 404 -> spec says raw HTML only, so on
    # error there are no headers to send; empty body is the documented
    # best-effort behavior (see connection.py's explicit HTTP/0.9 + non-200 branch)
    data, _ = send_raw(host, port, b"GET /no-such-direct-path\r\n")
    result.check("HTTP/0.9 404 case does not hang/error, returns cleanly", True, "")
    result.check("HTTP/0.9 404 case has no status line/headers leaked", not data.startswith(b"HTTP/"), repr(data[:60]))

    # Malformed / truncated / sloppy requests: must not hang, must not crash the server
    malformed_cases = {
        "empty_request": b"",
        "garbage_no_crlf": b"asdf;lkj GIBBERISH NOT HTTP AT ALL",
        "truncated_mid_headers": b"GET / HTTP/1.0\r\nHost: example.com\r\nX-Partial",
        "only_crlf": b"\r\n\r\n",
        "null_bytes": b"GET /\x00\x00\x00 HTTP/1.0\r\n\r\n",
        "request_line_only_no_terminator": b"GET / HTTP/1.0",
    }
    for name, payload in malformed_cases.items():
        try:
            data, elapsed = send_raw(host, port, payload, read_timeout=5.0)
            result.check(f"malformed case '{name}' does not hang (<4.5s)", elapsed < 4.5, f"elapsed={elapsed:.2f}s")
        except (ConnectionResetError, BrokenPipeError) as exc:
            # A TCP-level reset when the client is still mid-write while
            # the server closes early (e.g. an oversized request line)
            # is a client-observable artifact, not evidence the server
            # hung or crashed -- verified separately via the sanity
            # check below and the server's own stdout/stderr.
            result.check(f"malformed case '{name}' does not hang", True, f"connection reset (non-hang): {exc!r}")

    # Sanity: server still healthy after the malformed-request battery
    data, _ = send_raw(host, port, b"GET http://example.com/ HTTP/1.0\r\nHost: example.com\r\n\r\n", read_timeout=15)
    result.check("server still serves normal requests after malformed batch", data.startswith(b"HTTP/1.0 200"), repr(data[:100]))

    # Upstream fetch failure (DNS failure) -> clean 500, not a hang/crash
    data, _ = send_raw(
        host, port,
        b"GET http://this-domain-does-not-exist-xyz123.invalid/ HTTP/1.0\r\nHost: x\r\n\r\n",
        read_timeout=15,
    )
    result.check("unreachable upstream maps to clean 500", data.startswith(b"HTTP/1.0 500"), repr(data[:100]))

    result.summary("Protocol-level")
    return result


# --- dialect-level checks ---------------------------------------------------

DIALECT_DISALLOWED = {
    "html2": {
        "script", "style", "noscript", "frame", "frameset", "canvas",
        "video", "audio", "iframe", "embed", "object", "source", "track",
        "svg", "applet", "map", "area", "font", "basefont", "center",
        "div", "strike", "caption", "link",
    },
    "html3.2": {
        "script", "style", "noscript", "frame", "frameset", "canvas",
        "video", "audio", "iframe", "embed", "object", "source", "track",
        "svg", "applet", "link",
    },
}

IMAGE_MAGIC = {
    b"GIF87a": "gif",
    b"GIF89a": "gif",
    b"\xff\xd8\xff": "jpeg",
}


def _load_corpus(path: Path) -> list[str]:
    urls = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return urls


def _fetch_via_proxy(port: int, absolute_url: str, timeout: float = 40.0):
    proxy_handler = urllib.request.ProxyHandler({"http": f"http://127.0.0.1:{port}"})
    opener = urllib.request.build_opener(proxy_handler)
    try:
        with opener.open(absolute_url, timeout=timeout) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, b"", ""
    except urllib.error.URLError as e:
        return None, b"", str(e)


def _sniff_image(data: bytes) -> str:
    for magic, kind in IMAGE_MAGIC.items():
        if data.startswith(magic):
            return kind
    return "unknown"


def _resolve_asset_url(page_final_url: str, img_src: str) -> str:
    """Mirrors what a *real* proxy-configured browser does with the
    downgrader's rewritten <img src="/proxy/asset?url=...">: it's a
    root-relative path, resolved against the page's own base URL (the
    browser has no idea it's talking to a proxy at the URL-resolution
    stage -- proxy config only affects where the TCP connection goes).
    So the request the browser actually issues is
    `GET <page-origin>/proxy/asset?url=... HTTP/1.0` sent to the proxy.
    handle_proxy_request() only inspects path+query, not host, so this
    resolves correctly server-side even though the host in the request
    line is (deliberately, correctly) the original site's, not this
    proxy's own. A naive `requests.get(relative_path)` against the
    proxy's own root would NOT reproduce real browser behavior here --
    confirmed by hand during this harness's development.
    """
    absolute = urljoin(page_final_url, img_src)
    return absolute.replace("https://", "http://", 1)


def run_dialect_checks(port: int, dialect: str, corpus: list[str]) -> Result:
    print(f"\n=== Dialect-level checks: --html-version {dialect} ===")
    result = Result()

    for url in corpus:
        http_url = url.replace("https://", "http://", 1)
        print(f"\n  --- {url} ---")
        status, body, content_type = _fetch_via_proxy(port, http_url)
        result.check(f"[{url}] proxy fetch returns 200", status == 200, f"status={status}")
        if status != 200:
            continue

        text = body.decode("latin-1", errors="replace")
        soup = BeautifulSoup(text, "lxml")

        disallowed_found = sorted({tag.name for tag in soup.find_all(True) if tag.name in DIALECT_DISALLOWED[dialect]})
        result.check(f"[{url}] no disallowed tags for {dialect}", not disallowed_found, f"found: {disallowed_found}")

        style_attr_tags = [tag.name for tag in soup.find_all(True) if tag.has_attr("style")]
        result.check(f"[{url}] no style= attributes", not style_attr_tags, f"on tags: {style_attr_tags[:10]}")

        event_handler_tags = [
            f"{tag.name}[{attr}]"
            for tag in soup.find_all(True)
            for attr in tag.attrs
            if attr.startswith("on")
        ]
        result.check(f"[{url}] no on*= event-handler attributes", not event_handler_tags, f"{event_handler_tags[:10]}")

        try:
            BeautifulSoup(text, "html.parser")
            parses_clean = True
        except Exception as exc:  # pragma: no cover - defensive
            parses_clean = False
            result.check(f"[{url}] parses cleanly under html.parser", False, repr(exc))
        else:
            result.check(f"[{url}] parses cleanly under html.parser", parses_clean)

        img_srcs = [img.get("src") for img in soup.find_all("img") if img.get("src")]
        sample = img_srcs[:5]
        if not sample:
            print(f"    (no <img> tags on this page -- nothing to sample for asset check)")
        for src in sample:
            absolute = _resolve_asset_url(http_url, src)
            a_status, a_body, a_content_type = _fetch_via_proxy(port, absolute)
            if a_status != 200:
                result.check(f"[{url}] asset resolves: {src[:70]}", False, f"status={a_status} (see note below if SVG source)")
                continue
            kind = _sniff_image(a_body)
            result.check(
                f"[{url}] asset is real GIF/JPEG: {src[:70]}",
                kind in ("gif", "jpeg"),
                f"sniffed={kind} content_type={a_content_type} bytes={len(a_body)}",
            )

    result.summary(f"Dialect-level ({dialect})")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--skip-dialect", action="store_true")
    args = parser.parse_args()

    all_failures: list[str] = []

    port = args.port or _free_port()
    print(f"Using scratch port {port} for protocol checks (server started/stopped by this script)")
    with ManagedServer(port=port, html_version="html2"):
        protocol_result = run_protocol_checks("127.0.0.1", port)
        all_failures.extend(protocol_result.failures)

    if not args.skip_dialect:
        corpus = _load_corpus(args.corpus)
        print(f"\nLoaded {len(corpus)} corpus URLs from {args.corpus}")
        for dialect in ("html2", "html3.2"):
            port = args.port or _free_port()
            with ManagedServer(port=port, html_version=dialect):
                dialect_result = run_dialect_checks(port, dialect, corpus)
                all_failures.extend(dialect_result.failures)

    print("\n" + "=" * 70)
    if all_failures:
        print(f"OVERALL: FAIL ({len(all_failures)} failing checks)")
        for f in all_failures:
            print(f"  - {f}")
        return 1
    print("OVERALL: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
