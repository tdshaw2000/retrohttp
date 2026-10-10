"""A forward proxy that keeps a rendering browser off the local network.

The headless browser (see js_renderer) is started with this as its only
way out. For every connection it resolves the host once, refuses the
name if any answer is not a public address (loopback, private, link-local,
cloud metadata...), and then connects to exactly the address it checked,
so a name that answers differently a second time (DNS rebinding) gets
nowhere. Plain http is forwarded with `Connection: close`; https and
WebSockets arrive as CONNECT tunnels. Bodies are streamed, never
buffered, and the whole render shares one byte budget.
"""

import ipaddress
import select
import socket
import socketserver
import threading
from urllib.parse import urlsplit

# Total bytes the browser may download during one render.
DEFAULT_BYTE_BUDGET = 20_000_000
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10.0
IDLE_TIMEOUT_SECONDS = 30.0
MAX_REQUEST_HEAD_BYTES = 65536

_HOP_BY_HOP_HEADERS = {
    "connection",
    "proxy-connection",
    "keep-alive",
    "proxy-authorization",
    "te",
    "upgrade",
}
_REFUSED = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_BAD_GATEWAY = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_BAD_REQUEST = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


def is_public(address: str) -> bool:
    return ipaddress.ip_address(address.split("%")[0]).is_global


class _Budget:
    def __init__(self, total: int):
        self.remaining = total
        self._lock = threading.Lock()

    def take(self, wanted: int) -> int:
        """How many of `wanted` bytes may still be passed on."""
        with self._lock:
            granted = min(wanted, self.remaining)
            self.remaining -= granted
            return granted


class GuardProxy:
    def __init__(
        self,
        allowed_hosts=frozenset(),
        byte_budget: int = DEFAULT_BYTE_BUDGET,
        resolve=socket.getaddrinfo,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_SECONDS,
    ):
        # "host:port" pairs exempt from the public-address rule (tests).
        self.allowed_hosts = frozenset(allowed_hosts)
        self.resolve = resolve
        self.connect_timeout = connect_timeout
        self._budget = _Budget(byte_budget)
        self._server = None
        self._thread = None

    def __enter__(self) -> int:
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> int:
        proxy = self

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                proxy._handle(self.request)

        self._server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self._server.server_address[1]

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._thread.join()
            self._server = None

    def checked_address(self, host: str, port: int):
        """The one address to connect to for host:port, or None if refused."""
        try:
            answers = self.resolve(host, port, 0, socket.SOCK_STREAM)
        except (OSError, UnicodeError, ValueError):
            return None
        if not answers:
            return None
        authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        if authority not in self.allowed_hosts:
            if not all(is_public(answer[4][0]) for answer in answers):
                return None
        family, _type, _proto, _name, sockaddr = answers[0]
        return family, sockaddr

    def _connect(self, host: str, port: int):
        checked = self.checked_address(host, port)
        if checked is None:
            return None, _REFUSED
        family, sockaddr = checked
        upstream = socket.socket(family, socket.SOCK_STREAM)
        upstream.settimeout(self.connect_timeout)
        try:
            upstream.connect(sockaddr)
        except OSError:
            upstream.close()
            return None, _BAD_GATEWAY
        upstream.settimeout(None)
        return upstream, None

    def _handle(self, client: socket.socket) -> None:
        client.settimeout(IDLE_TIMEOUT_SECONDS)
        try:
            head, rest = _read_head(client)
            if head is None:
                client.sendall(_BAD_REQUEST)
                return
            lines = head.split(b"\r\n")
            try:
                method, target, version = lines[0].decode("latin-1").split(" ")
            except ValueError:
                client.sendall(_BAD_REQUEST)
                return
            if method.upper() == "CONNECT":
                self._tunnel(client, target, rest)
            else:
                self._forward(client, method, target, version, lines[1:], rest)
        except OSError:
            pass
        finally:
            client.close()

    def _tunnel(self, client, authority: str, rest: bytes) -> None:
        host, port = _split_authority(authority, default_port=443)
        if host is None:
            client.sendall(_BAD_REQUEST)
            return
        upstream, error = self._connect(host, port)
        if upstream is None:
            client.sendall(error)
            return
        with upstream:
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
            self._relay(client, upstream)

    def _forward(self, client, method, target, version, header_lines, rest) -> None:
        parts = urlsplit(target)
        if parts.scheme.lower() != "http" or not parts.hostname:
            client.sendall(_REFUSED)
            return
        try:
            port = parts.port or 80
        except ValueError:
            client.sendall(_BAD_REQUEST)
            return
        upstream, error = self._connect(parts.hostname, port)
        if upstream is None:
            client.sendall(error)
            return
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        headers = [
            line
            for line in header_lines
            if line
            and line.split(b":", 1)[0].strip().decode("latin-1").lower() not in _HOP_BY_HOP_HEADERS
        ]
        headers.append(b"Connection: close")
        request = f"{method} {path} {version}\r\n".encode("latin-1")
        request += b"\r\n".join(headers) + b"\r\n\r\n" + rest
        with upstream:
            upstream.sendall(request)
            self._relay(client, upstream)

    def _relay(self, client, upstream) -> None:
        """Pass bytes both ways until either side closes or the budget runs out."""
        sockets = [client, upstream]
        while True:
            readable, _, _ = select.select(sockets, [], [], IDLE_TIMEOUT_SECONDS)
            if not readable:
                return
            for source in readable:
                data = source.recv(65536)
                if not data:
                    return
                if source is upstream:
                    allowed = self._budget.take(len(data))
                    if allowed:
                        client.sendall(data[:allowed])
                    if allowed < len(data):
                        return
                else:
                    upstream.sendall(data)


def _read_head(conn: socket.socket):
    data = b""
    while b"\r\n\r\n" not in data:
        if len(data) > MAX_REQUEST_HEAD_BYTES:
            return None, b""
        chunk = conn.recv(65536)
        if not chunk:
            return None, b""
        data += chunk
    head, rest = data.split(b"\r\n\r\n", 1)
    return head, rest


def _split_authority(authority: str, default_port: int):
    parts = urlsplit("//" + authority)
    try:
        return parts.hostname, parts.port or default_port
    except ValueError:
        return None, None
