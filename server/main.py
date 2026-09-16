import argparse
import socket
import tempfile
import threading
from pathlib import Path

from server.connection import handle_connection
from server.html_downgrader import DIALECTS
from server.proxy import AssetCache


def main() -> None:
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
    args = parser.parse_args()

    docroot = args.docroot.resolve() if args.docroot else None
    asset_cache = AssetCache(Path(tempfile.mkdtemp(prefix="retrohttp-assets-")))

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", args.port))
        listener.listen()
        docroot_description = docroot if docroot else "none (proxy-only mode)"
        print(f"Serving {docroot_description} on port {args.port}")
        while True:
            conn, _addr = listener.accept()
            threading.Thread(
                target=handle_connection,
                args=(conn, docroot, asset_cache, args.html_version),
                daemon=True,
            ).start()


if __name__ == "__main__":
    main()
