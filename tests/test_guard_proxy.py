"""The forward proxy every rendering browser goes through."""

import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from server.guard_proxy import GuardProxy


class _Service(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _answer(self):
        self.server.seen.append((self.command, self.path, dict(self.headers)))
        body = self.server.body
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, format, *args):
        pass


@contextmanager
def _service(host: str, body: bytes = b"SECRET-CONFIG"):
    server = ThreadingHTTPServer((host, 0), _Service)
    server.seen = []
    server.body = body
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"{host}:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


def _exchange(proxy_port: int, request: bytes, read_limit: int = 10_000_000) -> bytes:
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=10) as conn:
        conn.sendall(request)
        received = b""
        while len(received) < read_limit:
            chunk = conn.recv(65536)
            if not chunk:
                break
            received += chunk
        return received


def test_plain_request_to_loopback_is_refused():
    with _service("127.0.0.2") as (service, authority), GuardProxy() as port:
        reply = _exchange(port, f"GET http://{authority}/config HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode())

    assert reply.startswith(b"HTTP/1.1 403")
    assert service.seen == []


def test_tunnel_to_loopback_is_refused():
    with _service("127.0.0.2") as (service, authority), GuardProxy() as port:
        reply = _exchange(port, f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode())

    assert reply.startswith(b"HTTP/1.1 403")
    assert service.seen == []


def test_allowed_request_is_forwarded_in_origin_form_and_closed():
    with _service("127.0.0.1", body=b"hello") as (service, authority):
        with GuardProxy(allowed_hosts={authority}) as port:
            reply = _exchange(
                port,
                f"GET http://{authority}/page?x=1 HTTP/1.1\r\nHost: {authority}\r\n"
                f"Proxy-Connection: keep-alive\r\n\r\n".encode(),
            )

    assert reply.startswith(b"HTTP/1.1 200")
    assert reply.endswith(b"hello")
    method, path, headers = service.seen[0]
    assert (method, path) == ("GET", "/page?x=1")
    assert headers.get("Connection") == "close"
    assert "Proxy-Connection" not in headers


def test_allowed_tunnel_carries_bytes_both_ways():
    with _service("127.0.0.1", body=b"tunnelled") as (service, authority):
        with GuardProxy(allowed_hosts={authority}) as port:
            with socket.create_connection(("127.0.0.1", port), timeout=10) as conn:
                conn.sendall(f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode())
                established = conn.recv(4096)
                conn.sendall(f"GET /inside HTTP/1.1\r\nHost: {authority}\r\nConnection: close\r\n\r\n".encode())
                reply = b""
                while chunk := conn.recv(65536):
                    reply += chunk

    assert established.startswith(b"HTTP/1.1 200")
    assert reply.endswith(b"tunnelled")
    assert service.seen[0][1] == "/inside"


def test_connects_to_the_address_it_checked_not_a_second_lookup():
    # A rebinding name: public on the first lookup, loopback afterwards.
    with _service("127.0.0.1") as (service, authority):
        port_number = int(authority.split(":")[1])
        answers = ["203.0.113.7", "127.0.0.1", "127.0.0.1"]

        def rebinding_resolve(host, port, *args, **kwargs):
            address = answers.pop(0) if answers else "127.0.0.1"
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

        with GuardProxy(resolve=rebinding_resolve, connect_timeout=1) as port:
            reply = _exchange(
                port,
                f"GET http://rebind.example:{port_number}/config HTTP/1.1\r\n"
                f"Host: rebind.example:{port_number}\r\n\r\n".encode(),
            )

    assert b"SECRET-CONFIG" not in reply
    assert service.seen == []


@pytest.mark.parametrize("address", ["10.0.0.1", "192.168.0.1", "169.254.169.254", "0.0.0.0"])
def test_any_private_answer_refuses_the_name(address):
    def resolve(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)),
        ]

    with GuardProxy(resolve=resolve) as port:
        reply = _exchange(port, b"GET http://mixed.example/ HTTP/1.1\r\nHost: mixed.example\r\n\r\n")

    assert reply.startswith(b"HTTP/1.1 403")


def test_only_http_is_proxied():
    with GuardProxy() as port:
        reply = _exchange(port, b"GET ftp://example.com/file HTTP/1.1\r\nHost: example.com\r\n\r\n")

    assert reply.startswith(b"HTTP/1.1 403")


def test_downloads_stop_at_the_byte_budget():
    with _service("127.0.0.1", body=b"x" * 2_000_000) as (_service_, authority):
        with GuardProxy(allowed_hosts={authority}, byte_budget=100_000) as port:
            reply = _exchange(port, f"GET http://{authority}/big HTTP/1.1\r\nHost: {authority}\r\n\r\n".encode())

    assert len(reply) <= 100_000
