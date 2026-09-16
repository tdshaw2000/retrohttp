import io
from pathlib import Path

import pytest
from PIL import Image

from server.asset_converter import AssetConversionError, ConvertedAsset, convert_asset

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "asset_converter"


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def test_png_with_transparency_converts_to_gif(tmp_path):
    data = _fixture_bytes("transparent.png")

    result = convert_asset(
        url="http://example.com/transparent.png",
        data=data,
        mime="image/png",
        output_dir=tmp_path,
    )

    assert result.original_url == "http://example.com/transparent.png"
    assert result.mime == "image/gif"

    output_path = Path(result.local_path)
    assert output_path.is_file()
    assert output_path.parent == tmp_path

    output_image = Image.open(output_path)
    assert output_image.format == "GIF"
    assert len(output_image.getpalette()) // 3 <= 256


def test_webp_converts_to_gif(tmp_path):
    data = _fixture_bytes("photo.webp")

    result = convert_asset(
        url="http://example.com/photo.webp",
        data=data,
        mime="image/webp",
        output_dir=tmp_path,
    )

    assert result.mime == "image/gif"
    output_image = Image.open(result.local_path)
    assert output_image.format == "GIF"


def test_progressive_jpeg_converts_to_gif(tmp_path):
    data = _fixture_bytes("progressive.jpg")

    result = convert_asset(
        url="http://example.com/progressive.jpg",
        data=data,
        mime="image/jpeg",
        output_dir=tmp_path,
    )

    assert result.mime == "image/gif"
    output_image = Image.open(result.local_path)
    assert output_image.format == "GIF"


def test_baseline_jpeg_passes_through_as_jpeg(tmp_path):
    data = _fixture_bytes("baseline.jpg")

    result = convert_asset(
        url="http://example.com/baseline.jpg",
        data=data,
        mime="image/jpeg",
        output_dir=tmp_path,
    )

    assert result.mime == "image/jpeg"
    output_image = Image.open(result.local_path)
    assert output_image.format == "JPEG"
    assert not output_image.info.get("progressive")


def test_animated_gif_keeps_only_first_frame(tmp_path):
    data = _fixture_bytes("animated.gif")

    result = convert_asset(
        url="http://example.com/animated.gif",
        data=data,
        mime="image/gif",
        output_dir=tmp_path,
    )

    output_image = Image.open(result.local_path)
    assert getattr(output_image, "n_frames", 1) == 1


def test_svg_source_raises_conversion_error():
    data = _fixture_bytes("vector.svg")

    with pytest.raises(AssetConversionError):
        convert_asset(
            url="http://example.com/vector.svg",
            data=data,
            mime="image/svg+xml",
            output_dir=Path("/tmp/unused"),
        )


def test_corrupt_image_raises_conversion_error(tmp_path):
    data = _fixture_bytes("corrupt.png")

    with pytest.raises(AssetConversionError):
        convert_asset(
            url="http://example.com/corrupt.png",
            data=data,
            mime="image/png",
            output_dir=tmp_path,
        )


def test_oversized_image_is_downscaled_to_ceiling(tmp_path):
    large = Image.new("RGB", (1600, 1200), (10, 20, 30))
    buffer = io.BytesIO()
    large.save(buffer, format="PNG")

    result = convert_asset(
        url="http://example.com/large.png",
        data=buffer.getvalue(),
        mime="image/png",
        output_dir=tmp_path,
    )

    output_image = Image.open(result.local_path)
    assert output_image.width <= 640
    assert output_image.height <= 480
