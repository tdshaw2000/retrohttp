from dataclasses import dataclass


@dataclass
class ParsedRequest:
    method: str
    target: str
    version: str
    is_malformed: bool


def parse_request_line(raw: bytes) -> ParsedRequest:
    line = raw.split(b"\r\n", 1)[0].split(b"\n", 1)[0]
    parts = line.split(b" ")

    if len(parts) == 3:
        method, target, version = parts
        return ParsedRequest(
            method=method.decode("ascii"),
            target=target.decode("ascii"),
            version=version.decode("ascii"),
            is_malformed=False,
        )

    if len(parts) == 2:
        method, target = parts
        return ParsedRequest(
            method=method.decode("ascii"),
            target=target.decode("ascii"),
            version="HTTP/0.9",
            is_malformed=False,
        )

    return ParsedRequest(method="", target="", version="", is_malformed=True)


STATUS_REASONS = {
    200: "OK",
    403: "Forbidden",
    404: "Not Found",
    500: "Internal Server Error",
}


def build_response(
    status: int, content_type: str, body: bytes, version: str
) -> bytes:
    if version == "HTTP/0.9":
        return body

    status_line = f"HTTP/1.0 {status} {STATUS_REASONS[status]}\r\n"
    headers = (
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
    )
    return status_line.encode("ascii") + headers.encode("ascii") + b"\r\n" + body
