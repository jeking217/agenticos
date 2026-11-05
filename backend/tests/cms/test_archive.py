"""Pulling a site's code must not be a way to write anywhere on the disk.

The archive and the edit path share these rules deliberately: a file that a
pull refuses and a push accepts would be a hole, and the two would drift apart
if each carried its own copy of "is this path allowed". So the tests below are
about one set of rules, exercised from both directions.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from app.cms.archive import (
    delete_file,
    extension_of,
    extract_site_zip,
    is_allowed,
    normalize_path,
    read_workspace,
    resolve_inside,
    write_file,
)
from app.cms.errors import PATH_REJECTED, CmsError

EXT = frozenset({"html", "css", "js", "png"})


def zip_of(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def pull(payload: bytes, root: Path, *, max_file_bytes: int = 1024 * 1024):
    return extract_site_zip(
        payload, root, allowed_ext=EXT, max_file_bytes=max_file_bytes
    )


@pytest.mark.parametrize(
    "raw",
    ["views/index.html", "/views/index.html", "//views/./index.html", "/views//index.html"],
)
def test_a_well_formed_path_comes_back_absolute_and_collapsed(raw):
    assert normalize_path(raw) == "/views/index.html"


def test_a_doubled_separator_collapses_rather_than_refuses():
    """`//` is a separator an archive may double; `\\\\` is the one that smuggles."""
    assert normalize_path("//host/share/x.html") == "/host/share/x.html"


@pytest.mark.parametrize(
    "raw",
    [
        "/views/../../etc/passwd",
        "..",
        "/C:/Windows/System32/drivers/etc/hosts",
        "\\views\\index.html",
        "/views/index\x00.html",
        "/",
    ],
)
def test_a_path_that_escapes_or_smuggles_is_refused(raw):
    with pytest.raises(CmsError) as caught:
        normalize_path(raw)

    assert caught.value.code == PATH_REJECTED


def test_an_extension_is_read_from_the_last_segment_only():
    assert extension_of("/static/app.min.js") == "js"
    assert extension_of("/views/v1.2/index.html") == "html"
    assert extension_of("/static/Makefile") == ""
    assert is_allowed("/static/logo.PNG", EXT), "case is not a policy"
    assert not is_allowed("/views/index.beetl", EXT)


def test_a_resolved_path_cannot_land_outside_the_workspace(tmp_path):
    root = tmp_path / "site"
    root.mkdir()

    assert resolve_inside(root, "/views/index.html") == (root / "views/index.html").resolve()
    with pytest.raises(CmsError):
        resolve_inside(root, "/../escaped.html")


def test_a_pull_writes_the_files_the_site_is_made_of(tmp_path):
    payload = zip_of(
        {
            "views/index.html": b"<h1>hi</h1>",
            "static/app.css": b"body{}",
            "WEB-INF/classes/App.java": b"class App {}",
        }
    )

    report = pull(payload, tmp_path)

    assert sorted(f.path for f in report.files) == ["/static/app.css", "/views/index.html"]
    assert (tmp_path / "views/index.html").read_bytes() == b"<h1>hi</h1>"
    assert not (tmp_path / "WEB-INF").exists(), "a disallowed type is not written at all"
    assert [s.path for s in report.skipped] == ["/WEB-INF/classes/App.java"]


def test_one_bad_entry_does_not_cost_the_whole_pull(tmp_path):
    payload = zip_of({"../../evil.sh": b"rm -rf /", "views/index.html": b"ok"})

    report = pull(payload, tmp_path)

    assert [f.path for f in report.files] == ["/views/index.html"]
    assert report.skipped and "climbs out" in report.skipped[0].reason
    assert not (tmp_path.parent / "evil.sh").exists()


def test_a_symlinked_entry_is_treated_as_its_name_not_its_target(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        link = zipfile.ZipInfo("static/passwd.html")
        link.external_attr = (0o120777 << 16)
        archive.writestr(link, "/etc/passwd")
    link_target = tmp_path / "link-target"
    link_target.mkdir()

    report = pull(buffer.getvalue(), tmp_path)

    written = tmp_path / "static/passwd.html"
    assert written.read_text() == "/etc/passwd", "a plain file, not a link"
    assert not written.is_symlink(), "a symlink entry is materialised as bytes"


def test_a_file_over_the_ceiling_is_skipped_rather_than_truncated(tmp_path):
    payload = zip_of({"static/big.png": b"x" * 2048, "views/index.html": b"ok"})

    report = pull(payload, tmp_path, max_file_bytes=1024)

    assert [f.path for f in report.files] == ["/views/index.html"]
    assert "larger than" in report.skipped[0].reason
    assert not (tmp_path / "static/big.png").exists()


def test_something_that_is_not_a_zip_fails_loudly(tmp_path):
    with pytest.raises(CmsError) as caught:
        pull(b"<html>login</html>", tmp_path)

    assert caught.value.code == PATH_REJECTED


def test_reading_the_workspace_skips_git_and_foreign_types(tmp_path):
    (tmp_path / "views").mkdir()
    (tmp_path / "views/index.html").write_bytes(b"ok")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_bytes(b"ref: refs/heads/main")
    (tmp_path / "notes.md").write_bytes(b"# notes")

    files = read_workspace(tmp_path, allowed_ext=EXT)

    assert list(files) == ["/views/index.html"]


def test_writing_and_deleting_go_through_the_same_rules(tmp_path):
    write_file(tmp_path, "/views/a.html", b"a", allowed_ext=EXT, max_file_bytes=1024)

    with pytest.raises(CmsError):
        write_file(tmp_path, "/views/a.php", b"a", allowed_ext=EXT, max_file_bytes=1024)
    with pytest.raises(CmsError):
        write_file(tmp_path, "/views/big.html", b"x" * 2048, allowed_ext=EXT, max_file_bytes=1024)

    delete_file(tmp_path, "/views/a.html")
    assert not (tmp_path / "views/a.html").exists()
