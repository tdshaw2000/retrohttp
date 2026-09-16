from dataclasses import dataclass
from pathlib import Path


@dataclass
class ConvertedAsset:
    original_url: str
    local_path: str
    mime: str


class AssetConversionError(Exception):
    """Raised when a source image cannot be converted for vintage clients."""


def convert_asset(url: str, data: bytes, mime: str, output_dir: Path) -> ConvertedAsset:
    return ConvertedAsset(original_url="", local_path="", mime="")
