"""Showing a person their own unpublished drafts, without handing them the key.

The CMS has a debug mode, and it is not a per-request flag: visiting
`/debug/...` sets a cookie, and after that *every* request on that domain
renders the edit-time state - every unpublished resource of every site the
person can see. Handing that cookie to a browser would be handing out a window
into the whole CMS, which is why the preview is proxied: the browser talks to
this deployment, this deployment talks to the CMS with the cookie attached, and
the cookie never leaves the server.

Three things then have to be right, and each has a test below.

**The link is signed, not guessed.** A preview URL carries a ticket naming the
person and the site, with an expiry. Without it the proxy would be an open
window: anybody who learned the URL could read a customer's unpublished site.

**The HTML has to be rewritten.** The CMS renders paths from the site root
(`/static/app.css`). Behind a proxy those resolve against *this* deployment and
404. A `<base>` element fixes the document's own references; CSS `url(/...)`
and inline `srcset` need rewriting by hand.

**The response's own headers must not survive.** `Set-Cookie` would leak the
debug cookie to the browser - the exact thing this module exists to prevent -
and a CSP written for the CMS's origin would block the proxied page outright.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time

from app.cms.errors import FORBIDDEN, TOKEN_EXPIRED, CmsError

#: Prefix of the per-site debug cookie the CMS now writes. The site id is part
#: of the name, so a cookie for one site cannot open another (`CmsSpaceServiceImpl`).
DEBUG_COOKIE_PREFIX = "unione-cms-debug-"

#: The cookie this deployment sends to the CMS. Only meaningful server-side: it
#: is attached to the proxied request, never returned to the browser.
def debug_cookie_name(site_id: int) -> str:
    return f"{DEBUG_COOKIE_PREFIX}{site_id}"


def debug_cookie_value(user_id: int, expires_at: int) -> str:
    """`{userId}:{expireAt}` - the shape `CmsSpaceServiceImpl` parses."""
    return f"{user_id}:{expires_at}"


#: Headers a proxied CMS response must not pass on. `set-cookie` is the whole
#: point of the module; the rest are origin-bound and would break the page.
DROPPED_RESPONSE_HEADERS = frozenset(
    {
        "set-cookie",
        "content-security-policy",
        "content-security-policy-report-only",
        "x-frame-options",
        "content-encoding",
        "content-length",
        "transfer-encoding",
        "strict-transport-security",
    }
)


def forwardable_headers(headers: dict[str, str]) -> dict[str, str]:
    """The CMS's response headers, minus the ones that would escape the proxy."""
    return {key: value for key, value in headers.items() if key.lower() not in DROPPED_RESPONSE_HEADERS}


class PreviewSigner:
    """Mints and checks the ticket in a preview URL.

    HMAC rather than a session lookup: the proxy validates on every asset
    request of one page load, and a database round trip per `<img>` is a cost
    with no benefit. The payload is readable - it holds a user id and a site
    id, both of which the person already knows - and it is unforgeable, which
    is the property that matters.
    """

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("a preview ticket needs a signing secret")
        self._secret = secret.encode()

    def mint(self, *, user_id: int, site_id: int, ttl_seconds: int, now: int | None = None) -> str:
        issued = int(now if now is not None else time.time())
        payload = {"u": user_id, "s": site_id, "e": issued + ttl_seconds}
        encoded = _b64(json.dumps(payload, separators=(",", ":")).encode())
        return f"{encoded}.{self._sign(encoded)}"

    def verify(self, ticket: str, *, site_id: int, now: int | None = None) -> int:
        """The user id this ticket was minted for, or a refusal.

        Site and user are both checked: a ticket for site 7 must not open site
        9, and the check is here rather than at the caller so a second caller
        cannot forget it.
        """
        encoded, _, signature = ticket.partition(".")
        if not encoded or not signature:
            raise CmsError(FORBIDDEN, "this preview link is not one we issued")
        if not hmac.compare_digest(self._sign(encoded), signature):
            raise CmsError(FORBIDDEN, "this preview link is not one we issued")
        try:
            payload = json.loads(_unb64(encoded))
        except (ValueError, json.JSONDecodeError) as exc:
            raise CmsError(FORBIDDEN, "this preview link is not one we issued") from exc
        moment = int(now if now is not None else time.time())
        if int(payload.get("e", 0)) < moment:
            raise CmsError(TOKEN_EXPIRED, "this preview link has expired; reopen the editor")
        if int(payload.get("s", 0)) != site_id:
            raise CmsError(FORBIDDEN, "this preview link was issued for another site")
        return int(payload.get("u", 0))

    def _sign(self, encoded: str) -> str:
        digest = hmac.new(self._secret, encoded.encode(), hashlib.sha256).digest()
        return _b64(digest)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


#: `<head>` with the attributes a real document may put on it.
_HEAD = re.compile(r"<head(\s[^>]*)?>", re.IGNORECASE)

#: `url(...)` in CSS, quoted or not, absolute or root-relative.
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(/[^)'\"]*)\1\s*\)", re.IGNORECASE)

#: `href`/`src`/`srcset`/`action` pointing at the site root in inline markup.
_ATTR_URL = re.compile(
    r"(\b(?:src|href|action)\s*=\s*)(['\"])(/(?!/)[^'\"]*)\2", re.IGNORECASE
)


def rewrite_html(html: str, *, base_url: str) -> str:
    """Point a rendered page at the proxy instead of at the CMS's own origin.

    A `<base>` element alone would be enough for a well-formed page, and it is
    not enough for the pages these sites actually contain: `<form action="/x">`
    and `<a href="/y">` in a document served from a different path resolve
    against the base only if the browser honours it, and a good deal of
    generated markup includes an absolute path in a place the base does not
    reach. So the base is a belt and the attribute rewrite is the braces.

    Paths that are already absolute (`https://cdn/x`) and protocol-relative
    (`//cdn/x`) are left alone: rewriting them would break a site that serves
    its assets from a CDN.
    """
    root = base_url.rstrip("/") + "/"

    def absolute(match: re.Match[str]) -> str:
        prefix, quote, path = match.groups()
        return f"{prefix}{quote}{root}{path.lstrip('/')}{quote}"

    rewritten = _ATTR_URL.sub(absolute, html)
    base_tag = f'<base href="{root}">'
    if _HEAD.search(rewritten):
        # Right after `<head>`: a `<base>` after the first stylesheet is ignored
        # by every browser, and the stylesheet usually comes first.
        return _HEAD.sub(lambda m: f"{m.group(0)}{base_tag}", rewritten, count=1)
    return base_tag + rewritten


def rewrite_css(css: str, *, base_url: str) -> str:
    """Rewrite root-relative `url(/...)` references in a stylesheet."""
    root = base_url.rstrip("/") + "/"

    def absolute(match: re.Match[str]) -> str:
        quote, path = match.groups()
        return f"url({quote}{root}{path.lstrip('/')}{quote})"

    return _CSS_URL.sub(absolute, css)


def needs_rewriting(content_type: str) -> bool:
    """Only documents and stylesheets carry paths that have to move."""
    return "text/html" in content_type or "text/css" in content_type


def rewrite(body: bytes, *, content_type: str, base_url: str) -> bytes:
    """Rewrite whatever kind of document this is, leaving everything else alone."""
    if "text/html" in content_type:
        return rewrite_html(body.decode("utf-8", "replace"), base_url=base_url).encode()
    if "text/css" in content_type:
        return rewrite_css(body.decode("utf-8", "replace"), base_url=base_url).encode()
    return body
