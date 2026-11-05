"""`CMS_BASE_URL` is the CMS's host, whatever address was pasted into it.

The CMS serves everything under one context path - `/cms/api`, `/cms/debug`,
`/cms/manage` - and the client appends that path itself. So the value a browser
shows, `http://host/cms`, is not the value this setting wants, and getting it
wrong does not fail honestly: the request goes to `/cms/cms/api`, nginx answers
with a portal page and HTTP 200, and the module reports a broken CMS.
"""

import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("http://192.168.56.1", "http://192.168.56.1"),
        ("http://192.168.56.1/", "http://192.168.56.1"),
        ("https://cms.example.com/cms", "https://cms.example.com"),
        ("https://cms.example.com/cms/", "https://cms.example.com"),
        ("https://cms.example.com/cms/api", "https://cms.example.com"),
        ("https://cms.example.com/cms/debug/", "https://cms.example.com"),
        ("  http://host:8080/cms/manage  ", "http://host:8080"),
        ("", ""),
    ],
)
def test_only_the_host_survives(written: str, expected: str) -> None:
    assert Settings(CMS_BASE_URL=written).CMS_BASE_URL == expected


def test_a_path_that_is_not_the_cms_is_left_alone() -> None:
    """A reverse proxy's own prefix is not ours to edit - only `/cms` is."""
    assert Settings(CMS_BASE_URL="https://host/agent").CMS_BASE_URL == "https://host/agent"


def test_a_host_named_cms_is_not_read_as_a_context_path() -> None:
    """`https://cms` ends in the same four characters as the path it may not lose."""
    assert Settings(CMS_BASE_URL="https://cms").CMS_BASE_URL == "https://cms"
    assert Settings(CMS_BASE_URL="https://cms/cms").CMS_BASE_URL == "https://cms"
