"""The one place that talks HTTP to a Unione CMS.

Unione's controllers answer a `Results` envelope - `{success, code, message,
body, total, page}` - and they answer it with HTTP 200 even when `success` is
false. A client that trusts the status code therefore reports success for a
refusal, which in this feature means "deployed" for a resource the CMS did not
accept. Parsing the envelope is not politeness; it is the difference between a
release that happened and one that quietly did not.

The class is deliberately small and stateless beyond its session: one site, one
person's token, and methods that map one-to-one onto the endpoints the console
and the agent both use. Everything that decides *what* to call lives above it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.cms.errors import (
    DRIFT,
    FORBIDDEN,
    NOT_FOUND,
    PUBLISHED,
    TOKEN_EXPIRED,
    UNEXPECTED,
    UNREACHABLE,
    VERIFY_FAILED,
    CmsError,
)

logger = logging.getLogger(__name__)

#: The header the CMS reads its token from (`security.jwt.token`, default
#: `token`). Not `Authorization`: the platform's filter looks for this one by
#: name, and a bearer header is simply ignored - which fails as "not signed in"
#: rather than as "wrong header".
TOKEN_HEADER = "token"

#: The CMS's own context path. Everything the CMS serves hangs off it: the
#: API under `/cms/api`, the admin console under `/cms/manage`, and the debug
#: preview under `/cms/debug`. `CMS_BASE_URL` is therefore the *host*, not the
#: CMS root - a distinction the deployment makes easy to get wrong, because
#: nginx answers `/debug/...` with a portal page and HTTP 200 rather than 404,
#: so a preview built from the host root renders the wrong site convincingly.
API_PREFIX = "/cms/api"
DEBUG_PREFIX = "/cms/debug"


def debug_url(base_url: str, path: str) -> str:
    """Where the CMS renders a draft for the debug console.

    Kept beside `API_PREFIX` because the two are one fact - the CMS's URL
    layout - and splitting them across modules is how they drift apart.
    """
    return f"{base_url.rstrip('/')}{DEBUG_PREFIX}/{path.lstrip('/')}"


#: Phrases the CMS uses for the two refusals a caller can actually act on.
#: Matching on text is fragile, but the controller raises `ServiceException`
#: with these exact sentences and exposes no code, so there is nothing sturdier
#: to match on. A miss degrades to a generic failure, never to a false success.
_PUBLISHED_MARKERS = ("已发布", "请先升级")
_REFUSED_MARKERS = ("无操作权限", "没有权限", "无权")

#: Any redirect from an API endpoint means the request never reached the
#: controller. The deployed CMS answers a dead session with `303 -> /portal/login`
#: (nginx plus the platform's auth filter, which redirects rather than returning
#: 401), so the body is empty and there is no envelope to inspect. Redirects are
#: not followed: doing so would carry the token - and, on the preview proxy, the
#: debug cookie - to a page it was never meant for.
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


@dataclass(frozen=True)
class CmsSiteRecord:
    """A site as the list endpoint reports it."""

    id: int
    title: str
    ctx: str
    domain: str | None = None
    cover_pic: str | None = None
    space_id: int | None = None
    space_vers: int | None = None
    admin_userid: int | None = None
    created_by: int | None = None
    status: int | None = None

    @property
    def address(self) -> str:
        """Where this site answers: its own domain if it has one, else its ctx."""
        return f"https://{self.domain}" if self.domain else self.ctx


@dataclass(frozen=True)
class CmsResourceRecord:
    """One row of the resource tree - a directory, a template or an asset."""

    id: int
    name: str
    types: str
    status: int | None = None
    vers: int | None = None
    site_id: int | None = None
    space_id: int | None = None
    parent_id: int | None = None
    suffix: str | None = None

    @property
    def is_text(self) -> bool:
        return self.types == "text"

    @property
    def is_dir(self) -> bool:
        return self.types == "dir"


def _int(value: Any) -> int | None:
    """A JSON number or numeric string, or None. The CMS sends both."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class CmsClient:
    """A CMS, as seen by one signed-in person.

    `transport` exists for tests; production passes nothing and gets the real
    network. The token is never logged, never put in an exception message, and
    never returned from a method - a caller that needs it has the session.
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            transport=transport,
            headers={TOKEN_HEADER: token, "accept": "application/json"},
            follow_redirects=False,
        )

    async def __aenter__(self) -> CmsClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- requests ---------------------------------------------------------

    def _raise_for_status(self, response: httpx.Response, *, context: str) -> None:
        """Turn a transport-level failure into the code that says what to do."""
        status = response.status_code
        if status in _REDIRECT_STATUSES:
            raise CmsError(
                TOKEN_EXPIRED,
                f"{context}: 请求被重定向（{status}），通常是登录态已失效，请重新登录该 CMS",
                status=status,
            )
        if status in (401, 403):
            code = TOKEN_EXPIRED if status == 401 else FORBIDDEN
            raise CmsError(code, _message_of(response) or f"{context}: {status}", status=status)
        if status == 404:
            raise CmsError(NOT_FOUND, _message_of(response) or f"{context}: not found", status=404)
        if status >= 500:
            raise CmsError(
                UNEXPECTED, _message_of(response) or f"{context}: the CMS failed ({status})", status=status
            )
        if status >= 400:
            raise CmsError(
                UNEXPECTED, _message_of(response) or f"{context}: refused ({status})", status=status
            )

    async def _send(self, method: str, path: str, *, context: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            # The address, not the request: the request carries the token, and
            # httpx prints the whole URL in some of its messages.
            raise CmsError(
                UNREACHABLE, f"{context}: the CMS did not answer at {self._base_url}"
            ) from exc
        self._raise_for_status(response, context=context)
        return response

    async def _payload(self, method: str, path: str, *, context: str, **kwargs: Any) -> Any:
        """Send and return the whole envelope, refusal included.

        Split out from :meth:`_envelope` because one caller - `verify` - has
        something useful to say about the refusal's `body`, and a helper that
        raised before it could look would throw that away.
        """
        response = await self._send(method, path, context=context, **kwargs)
        try:
            return response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CmsError(
                TOKEN_EXPIRED if _looks_like_html(response) else UNEXPECTED,
                f"{context}: the CMS answered something that is not JSON",
            ) from exc

    async def _envelope(self, method: str, path: str, *, context: str, **kwargs: Any) -> Any:
        """Send, then unwrap `Results`, raising the CMS's own refusal message."""
        payload = await self._payload(method, path, context=context, **kwargs)
        if _is_envelope(payload):
            if payload.get("success") is False:
                raise _refusal(payload, context=context)
            return payload.get("body")
        # Some endpoints (the Feign helpers) answer the bare value.
        return payload

    # -- sites ------------------------------------------------------------

    async def find_sites(
        self, *, keywords: str | None = None, page: int = 1, page_size: int = 50
    ) -> list[CmsSiteRecord]:
        """The sites this person's tenant can see, one page at a time.

        `body: {}` is sent because the controller asserts it is present; the
        `delFlag` filter is applied there, not here.
        """
        payload: dict[str, Any] = {"page": page, "pageSize": page_size, "body": {}}
        if keywords:
            # `keywords` sits on `Params`, beside `body` - the CMS's SQL builder
            # reads it there and folds it into the where-clause itself.
            payload["keywords"] = keywords
        rows = await self._envelope(
            "POST", f"{API_PREFIX}/site/find", context="find_sites", json=payload
        )
        return [_site(row) for row in rows or []]

    async def site_detail(self, site_id: int) -> CmsSiteRecord:
        body = await self._envelope(
            "GET", f"{API_PREFIX}/site/detail", context="site_detail", params={"id": site_id}
        )
        if not body:
            raise CmsError(NOT_FOUND, f"site {site_id} is not in this tenant")
        return _site(body)

    # -- resources --------------------------------------------------------

    async def find_resources(self, site_id: int, *, page_size: int = 2000) -> list[CmsResourceRecord]:
        """Every resource of a site, as the resource tree lists them."""
        rows = await self._envelope(
            "POST",
            f"{API_PREFIX}/resource/find",
            context="find_resources",
            json={"page": 1, "pageSize": page_size, "body": {"siteId": site_id}},
        )
        return [_resource(row) for row in rows or []]

    async def export_site(self, site_id: int) -> bytes:
        """The site's code as a ZIP.

        A login page is a 200 with HTML, so the content type is checked instead
        of trusted: an HTML body here means the session is gone, and handing it
        to the unzipper would fail somewhere far from the cause.
        """
        response = await self._send(
            "GET", f"{API_PREFIX}/resource/export/site/{site_id}", context="export_site"
        )
        if _looks_like_html(response):
            raise CmsError(TOKEN_EXPIRED, "export_site: the CMS answered a login page")
        return response.content

    async def save_resource(
        self,
        *,
        site_id: int,
        space_id: int | None,
        name: str,
        types: str,
        body_text: str | None = None,
        title: str | None = None,
        resource_id: int | None = None,
    ) -> int:
        """Create or update one resource, as a draft.

        Sent as a form rather than JSON because the controller binds
        `CmsResource` field-by-field (`@Validated` on a model attribute, with an
        optional `file` part). A JSON body reaches it as an empty entity and
        fails validation with "资源名称不能为空", which reads like a client bug
        and is really a content-type one.
        """
        form: dict[str, Any] = {
            "siteId": str(site_id),
            "name": name,
            "title": title or name.rsplit("/", 1)[-1] or name,
            "types": types,
        }
        if space_id is not None:
            form["spaceId"] = str(space_id)
        if body_text is not None:
            form["bodyText"] = body_text
        if resource_id is not None:
            form["id"] = str(resource_id)

        body = await self._envelope(
            "POST", f"{API_PREFIX}/resource/save", context="save_resource", data=form
        )
        saved = _int(body)
        if saved is None:
            raise CmsError(UNEXPECTED, "save_resource: the CMS did not return the resource id")
        return saved

    async def upgrade(self, resource_id: int) -> int:
        """Copy a published resource back into the editable state."""
        body = await self._envelope(
            "POST", f"{API_PREFIX}/resource/upgrade", context="upgrade", json=resource_id
        )
        return _int(body) or 0

    async def verify(
        self,
        *,
        site_id: int,
        name: str,
        body_text: str,
        resource_id: int | None = None,
    ) -> None:
        """Beetl-check a template without saving it.

        With an `id` the CMS checks the stored resource, optionally overridden
        by the body sent; without one it builds a throwaway resource from
        `siteId`/`name`/`bodyText`, which is what lets a file that has never
        been saved be checked before it exists. The CMS answers a parse failure
        as `success: false` with a `BeetlException` in `body`.
        """
        payload: dict[str, Any] = {
            "siteId": site_id,
            "name": name,
            "bodyText": body_text,
            "types": "text",
        }
        if resource_id is not None:
            payload["id"] = resource_id
        answer = await self._payload(
            "POST", f"{API_PREFIX}/resource/verify", context="verify", json=payload
        )
        if not _is_envelope(answer) or answer.get("success") is not False:
            return
        refusal = _refusal(answer, context="verify")
        if refusal.code in (TOKEN_EXPIRED, FORBIDDEN):
            raise refusal
        raise CmsError(VERIFY_FAILED, _parser_detail(answer, fallback=refusal.message))

    async def release_all(self, site_id: int) -> int:
        """Publish every unpublished resource of a site. Returns how many."""
        body = await self._envelope(
            "POST", f"{API_PREFIX}/resource/release/all", context="release_all", json=site_id
        )
        return _int(body) or 0

    async def delete_resources(self, ids: list[int]) -> int:
        """Delete resources by id. Returns how many the CMS removed."""
        if not ids:
            return 0
        body = await self._envelope(
            "POST", f"{API_PREFIX}/resource/delete", context="delete_resources", json=ids
        )
        return _int(body) or 0

    async def build_html(self, site_ids: list[int]) -> None:
        """Regenerate the static pages, for sites that serve them.

        Optional by design: a site with static generation off answers fine
        without it, and a release that has already been published must not be
        reported as failed because the optional warm-up did.
        """
        await self._envelope(
            "POST", f"{API_PREFIX}/site/build/html", context="build_html", json=site_ids
        )

    async def is_authed(self) -> bool:
        """Whether the token is still good, without asking for anything else."""
        try:
            await self._envelope("GET", f"{API_PREFIX}/security/isAuthed", context="is_authed")
        except CmsError as exc:
            if exc.code in (TOKEN_EXPIRED, FORBIDDEN):
                return False
            raise
        return True


