from server.static_files import StaticResult, resolve_static_file


def test_serves_existing_file_under_docroot(tmp_path):
    (tmp_path / "index.html").write_bytes(b"<html>hi</html>")

    result = resolve_static_file(tmp_path, "/index.html")

    assert result == StaticResult(
        status=200, content_type="text/html", body=b"<html>hi</html>"
    )


def test_missing_file_is_404(tmp_path):
    result = resolve_static_file(tmp_path, "/nope.html")

    assert result.status == 404


def test_path_traversal_is_403(tmp_path):
    (tmp_path / "docroot").mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"top secret")

    result = resolve_static_file(tmp_path / "docroot", "/../secret.txt")

    assert result.status == 403


def test_directory_request_serves_index_html(tmp_path):
    (tmp_path / "index.html").write_bytes(b"<html>home</html>")

    result = resolve_static_file(tmp_path, "/")

    assert result == StaticResult(
        status=200, content_type="text/html", body=b"<html>home</html>"
    )


def test_empty_directory_listing_is_200_not_404(tmp_path):
    result = resolve_static_file(tmp_path, "/")

    assert result.status == 200
    assert result.content_type == "text/html"


def test_directory_without_index_lists_files(tmp_path):
    (tmp_path / "notes.txt").write_bytes(b"hi")
    (tmp_path / "readme.txt").write_bytes(b"hi")

    result = resolve_static_file(tmp_path, "/")

    assert result.status == 200
    assert result.content_type == "text/html"
    assert b'href="notes.txt"' in result.body
    assert b'href="readme.txt"' in result.body


def test_directory_listing_shows_subdirectories_with_trailing_slash(tmp_path):
    (tmp_path / "images").mkdir()

    result = resolve_static_file(tmp_path, "/")

    assert b'href="images/"' in result.body


def test_directory_listing_excludes_dotfiles(tmp_path):
    (tmp_path / ".hidden").write_bytes(b"secret")
    (tmp_path / "visible.txt").write_bytes(b"ok")

    result = resolve_static_file(tmp_path, "/")

    assert b".hidden" not in result.body
    assert b"visible.txt" in result.body


def test_directory_listing_includes_parent_link_for_subdirectory(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "file.txt").write_bytes(b"x")

    sub_result = resolve_static_file(tmp_path, "/sub/")
    root_result = resolve_static_file(tmp_path, "/")

    assert sub_result.status == 200
    assert b'href="../"' in sub_result.body
    assert b'href="../"' not in root_result.body
