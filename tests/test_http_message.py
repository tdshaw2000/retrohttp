from server.http_message import ParsedRequest, build_response, parse_request_line


def test_parses_http_1_0_request_line():
    result = parse_request_line(b"GET /index.html HTTP/1.0\r\n")
    assert result == ParsedRequest(
        method="GET", target="/index.html", version="HTTP/1.0", is_malformed=False
    )


def test_parses_http_0_9_request_line_with_no_version():
    result = parse_request_line(b"GET /index.html\r\n")
    assert result == ParsedRequest(
        method="GET", target="/index.html", version="HTTP/0.9", is_malformed=False
    )


def test_empty_request_line_is_malformed():
    result = parse_request_line(b"")
    assert result.is_malformed is True


def test_garbage_request_line_is_malformed():
    result = parse_request_line(b"garbage\r\n")
    assert result.is_malformed is True


def test_builds_http_1_0_response_with_status_line_and_headers():
    result = build_response(
        status=200, content_type="text/html", body=b"<html></html>", version="HTTP/1.0"
    )
    assert result == (
        b"HTTP/1.0 200 OK\r\n"
        b"Content-Type: text/html\r\n"
        b"Content-Length: 13\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"<html></html>"
    )


def test_builds_http_1_0_404_response():
    result = build_response(
        status=404, content_type="text/plain", body=b"Not Found", version="HTTP/1.0"
    )
    assert result == (
        b"HTTP/1.0 404 Not Found\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: 9\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"Not Found"
    )


def test_builds_http_1_0_400_response():
    result = build_response(
        status=400, content_type="text/plain", body=b"Bad Request", version="HTTP/1.0"
    )
    assert result == (
        b"HTTP/1.0 400 Bad Request\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: 11\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"Bad Request"
    )


def test_builds_http_0_9_response_as_raw_body_only():
    result = build_response(
        status=200, content_type="text/html", body=b"<html></html>", version="HTTP/0.9"
    )
    assert result == b"<html></html>"
