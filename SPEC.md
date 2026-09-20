# SPEC.md — Retro HTTP Server / Proxy

## Purpose

An HTTP server for serving hand-built early-web pages, and eventually a
proxy that downgrades modern web pages, to be browsed from real or emulated
period hardware (Netscape / Mosaic on Windows 3.1).

## Target clients

For the `html2`/`html3.2` dialects:
- **Primary**: Netscape Navigator 1.1+ (Apr 1995 onward)
- **Secondary**: NCSA Mosaic 2.x (1994/95 Alpha/Beta era)
- Both run under Windows 3.1 via Trumpet Winsock, real or emulated
  (86Box / DOSBox recommended as the compatibility oracle — don't trust
  assumptions, test against an actual client).

For the `html4` dialect (see below), the pairing is different:
- **Netscape Communicator 4.x** and **Internet Explorer 4.x** (both
  1997 onward), run under **Windows 95**, real or emulated. These are
  32-bit browsers — Windows 3.1 does not run them (Communicator's
  16-bit Win3.1 build is CSS-crippled and unreliable; IE4 never
  shipped for Win3.1 at all). Don't move the `html2`/`html3.2` target
  hardware to Win95 to compensate — the two dialect families have
  genuinely different target clients.

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
- Basic status codes only: 200, 400, 403, 404, 500. No need for
  anything requiring content negotiation.

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

## HTML dialect: `html2` (default, Netscape 1.1 / Mosaic 2.x era)

**Allowed:**
- Core HTML 2.0 elements: headings, paragraphs, lists, anchors, basic
  text formatting (`<b>`, `<i>`, `<tt>`, etc.), `<hr>`, `<br>`
- `<img>` (inline images)
- `<table>`, `<tr>`, `<td>`, `<th>` — no nested tables (nested table
  support didn't arrive until Mosaic's 1997 release; treat nesting as
  unsupported for this target)
- Forms (`<form>`, `<input>`, basic form elements) — present in
  HTML 2.0 and supported by both target clients

**Explicitly excluded / must be stripped or flattened:**
- `<script>`, `<style>`, any inline CSS (`style=` attributes)
- `<link rel="stylesheet">`
- Frames (`<frame>`, `<frameset>`)
- Background colors/images, font colors, `<font>`/`<center>`/`<div>`,
  image maps (`<map>`/`<area>`), anything else presentational that
  postdates this era — see the `html3.2` dialect below for a looser
  option
- Any HTML5 semantic tags, `<canvas>`, `<video>`, `<audio>`

**Downgrade behavior for excluded content:**
- Drop `<script>`/`<style>` content entirely, don't attempt to inline
  or approximate it
- Flatten CSS layout to whatever crude structural approximation is
  reasonable (nested tables → single-level table or sequential
  sections; TBD in implementation, not blocking for spec)
- Log/report anything silently dropped so downgrade quality is
  inspectable, not just guessed at

## HTML version selection

The server takes a `--html-version` flag selecting which dialect
proxied pages get downgraded to. Values are real HTML version numbers,
not browser names — see `server/html_downgrader.py`'s `DIALECTS`
registry, which is the source of truth.

- **`html2`** (default) — the dialect documented above. Note this is
  an accepted approximation, not strict RFC 1866: real HTML 2.0 has no
  tables at all: tables first appeared in the (expired, never
  finalized) HTML 3.0 Internet-Draft. `html2` here means "HTML 2.0
  core + tables, nothing later," which is what both target clients
  (Netscape 1.1+, Mosaic 2.x) actually render.
