import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

GIF_MIME = "image/gif"
JPEG_MIME = "image/jpeg"
SVG_MIME = "image/svg+xml"

PALETTE_COLORS = 256

# Size ceiling (SPEC.md flags this as an open question -- see report):
# 640x480 matches standard VGA, the display resolution both target
# clients (Netscape 1.1+, Mosaic 2.x on Win3.1) can assume as a safe
# floor across the whole install base. Images only ever shrink to fit
# this box (never upscaled) and aspect ratio is preserved.
MAX_WIDTH = 640
MAX_HEIGHT = 480


@dataclass
class ConvertedAsset:
    original_url: str
    local_path: str
    mime: str


class AssetConversionError(Exception):
    """Raised when a source image cannot be converted for vintage clients."""


def convert_asset(url: str, data: bytes, mime: str, output_dir: Path) -> ConvertedAsset:
    if _looks_like_svg(mime, data):
        raise AssetConversionError(
            f"SVG rasterization is out of scope for v1, skipping: {url}"
        )

    try:
        return _convert_raster_image(url, data, output_dir)
    except (OSError, UnidentifiedImageError) as exc:
        raise AssetConversionError(f"unreadable/corrupt image at {url}: {exc}") from exc


def _convert_raster_image(url: str, data: bytes, output_dir: Path) -> ConvertedAsset:
    image = Image.open(io.BytesIO(data))
    image.thumbnail((MAX_WIDTH, MAX_HEIGHT))

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if _is_baseline_jpeg(image):
        local_path = output_dir / _filename_for(url, "jpg")
        image.save(local_path, format="JPEG")
        return ConvertedAsset(original_url=url, local_path=str(local_path), mime=JPEG_MIME)

    flattened = _flatten_to_rgb(image)
    palette_image = flattened.convert("P", palette=Image.ADAPTIVE, colors=PALETTE_COLORS)

    local_path = output_dir / _filename_for(url, "gif")
    palette_image.save(local_path, format="GIF")

    return ConvertedAsset(original_url=url, local_path=str(local_path), mime=GIF_MIME)


def _looks_like_svg(mime: str, data: bytes) -> bool:
    if mime == SVG_MIME:
        return True
    return b"<svg" in data[:512]


def _is_baseline_jpeg(image: Image.Image) -> bool:
    return image.format == "JPEG" and not image.info.get("progressive")


def _flatten_to_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    ):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def _filename_for(url: str, extension: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"{digest}.{extension}"
