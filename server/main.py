import argparse
import socket
import tempfile
import threading
from pathlib import Path

from server.connection import handle_connection
from server.html_downgrader import DIALECTS
from server.js_renderer import JsRenderer, engine_available
from server.proxy import AssetCache


def format_startup_message(
    docroot_description, port: int, html_version: str, js_rendering: str = "off"
) -> str:
    return (
        f"Serving {docroot_description} on port {port} "
        f"(HTML dialect: {html_version}, JavaScript rendering: {js_rendering})"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal retro HTTP server")
    parser.add_argument(
        "--docroot",
        type=Path,
        default=None,
        help="Local directory to serve for direct (non-proxy) requests. "
        "Omit to run in proxy-only mode.",
    )
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--html-version",
        choices=sorted(DIALECTS),
        default="html2",
        help="HTML dialect to downgrade proxied pages to (default: html2)",
    )
    parser.add_argument(
        "--render-js",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When a proxied page has no visible content, render it in a "
        "headless browser so its JavaScript runs, then downgrade that "
        "(default: on; needs the browser engine, see SPEC.md)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    docroot = args.docroot.resolve() if args.docroot else None
    asset_cache = AssetCache(Path(tempfile.mkdtemp(prefix="retrohttp-assets-")))

    js_renderer = None
    js_rendering = "off"
    if args.render_js:
        if engine_available():
            js_renderer = JsRenderer()
            js_rendering = "on"
        else:
            js_rendering = "on, but the engine is not installed"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", args.port))
        listener.listen()
        docroot_description = docroot if docroot else "none (proxy-only mode)"
        print(
            format_startup_message(
                docroot_description, args.port, args.html_version, js_rendering
            )
        )
        while True:
            conn, _addr = listener.accept()
            threading.Thread(
                target=handle_connection,
                args=(conn, docroot, asset_cache, args.html_version, js_renderer),
                daemon=True,
            ).start()


if __name__ == "__main__":
    main()