- **`html3.2`** — the real W3C HTML 3.2 Recommendation (Jan 1997,
  https://www.w3.org/TR/REC-html32), verified against the spec text
  directly. Adds on top of `html2`: `<font>`, `<basefont>`,
  `<center>`, `<div>` (with `align`), `<strike>`, `<caption>`, and
  client-side image maps (`<map>`/`<area>`, `area`'s `href` proxied
  like any other link). Still excludes CSS and frames (both genuinely
  absent from the 3.2 spec) and `<script>`/`<style>` content (3.2
  itself specifies user agents must hide their contents, so stripping
  entirely already satisfies the spec). `<applet>` is a real 3.2
  element but is deliberately kept stripped in both dialects — no
  sane way for this proxy to make a Java applet do anything on a
  vintage client.

Both dialects were already renderable by the target clients in
`## Target clients` above — `font`/`center` were Netscape extensions
in wide use well before HTML 3.2 formalized them in Jan 1997, so
`html3.2` mode doesn't require anything newer than what Netscape
1.1+/Mosaic 2.x already understood, just a looser downgrade.

- **`html4`** — targets Netscape Communicator 4.x / IE 4.x on
  Windows 95 (see `## Target clients` above), not the Win3.1 pairing
  the other two dialects use. Adds on top of `html3.2`:
  - **CSS1, filtered through a conservative property allowlist**: only
    `color`, `background-color`, `font-family`, `font-size`,
    `font-weight`, `font-style`, `text-align`, `margin*`, `padding*`,
    `border*`, `width`, `height`. Applies to both `<style>` block
    contents and inline `style=` attributes — both are now retained
    rather than stripped, but every declaration is checked against
    this allowlist first. Anything outside it (selectors, at-rules
    like `@media`, any CSS2+/CSS3 syntax — `calc()`, `rgba()`,
    flex/grid, custom properties, units CSS1 doesn't have) is dropped
    and logged, never passed through. This is deliberately narrower
    than the full CSS1 recommendation: Communicator 4's CSS engine is
    known to misrender or destabilize on CSS it doesn't expect, so
    conservative-and-working beats spec-complete-and-fragile.
  - **Frames** (`<frameset>`, `<frame>`, `<noframes>`) — passthrough
    only. If the source page already uses frames, carry the structure
    through (each frame's `src` proxied like any other link). No
    attempt to synthesize a frameset from a modern flex/grid/CSS-grid
    layout — that's a lot of heuristic work for a layout pattern
    modern sites essentially never use.
  - `<script>` stays stripped, same as `html2`/`html3.2` — "no JS
    execution" is a standing non-goal (see below), not a per-dialect
    decision.
  - `<applet>` stays stripped, same reasoning as the other dialects.

## Images

- **GIF**: safe baseline, supported everywhere. Convert to this by
  default.
- **JPEG**: inline support varies by client version — treat as
  optional/best-effort, not guaranteed. Netscape 4.x/IE 4.x (the
  `html4` pairing) support it more reliably than the `html2`/`html3.2`
  target clients, but the default conversion behavior doesn't branch
  on dialect for v1 — GIF stays the safe default across all dialects.
- **PNG**: not supported (didn't exist commercially in the
  `html2`/`html3.2` window). Always convert PNG sources to GIF. This
  holds for `html4` too even though IE4 has partial PNG support —
  Communicator 4 needs a plugin for it, so treat PNG as unsupported
  across all dialects for now rather than branching on which `html4`
  browser is in play.
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

- ~~Exact nested-table flattening strategy~~ — resolved: repeatedly
  flatten the innermost nested `<table>`, linearizing its rows as
  inline content (`" | "` between cells, `<br>` between rows) rather
  than wrapping in `<p>`, so it composes correctly under arbitrary
  nesting depth. See `server/html_downgrader.py`.
- Whether to support an allowlist/blocklist for proxied domains — still
  open, not yet implemented.
- ~~Whether image maps are worth v1 effort~~ — resolved: implemented,
  gated behind the `html3.2` dialect (see "HTML version selection"
  above) since real HTML 2.0/`html2` has no `<map>`/`<area>`.
- `html4`'s CSS1 property allowlist (see above) is a starting set, not
  necessarily final — expect to widen it once real Communicator
  4.x/IE 4.x testing on Win95 surfaces properties that render fine but
  aren't yet in the list.
- Whether image conversion should ever branch on dialect (e.g. trust
  JPEG more, or attempt PNG passthrough for IE4 specifically) — not
  implemented; current stance is GIF-default/PNG→GIF across all
  dialects, revisit only if `html4` output quality demands it.
