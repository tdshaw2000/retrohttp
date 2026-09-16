import socket
import time

from server.connection import handle_connection


def test_serves_existing_file_over_http_1_0(tmp_path):
    (tmp_path / "index.html").write_bytes(b"<html>hi</html>")
    client, server = socket.socketpair()
    client.sendall(b"GET /index.html HTTP/1.0\r\n\r\n")
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, tmp_path)

    response = client.recv(65536)
    assert response == (
        b"HTTP/1.0 200 OK\r\n"
        b"Content-Type: text/html\r\n"
        b"Content-Length: 15\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"<html>hi</html>"
    )
    assert client.recv(1) == b""  # server closed its end


def test_serves_existing_file_over_http_0_9(tmp_path):
    (tmp_path / "index.html").write_bytes(b"<html>hi</html>")
    client, server = socket.socketpair()
    client.sendall(b"GET /index.html\r\n")
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, tmp_path)

    assert client.recv(65536) == b"<html>hi</html>"


def test_malformed_request_returns_400_and_closes(tmp_path):
    client, server = socket.socketpair()
    client.sendall(b"\r\n")
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, tmp_path)

    response = client.recv(65536)
    assert response == (
        b"HTTP/1.0 400 Bad Request\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: 11\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"Bad Request"
    )
    assert client.recv(1) == b""  # server closed its end


def test_non_get_method_returns_404_and_closes(tmp_path):
    client, server = socket.socketpair()
    client.sendall(b"POST /index.html HTTP/1.0\r\n\r\n")
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, tmp_path)

    response = client.recv(65536)
    assert response == (
        b"HTTP/1.0 404 Not Found\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: 9\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"Not Found"
    )
    assert client.recv(1) == b""  # server closed its end


def test_connection_with_no_data_closes_after_timeout_instead_of_hanging(tmp_path):
    client, server = socket.socketpair()
    # client deliberately sends nothing

    start = time.monotonic()
    handle_connection(server, tmp_path, timeout=0.05)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0
    assert client.recv(65536) == b""


def test_http_0_9_missing_file_closes_with_empty_body(tmp_path):
    client, server = socket.socketpair()
    client.sendall(b"GET /nope.html\r\n")
    client.shutdown(socket.SHUT_WR)

    handle_connection(server, tmp_path)

    assert client.recv(65536) == b""
