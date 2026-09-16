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


def test_directory_request_without_index_html_is_404(tmp_path):
    result = resolve_static_file(tmp_path, "/")

    assert result.status == 404
