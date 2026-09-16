"""Generates the asset-converter test fixture set.

Run with: .venv/bin/python fixtures/asset_converter/generate_fixtures.py

Produces synthetic sample files (no real sample images on hand) covering:
- a PNG with an alpha transparency channel
- a WebP
- a progressive JPEG
- a baseline JPEG (for the best-effort JPEG passthrough path)
- an animated GIF (multiple frames, to exercise first-frame-only handling)
- an SVG (out of scope for v1 -- exists to prove the graceful-skip path)
- a corrupt/truncated file masquerading as a PNG (to exercise the
  fail-gracefully path)
"""

from pathlib import Path

from PIL import Image

FIXTURES_DIR = Path(__file__).parent


def make_transparent_png() -> None:
    image = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    for x in range(300):
        for y in range(200):
            image.putpixel((x, y), (x % 256, y % 256, (x + y) % 256, 128))
    image.save(FIXTURES_DIR / "transparent.png")


def make_webp() -> None:
    image = Image.new("RGB", (300, 200))
    for x in range(300):
        for y in range(200):
            image.putpixel((x, y), (x % 256, (y * 2) % 256, (x * y) % 256))
    image.save(FIXTURES_DIR / "photo.webp", format="WEBP")


def make_progressive_jpeg() -> None:
    image = Image.new("RGB", (400, 300))
    for x in range(400):
        for y in range(300):
            image.putpixel((x, y), ((x * 2) % 256, (y * 2) % 256, 128))
    image.save(FIXTURES_DIR / "progressive.jpg", format="JPEG", progressive=True)


def make_baseline_jpeg() -> None:
    image = Image.new("RGB", (400, 300))
    for x in range(400):
        for y in range(300):
            image.putpixel((x, y), (128, (x * 2) % 256, (y * 2) % 256))
    image.save(FIXTURES_DIR / "baseline.jpg", format="JPEG", progressive=False)


def make_animated_gif() -> None:
    frames = []
    for i in range(5):
        frame = Image.new("RGB", (200, 150), ((i * 40) % 256, 0, 0))
        frames.append(frame)
    frames[0].save(
        FIXTURES_DIR / "animated.gif",
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )


def make_svg() -> None:
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">\n'
        '  <circle cx="50" cy="50" r="40" fill="red" />\n'
        "</svg>\n"
    )
    (FIXTURES_DIR / "vector.svg").write_text(svg)


def make_corrupt_png() -> None:
    (FIXTURES_DIR / "corrupt.png").write_bytes(b"\x89PNG\r\n\x1a\nnot actually a png")


def main() -> None:
    make_transparent_png()
    make_webp()
    make_progressive_jpeg()
    make_baseline_jpeg()
    make_animated_gif()
    make_svg()
    make_corrupt_png()
    print("Fixtures written to", FIXTURES_DIR)


if __name__ == "__main__":
    main()
