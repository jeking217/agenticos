"""The preview is the feature's sharpest edge, so it gets the sharpest tests.

Three properties, each of which is a way the preview could become something
worse than a preview: a link that anyone can mint, a `<base>` that only half
works, and a `Set-Cookie` that hands the browser the CMS's own debug session.
"""

from __future__ import annotations

import pytest

from app.cms.errors import FORBIDDEN, TOKEN_EXPIRED, CmsError
from app.cms.preview import (
    debug_cookie_name,
    debug_cookie_value,
    forwardable_headers,
    needs_rewriting,
    PreviewSigner,
    rewrite_css,
    rewrite_html,
)

SECRET = "preview-secret"


@pytest.fixture
def signer() -> PreviewSigner:
    return PreviewSigner(SECRET)


def test_a_ticket_names_one_person_and_one_site(signer):
    ticket = signer.mint(user_id=42, site_id=7, ttl_seconds=600, now=1_000)

    assert signer.verify(ticket, site_id=7, now=1_100) == 42


def test_a_ticket_for_another_site_is_refused(signer):
    ticket = signer.mint(user_id=42, site_id=7, ttl_seconds=600, now=1_000)

    with pytest.raises(CmsError) as caught:
        signer.verify(ticket, site_id=9, now=1_100)

    assert caught.value.code == FORBIDDEN


def test_an_expired_ticket_says_so_rather_than_forbidding(signer):
    """"Expired" and "not yours" are different instructions to the console."""
    ticket = signer.mint(user_id=42, site_id=7, ttl_seconds=60, now=1_000)

    with pytest.raises(CmsError) as caught:
        signer.verify(ticket, site_id=7, now=1_061)

    assert caught.value.code == TOKEN_EXPIRED


def test_a_tampered_payload_does_not_verify_even_with_the_original_signature(signer):
    ticket = signer.mint(user_id=42, site_id=7, ttl_seconds=600, now=1_000)
    encoded, _, signature = ticket.partition(".")

    with pytest.raises(CmsError):
        signer.verify(f"{encoded[:-2]}xx.{signature}", site_id=7, now=1_100)


def test_another_deployments_secret_cannot_mint_a_usable_ticket(signer):
    foreign = PreviewSigner("somebody-elses-secret")
    ticket = foreign.mint(user_id=42, site_id=7, ttl_seconds=600, now=1_000)

    with pytest.raises(CmsError):
        signer.verify(ticket, site_id=7, now=1_100)


def test_a_ticket_without_a_signature_is_not_a_ticket(signer):
    with pytest.raises(CmsError):
        signer.verify("just-some-base64", site_id=7)


def test_an_empty_secret_is_refused_at_construction():
    with pytest.raises(ValueError):
        PreviewSigner("")


def test_the_debug_cookie_is_the_one_the_cms_writes_and_parses():
    assert debug_cookie_name(7) == "unione-cms-debug-7"
    assert debug_cookie_value(42, 1_700_000_000) == "42:1700000000"


def test_html_gets_a_base_element_first_in_head():
    html = "<html><head><link rel=stylesheet href=/static/app.css></head><body>x</body></html>"

    out = rewrite_html(html, base_url="/cms/preview/t")

    assert out.index("<base") < out.index("<link"), "a base after a link is ignored"
    assert '<base href="/cms/preview/t/">' in out


def test_root_relative_attributes_are_rewritten_and_absolute_ones_are_not():
    html = (
        '<a href="/about.html">about</a>'
        '<img src="/static/logo.png">'
        '<script src="https://cdn.example.com/x.js"></script>'
        '<img src="//cdn.example.com/y.png">'
        '<form action="/search"></form>'
    )

    out = rewrite_html(html, base_url="/cms/preview/t")

    assert 'href="/cms/preview/t/about.html"' in out
    assert 'src="/cms/preview/t/static/logo.png"' in out
    assert 'action="/cms/preview/t/search"' in out
    assert "https://cdn.example.com/x.js" in out
    assert "//cdn.example.com/y.png" in out


def test_a_fragment_keeps_its_place():
    out = rewrite_html('<a href="/p.html#top">p</a>', base_url="/cms/preview/t")

    assert 'href="/cms/preview/t/p.html#top"' in out


def test_a_document_without_head_still_gets_a_base():
    out = rewrite_html("<h1>bare</h1>", base_url="/cms/preview/t")

    assert out.startswith("<base href=\"/cms/preview/t/\">")


def test_css_urls_are_rewritten_and_data_uris_are_not():
    css = "@font-face{src:url(/static/f.woff2)} .a{background:url('/static/b.png')} .b{background:url(data:image/png;base64,AA)}"

    out = rewrite_css(css, base_url="/cms/preview/t")

    assert "url(/cms/preview/t/static/f.woff2)" in out
    assert "url('/cms/preview/t/static/b.png')" in out
    assert "url(data:image/png;base64,AA)" in out


def test_only_documents_and_stylesheets_are_rewritten():
    assert needs_rewriting("text/html; charset=utf-8")
    assert needs_rewriting("text/css")
    assert not needs_rewriting("image/png")
    assert not needs_rewriting("application/javascript")


def test_the_debug_cookie_never_reaches_the_browser():
    headers = {
        "content-type": "text/html",
        "Set-Cookie": "unione-cms-debug-7=42:1700000000; HttpOnly",
        "Content-Security-Policy": "default-src 'self'",
        "etag": "abc",
    }

    forwarded = forwardable_headers(headers)

    assert "set-cookie" not in {k.lower() for k in forwarded}
    assert "content-security-policy" not in {k.lower() for k in forwarded}
    assert forwarded["etag"] == "abc"
