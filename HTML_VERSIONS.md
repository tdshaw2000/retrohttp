# HTML_VERSIONS.md — HTML version / browser support reference

Quick reference for which HTML version maps to which era of browser —
used to decide dialect scope and target-client pairings in SPEC.md.

| HTML version | Year | Supporting browsers |
|---|---|---|
| 2.0 (RFC 1866) | 1995 | Mosaic 2.x, Netscape 1.x |
| 3.2 (W3C Recommendation) | Jan 1997 | Netscape 2.x/3.x, IE 3.x — tables, `<font>`/`<center>` formalized |
| 4.0 / 4.01 | Dec 1997 / 1999 | Netscape 4.x (Communicator), IE 4.x — CSS1, frames formalized, DHTML |
| XHTML 1.0 | 2000 | Netscape 6, IE 5.x |

Notes:
- IE 3.0 shipped a 16-bit Windows 3.1 build (via the Win32s
  compatibility layer) and supports HTML 3.2 — but it was also the
  first browser with any CSS support (partial CSS1), so it's not a
  "pure" HTML 3.2 client for compat-testing purposes; Netscape 3.x
  (no CSS at all) is the cleaner oracle for that dialect.
- Netscape 4.x/IE 4.x (the HTML 4.0/CSS1 generation) are 32-bit
  browsers — Windows 3.1 does not run them properly. They need
  Windows 95 (or NT4) as the target hardware.
