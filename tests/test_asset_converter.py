import io
from pathlib import Path

import pytest
from PIL import Image

from server.asset_converter import AssetConversionError, convert_asset
from server.contracts import ConvertedAsset, FetchedAsset

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "asset_converter"


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def test_png_with_transparency_converts_to_gif(tmp_path):
    asset = FetchedAsset(
        url="http://example.com/transparent.png",
        bytes=_fixture_bytes("transparent.png"),
        mime="image/png",
    )

    result = convert_asset(asset, output_dir=tmp_path)

    assert isinstance(result, ConvertedAsset)
    assert result.original_url == "http://example.com/transparent.png"
    assert result.mime == "image/gif"

    output_path = Path(result.local_path)
    assert output_path.is_file()
    assert output_path.parent == tmp_path

    output_image = Image.open(output_path)
    assert output_image.format == "GIF"
    assert len(output_image.getpalette()) // 3 <= 256


def test_webp_converts_to_gif(tmp_path):
    asset = FetchedAsset(
        url="http://example.com/photo.webp",
        bytes=_fixture_bytes("photo.webp"),
        mime="image/webp",
    )

    result = convert_asset(asset, output_dir=tmp_path)

    assert result.mime == "image/gif"
    output_image = Image.open(result.local_path)
    assert output_image.format == "GIF"


def test_progressive_jpeg_converts_to_gif(tmp_path):
    asset = FetchedAsset(
        url="http://example.com/progressive.jpg",
        bytes=_fixture_bytes("progressive.jpg"),
        mime="image/jpeg",
    )

    result = convert_asset(asset, output_dir=tmp_path)

    assert result.mime == "image/gif"
    output_image = Image.open(result.local_path)
    assert output_image.format == "GIF"


def test_baseline_jpeg_passes_through_as_jpeg(tmp_path):
    asset = FetchedAsset(
        url="http://example.com/baseline.jpg",
        bytes=_fixture_bytes("baseline.jpg"),
        mime="image/jpeg",
    )

    result = convert_asset(asset, output_dir=tmp_path)

    assert result.mime == "image/jpeg"
    output_image = Image.open(result.local_path)
    assert output_image.format == "JPEG"
    assert not output_image.info.get("progressive")


def test_animated_gif_keeps_only_first_frame(tmp_path):
    asset = FetchedAsset(
        url="http://example.com/animated.gif",
        bytes=_fixture_bytes("animated.gif"),
        mime="image/gif",
    )

    result = convert_asset(asset, output_dir=tmp_path)

    output_image = Image.open(result.local_path)
    assert getattr(output_image, "n_frames", 1) == 1


def test_svg_source_raises_conversion_error():
    asset = FetchedAsset(
        url="http://example.com/vector.svg",
        bytes=_fixture_bytes("vector.svg"),
        mime="image/svg+xml",
    )

    with pytest.raises(AssetConversionError):
        convert_asset(asset, output_dir=Path("/tmp/unused"))


def test_corrupt_image_raises_conversion_error(tmp_path):
    asset = FetchedAsset(
        url="http://example.com/corrupt.png",
        bytes=_fixture_bytes("corrupt.png"),
        mime="image/png",
    )

    with pytest.raises(AssetConversionError):
        convert_asset(asset, output_dir=tmp_path)


def test_oversized_image_is_downscaled_to_ceiling(tmp_path):
    large = Image.new("RGB", (1600, 1200), (10, 20, 30))
    buffer = io.BytesIO()
    large.save(buffer, format="PNG")
    asset = FetchedAsset(
        url="http://example.com/large.png", bytes=buffer.getvalue(), mime="image/png"
    )

    result = convert_asset(asset, output_dir=tmp_path)

    output_image = Image.open(result.local_path)
    assert output_image.width <= 640
    assert output_image.height <= 480


def test_malicious_url_does_not_escape_output_dir(tmp_path):
    asset = FetchedAsset(
        url="http://evil.example.com/../../../../etc/passwd.png",
        bytes=_fixture_bytes("transparent.png"),
        mime="image/png",
    )

    result = convert_asset(asset, output_dir=tmp_path)

    output_path = Path(result.local_path).resolve()
    assert output_path.parent == tmp_path.resolve()


def test_different_urls_produce_different_local_paths(tmp_path):
    data = _fixture_bytes("transparent.png")

    first = convert_asset(
        FetchedAsset(url="http://example.com/one.png", bytes=data, mime="image/png"),
        output_dir=tmp_path,
    )
    second = convert_asset(
        FetchedAsset(url="http://example.com/two.png", bytes=data, mime="image/png"),
        output_dir=tmp_path,
    )

    assert first.local_path != second.local_path
