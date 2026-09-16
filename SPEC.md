# SPEC.md — Retro HTTP Server / Proxy

## Purpose

An HTTP server for serving hand-built early-web pages, and eventually a
proxy that downgrades modern web pages, to be browsed from real or emulated
period hardware (Netscape / Mosaic on Windows 3.1).

## Target clients

- **Primary**: Netscape Navigator 1.1+ (Apr 1995 onward)
- **Secondary**: NCSA Mosaic 2.x (1994/95 Alpha/Beta era)
- Both run under Windows 3.1 via Trumpet Winsock, real or emulated
  (86Box / DOSBox recommended as the compatibility oracle — don't trust
  assumptions, test against an actual client).

This is deliberately **not** strict RFC 1866 "HTML 2.0" — that spec
predates tables entirely. Targeting the Netscape 1.1 / Mosaic 2.x era
instead means tables are in scope (critical for downgrading modern
layouts into something recognizable), while CSS, JS, frames, and
background colors are still correctly excluded (first appeared in the
Netscape 3.x / HTML 3.2+ era).

## Protocol behavior

- **HTTP/1.0** is the baseline. **HTTP/0.9** must also be handled as a
  fallback: a bare `GET /path` request line, no headers, response is raw
  HTML with no status line and no headers.
- **No persistent connections.** Every response includes
  `Connection: close`; the server closes the socket after writing the
  response body. No keep-alive, no chunked transfer-encoding (HTTP/1.0
  doesn't have chunked encoding regardless).
- Always set `Content-Length` explicitly.
- Handle malformed / truncated / sloppy requests gracefully — old
  client and network stacks are not spec-perfect. Prefer a best-effort
  response or clean 4xx over hanging or crashing.
- Basic status codes only: 200, 404, 403, 500. No need for anything
  requiring content negotiation.

### Proxy-mode request handling

- Netscape/Mosaic are configured with this server as their HTTP proxy
  (host:port set in the browser's own network/proxy preferences). Once
  configured, the user browses normally — no gateway page, no form to
  type URLs into.
- When acting as a proxy, the client sends the request line with a
  full absolute URI as the target, e.g. `GET http://bbc.co.uk/news
  HTTP/1.0`, rather than the relative-path form used for direct
  requests to this server (`GET /news HTTP/1.0` + `Host:` header).
- The server must branch on this at the request-line parsing stage: if
  the request target starts with a scheme (`http://`), treat it as a
  proxy request — fetch the target via the fetcher pipeline, downgrade
  it, and return the result. Otherwise treat it as a local static-file
  request per the existing static-serving logic.
- This proxy mode is also what makes TLS termination transparent to
  the client: since the client only ever speaks plain HTTP/1.0 to this
  server and never attempts TLS itself, all HTTPS negotiation with the
  real origin server happens entirely on the fetcher side. See "Proxy
  / TLS handling" below.

## Charset

- Output is ASCII / Latin-1. No UTF-8.
- No `<meta charset>` (didn't exist yet) — if a charset needs to be
  communicated at all, it goes in the `Content-Type` header, and often
  it's simply omitted since clients of this era assume Latin-1/ASCII.
- Anything outside that range must be transliterated or stripped before
  serving, not passed through.

## HTML dialect ("Netscape 1.1 / Mosaic 2.x era")

**Allowed:**
- Core HTML 2.0 elements: headings, paragraphs, lists, anchors, basic
  text formatting (`<b>`, `<i>`, `<tt>`, etc.), `<hr>`, `<br>`
- `<img>` (inline images)
- `<table>`, `<tr>`, `<td>`, `<th>` — no nested tables (nested table
  support didn't arrive until Mosaic's 1997 release; treat nesting as
  unsupported for this target)
- Forms (`<form>`, `<input>`, basic form elements) — present in
  HTML 2.0 and supported by both target clients
- Basic image maps if useful, otherwise skip for v1

**Explicitly excluded / must be stripped or flattened:**
- `<script>`, `<style>`, any inline CSS (`style=` attributes)
- `<link rel="stylesheet">`
- Frames (`<frame>`, `<frameset>`)
- Background colors/images, font colors, anything presentational that
  postdates this era
- Any HTML5 semantic tags, `<canvas>`, `<video>`, `<audio>`

**Downgrade behavior for excluded content:**
- Drop `<script>`/`<style>` content entirely, don't attempt to inline
  or approximate it
- Flatten CSS layout to whatever crude structural approximation is
  reasonable (nested tables → single-level table or sequential
  sections; TBD in implementation, not blocking for spec)
- Log/report anything silently dropped so downgrade quality is
  inspectable, not just guessed at

## Images

- **GIF**: safe baseline, supported everywhere. Convert to this by
  default.
- **JPEG**: inline support varies by client version — treat as
  optional/best-effort, not guaranteed.
- **PNG**: not supported (didn't exist commercially in this window).
  Always convert PNG sources to GIF.
- Palette-reduce to 256 colors for GIF output. Consider a size ceiling
  given the target hardware/network is slow even when emulated.

## Proxy / TLS handling

- Old client network stacks (Trumpet Winsock etc.) cannot do TLS at
  all.
- The proxy fully **terminates HTTPS on the modern side** and serves
  plain HTTP downstream — this is a full protocol/content translator,
  not a passthrough or tunnel.
- Modern fetch path handles redirects, gzip/br decoding, and normal
  HTTPS certificate validation as any current client would.

## Non-goals (v1)

- No support for authentication-gated pages
- No cookies/session state
- No JavaScript execution or approximation of JS-dependent content
- No attempt to preserve pixel-accurate modern layouts — "recognizable
  and readable" is the bar, not visual fidelity

## Open questions / decide during implementation

- Exact nested-table flattening strategy
- Whether to support an allowlist/blocklist for proxied domains
- Whether image maps are worth v1 effort
