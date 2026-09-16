# CONTRACTS.md — Shared Interfaces

These are the data shapes every subagent works against. Treat as the
source of truth — if an agent's implementation needs to deviate from
these, it should flag that back rather than silently drifting.

See `SPEC.md` for the target client/dialect decisions these contracts
serve.

---

## FetchedDocument

Produced by: `fetcher`
Consumed by: `html-downgrader`

```
FetchedDocument {
  url: string
  status: int
  headers: dict
  html: bytes          // UTF-8-encoded bytes of the decoded document
  content_type: string
  asset_urls: [string] // URLs of referenced images/assets found in the page,
                        // resolved to absolute URLs, NOT yet fetched
}
```

Notes:
- `html` is decompressed (gzip/br handled already) and decoded using
  the declared or detected source charset, then always re-encoded as
  UTF-8 bytes — never the origin's raw byte sequence. Content is
  otherwise unmodified: no stripping, no rewriting. That's the
  downgrader's job.
- Redirects are followed by the fetcher; `url` reflects the final
  resolved URL, not the originally requested one.
- Implemented as a shared dataclass in `server/contracts.py`, along
  with `FetchedAsset`, `DowngradedDocument`, and `ConvertedAsset` below
  — import from there rather than redefining these shapes locally.

---

## FetchedAsset

Produced by: `fetcher` (on request, or `asset-converter` pulling
directly from `asset_urls`)
Consumed by: `asset-converter`

```
FetchedAsset {
  url: string
  bytes: bytes
  mime: string
}
```

---

## DowngradedDocument

Produced by: `html-downgrader`
Consumed by: server proxy route (orchestrator-wired, not a subagent)

```
DowngradedDocument {
  html: string          // HTML dialect per SPEC.md — Netscape 1.1 / Mosaic 2.x era
  asset_refs: [string]  // URLs now rewritten to point at /proxy/asset?url=...
  warnings: [string]    // human-readable notes on what was dropped/flattened
                         // (e.g. "stripped <script> block at offset 4021",
                         // "flattened nested table to single level")
}
```

Notes:
- `html` must validate against the allowed-tag list in `SPEC.md` —
  no `<script>`, `<style>`, `style=` attributes, frames, or any tag
  outside the documented allowlist.
- `asset_refs` URLs are what the HTML actually points to after
  rewriting — the proxy route uses these to know what to fetch via
  `asset-converter` on request.
- `warnings` should be genuinely useful for debugging downgrade
  quality, not just a dump of every tag touched — one line per
  meaningful decision, not per character stripped.

---

## ConvertedAsset

Produced by: `asset-converter`
Consumed by: server proxy route

```
ConvertedAsset {
  original_url: string
  local_path: string    // GIF (or best-effort JPEG) on disk / in cache
  mime: string           // image/gif in the common case
}
```

Notes:
- PNG sources always convert to GIF (no PNG support on target clients
  — see `SPEC.md`).
- Palette-reduced to 256 colors.
- Size ceiling implemented as a 640x480 (VGA) pixel-dimension cap —
  images are downscaled to fit, preserving aspect ratio, never
  upscaled. No separate byte-size ceiling.
- Actual function signature:
  `convert_asset(asset: FetchedAsset, output_dir: Path) -> ConvertedAsset`.
  `output_dir` is orchestration-level cache-location config supplied
  by the caller — it's not part of either data shape, so it isn't a
  field on `FetchedAsset` or `ConvertedAsset`.

---

## Ownership boundaries

- `fetcher` never touches HTML content — pure fetch, decode, and
  normalize to UTF-8 text plus a list of asset URLs.
- `html-downgrader` never fetches anything — it receives a
  `FetchedDocument` and returns a `DowngradedDocument`. No network
  calls.
- `asset-converter` never parses HTML — it receives asset
  URLs/bytes and returns converted images. No knowledge of the
  surrounding page.
- `compat-test-harness` doesn't modify the pipeline — it drives the
  integrated server from the outside (real requests) and reports
  pass/fail. Runs last, against the wired-together system.
- Wiring `fetcher → html-downgrader (+ asset-converter) → response`
  into the actual proxy route is done by the orchestrator, not
  delegated to a subagent — it's the one place that needs visibility
  across all three contracts at once.

## Change process

If any agent finds these contracts don't fit reality (a field is
missing, a type is wrong), it should say so explicitly in its report
back rather than inventing an undocumented field or adapter. Update
this file, then re-run affected agents against the new version.
