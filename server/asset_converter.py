import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

GIF_MIME = "image/gif"
JPEG_MIME = "image/jpeg"

PALETTE_COLORS = 256


@dataclass
class ConvertedAsset:
    original_url: str
    local_path: str
    mime: str


class AssetConversionError(Exception):
    """Raised when a source image cannot be converted for vintage clients."""


def convert_asset(url: str, data: bytes, mime: str, output_dir: Path) -> ConvertedAsset:
    image = Image.open(io.BytesIO(data))

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