def _site(row: dict[str, Any]) -> CmsSiteRecord:
    return CmsSiteRecord(
        id=_int(row.get("id")) or 0,
        title=str(row.get("title") or ""),
        ctx=str(row.get("ctx") or ""),
        domain=row.get("domain") or None,
        cover_pic=row.get("coverPic") or None,
        space_id=_int(row.get("spaceId")),
        space_vers=_int(row.get("spaceVers")),
        admin_userid=_int(row.get("adminUserid")),
        created_by=_int(row.get("createdBy")),
        status=_int(row.get("status")),
    )


def _resource(row: dict[str, Any]) -> CmsResourceRecord:
    return CmsResourceRecord(
        id=_int(row.get("id")) or 0,
        name=str(row.get("name") or ""),
        types=str(row.get("types") or ""),
        status=_int(row.get("status")),
        vers=_int(row.get("vers")),
        site_id=_int(row.get("siteId")),
        space_id=_int(row.get("spaceId")),
        parent_id=_int(row.get("parentId")),
        suffix=row.get("suffix") or None,
    )


def _message_of(response: httpx.Response) -> str:
    """The CMS's own sentence, if it wrote one. Never the raw body."""
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return ""
    if isinstance(payload, dict):
        return str(payload.get("message") or "")
    return ""


