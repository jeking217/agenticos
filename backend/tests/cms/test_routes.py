"""The route layer's own rules, tested where they live.

Everything with business meaning is tested through the service and the client,
which need no FastAPI app. What is left here is the handful of decisions the
routes make *before* calling either - the ones that turn a header into a URL a
browser will follow, and so the ones where being trusting is a security bug
rather than a convenience.
"""

from __future__ import annotations

from starlette.requests import Request

from app.api.routes.v1.cms import _preview_prefix


def request_with(headers: dict[str, str] | None = None) -> Request:
    """A bare ASGI request carrying only the headers under test."""
    raw = [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


def test_a_mounted_console_contributes_its_segment():
    assert _preview_prefix(request_with({"x-forwarded-prefix": "/agent/web"})) == "/agent/web"


def test_a_trailing_slash_is_not_doubled_in_the_link():
    assert _preview_prefix(request_with({"x-forwarded-prefix": "/agent/web/"})) == "/agent/web"


def test_no_header_means_the_deployment_root():
    assert _preview_prefix(request_with()) == ""


def test_a_protocol_relative_prefix_is_refused():
    """`//evil.example` is a valid URL that leaves the site.

    The value ends up in an iframe's `src` and in a `<base href>`, so a header
    that can write `//` there can point the preview - and every relative asset in
    it - at a host of its choosing. Ignoring it costs nothing: without a prefix
    the link resolves to this deployment's root, which is wrong but harmless.
    """
    assert _preview_prefix(request_with({"x-forwarded-prefix": "//evil.example"})) == ""


def test_a_prefix_that_is_not_rooted_is_refused():
    assert _preview_prefix(request_with({"x-forwarded-prefix": "agent/web"})) == ""