def _is_envelope(payload: Any) -> bool:
    """Whether the CMS answered its `Results` wrapper rather than a bare value."""
    return isinstance(payload, dict) and "success" in payload


def _parser_detail(payload: dict[str, Any], *, fallback: str) -> str:
    """A Beetl failure, with the line and token the parser named.

    The exception is serialised by Jackson from a third-party class, so the
    field names are theirs (`line`, `token`, `msg`), not ours. Read what is
    there and keep the original sentence either way - a message without a line
    is still better than "verify failed".
    """
    detail = payload.get("body")
    parts = [fallback]
    if isinstance(detail, dict):
        line = detail.get("line")
        message = detail.get("message") or detail.get("msg") or detail.get("token")
        if line is not None:
            parts.append(f"line {line}")
        if message:
            parts.append(str(message))
    elif isinstance(detail, str) and detail:
        parts.append(detail)
    return ": ".join(parts)


def _looks_like_html(response: httpx.Response) -> bool:
    content_type = response.headers.get("content-type", "")
    return "text/html" in content_type or response.content[:1] == b"<"


def _refusal(payload: dict[str, Any], *, context: str) -> CmsError:
    """Classify a `success: false` by what the caller can do about it."""
    message = str(payload.get("message") or "")
    status = _int(payload.get("code"))
    if any(marker in message for marker in _PUBLISHED_MARKERS):
        return CmsError(PUBLISHED, message, status=status)
    if any(marker in message for marker in _REFUSED_MARKERS):
        return CmsError(FORBIDDEN, message, status=status)
    if "登录" in message or "token" in message.lower():
        return CmsError(TOKEN_EXPIRED, message, status=status)
    if "模版" in message or "模板" in message:
        # `verify` re-raises this as VERIFY_FAILED with the parser's line.
        return CmsError(VERIFY_FAILED, message, status=status)
    if "版本" in message or "已被修改" in message:
        return CmsError(DRIFT, message, status=status)
    return CmsError(UNEXPECTED, message or f"{context}: the CMS refused", status=status)
