"""Web apps: the console's API over the CMS.

The shape of each route is the flow's: `pull` then `diff` then `verify` then
`push` then `release`. They are separate routes rather than one `sync` because the
console shows the diff before anything is written, and because an approval gate
can only sit between two calls.

Two of them deserve a note.

**`/release` requires `confirmed`.** The CMS's `release/all` publishes every
unpublished resource of the site, including a colleague's half-finished draft.
The route refuses without the person's own click, and returns the sheet it
would have shown (`blockers`, `foreignDrafts`) so a client that skipped the
sheet can still show one.

**`/preview` proxies, it does not redirect.** The CMS's debug mode is a cookie
on its own domain that renders every unpublished resource the person can see.
Handing that cookie to a browser would hand out the CMS; so the proxy attaches
it server-side, strips `Set-Cookie` from the response, and rewrites root
relative paths so the page still loads its own assets.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import Response

from app.api.deps import Auth, CurrentUser, DBSession
from app.cms.client import debug_url
from app.cms.errors import CmsError
from app.cms.preview import debug_cookie_name, debug_cookie_value, forwardable_headers, rewrite
from app.cms.service import PREVIEW_PATH
from app.core.config import settings
from app.schemas.cms import (
    BindSiteRequest,
    CmsStatusRead,
    DiffRead,
    DiscardRead,
    DiscardRequest,
    FileRead,
    PreviewRead,
    PullRead,
    PushRead,
    ReleasePlanRead,
    ReleaseRead,
    ReleaseRequest,
    RollbackRead,
    RollbackRequest,
    SiteListRead,
    SuggestionRead,
    VerifyRead,
    WorkspaceRead,
)
from app.services.cms_sites import CmsSitesService, platform_identity

router = APIRouter()

#: How the CMS's refusal codes become HTTP codes. The body always carries the
#: CMS's own `code` as well, because the console keys on that - a 409 covers
#: "published", "drifted" and "not confirmed", and only one of them means
#: "reload the page".
_STATUS_FOR = {
    "cms_token_expired": status.HTTP_401_UNAUTHORIZED,
    "cms_forbidden": status.HTTP_403_FORBIDDEN,
    "cms_not_found": status.HTTP_404_NOT_FOUND,
    "cms_resource_published": status.HTTP_409_CONFLICT,
    "cms_drift": status.HTTP_409_CONFLICT,
    "cms_path_rejected": status.HTTP_400_BAD_REQUEST,
    "cms_verify_failed": 422,
}


def _raise(exc: CmsError) -> None:
    raise HTTPException(
        status_code=_STATUS_FOR.get(exc.code, status.HTTP_502_BAD_GATEWAY),
        detail={"code": exc.code, "message": exc.message},
    )


def _service(db) -> CmsSitesService:
    return CmsSitesService(db)


@router.get("/status", response_model=CmsStatusRead)
async def module_status(user: CurrentUser, db: DBSession) -> Any:
    """Whether the module may be opened, so the sidebar knows before it draws."""
    service = _service(db)
    access = service.access_for(user)
    return CmsStatusRead(
        configured=access.configured,
        platform_linked=access.platform_linked,
        can_use=access.can_use,
        has_token=await service.has_token(user),
        reason=access.reason,
    )


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
async def forget_session(user: CurrentUser, db: DBSession) -> Response:
    """Drop the stored token - on sign-out, or when the CMS has stopped accepting it.

    There is no matching `POST`: the token is kept by the sign-in that obtained it
    (see `app/api/routes/v1/unione_auth.py`), so a person never copies one by hand
    and this route only ever removes one.
    """
    await _service(db).forget_token(user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sites", response_model=SiteListRead)
async def list_sites(user: CurrentUser, db: DBSession) -> Any:
    """The sites this person may open, with `editable` already decided."""
    service = _service(db)
    try:
        cards = await service.list_sites(user)
    except CmsError as exc:
        _raise(exc)
    return SiteListRead(sites=[card.to_json() for card in cards])


@router.post("/sites/{site_id}/bind", status_code=status.HTTP_204_NO_CONTENT)
async def bind_site(site_id: int, body: BindSiteRequest, ctx: Auth, user: CurrentUser, db: DBSession) -> Response:
    """Remember that this person opened this site.

    `site_id` comes from the path and is checked against the body: a client that
    disagrees with itself is refused rather than silently recorded twice.
    """
    if body.site_id != site_id:
        raise HTTPException(status_code=400, detail="site_id 与请求体不一致")
    service = _service(db)
    cards = [card for card in await service.list_sites(user) if card.id == site_id]
    if not cards:
        raise HTTPException(status_code=404, detail="站点不在可见范围内")
    await service.bind(user, ctx.organization_id, cards[0])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sites/{site_id}/workspace", response_model=WorkspaceRead)
async def workspace(site_id: int, user: CurrentUser, db: DBSession) -> Any:
    """The tree the editor draws, before anything has changed."""
    service = _service(db)
    try:
        space = service.workspace_for(user, site_id)
        from app.cms.archive import read_workspace

        files = sorted(read_workspace(space.root, allowed_ext=_extensions()))
        return WorkspaceRead(
            files=files,
            branch=await space.current_branch(),
            releases=await space.releases(),
        )
    except CmsError as exc:
        _raise(exc)


@router.get("/sites/{site_id}/file", response_model=FileRead)
async def read_file(
    site_id: int, user: CurrentUser, db: DBSession, path: str = Query(..., max_length=500)
) -> Any:
    """One workspace file's text, for the editor pane.

    Read from the workspace rather than from the CMS: what the person edits is
    what a push would send, and a pull may have updated the file since the CMS
    last saw it.
    """
    try:
        flow = await _service(db).flow(user, site_id)
        return FileRead(path=path, text=flow.read_file(path))
    except CmsError as exc:
        _raise(exc)


@router.post("/sites/{site_id}/pull", response_model=PullRead)
async def pull(site_id: int, user: CurrentUser, db: DBSession) -> Any:
    """Fetch the site's code and lay down the baseline."""
    try:
        flow = await _service(db).flow(user, site_id)
        result = await flow.pull()
    except CmsError as exc:
        _raise(exc)
    return PullRead(**result.to_json())


@router.get("/sites/{site_id}/diff", response_model=DiffRead)
async def diff(site_id: int, user: CurrentUser, db: DBSession) -> Any:
    """What changed in the workspace, and what a push would send."""
    try:
        flow = await _service(db).flow(user, site_id)
        plan = await flow.diff()
    except CmsError as exc:
        _raise(exc)
    return DiffRead(**plan.to_json())


@router.post("/sites/{site_id}/verify", response_model=VerifyRead)
async def verify(site_id: int, user: CurrentUser, db: DBSession) -> Any:
    """Beetl-check the changed templates. Nothing is saved."""
    try:
        flow = await _service(db).flow(user, site_id)
        report = await flow.verify()
    except CmsError as exc:
        _raise(exc)
    return VerifyRead(**report.to_json())


@router.post("/sites/{site_id}/push", response_model=PushRead)
async def push(site_id: int, user: CurrentUser, db: DBSession) -> Any:
    """Save the changed files as drafts - visible in the preview, not on the site."""
    try:
        flow = await _service(db).flow(user, site_id)
        report = await flow.push()
    except CmsError as exc:
        _raise(exc)
    return PushRead(**report.to_json())


@router.post("/sites/{site_id}/discard", response_model=DiscardRead)
async def discard(site_id: int, body: DiscardRequest, user: CurrentUser, db: DBSession) -> Any:
    """Undo the edits to these files, back to what is live.

    Not a rollback: the site keeps everything else. A file that was already
    pushed has its draft taken back to the published content, because
    `release/all` publishes every draft of the site - leaving one behind would
    ship the edit the person just undid.
    """
    try:
        flow = await _service(db).flow(user, site_id)
        report = await flow.discard(body.paths)
    except CmsError as exc:
        _raise(exc)
    return DiscardRead(**report.to_json())


@router.get("/sites/{site_id}/release/plan", response_model=ReleasePlanRead)
async def release_plan(site_id: int, user: CurrentUser, db: DBSession) -> Any:
    """The confirmation sheet, without publishing anything."""
    try:
        flow = await _service(db).flow(user, site_id)
        plan = await flow.plan_release(confirmed=False)
    except CmsError as exc:
        _raise(exc)
    return ReleasePlanRead(**plan.to_json())


@router.post("/sites/{site_id}/release", response_model=ReleaseRead)
async def release(site_id: int, body: ReleaseRequest, user: CurrentUser, db: DBSession) -> Any:
    """Publish the drafts, if every guard passes and the person confirmed."""
    try:
        flow = await _service(db).flow(user, site_id)
        result = await flow.release(
            confirmed=body.confirmed, foreign_drafts=body.foreign_drafts
        )
    except CmsError as exc:
        _raise(exc)
    return ReleaseRead(**result)


@router.post("/sites/{site_id}/rollback", response_model=RollbackRead)
async def rollback(site_id: int, body: RollbackRequest, user: CurrentUser, db: DBSession) -> Any:
    """Restore a release and publish it again."""
    try:
        flow = await _service(db).flow(user, site_id)
        result = await flow.rollback(body.to)
    except CmsError as exc:
        _raise(exc)
    if not result.get("ok", False):
        raise HTTPException(status_code=409, detail=result)
    return RollbackRead(**result)


@router.get("/sites/{site_id}/suggestions", response_model=list[SuggestionRead])
async def suggestions(site_id: int, user: CurrentUser, db: DBSession) -> Any:
    """The chips to offer now: fix, diff, preview or publish.

    Computed from the workspace's state rather than remembered from the last
    agent run, so a person who edited a file by hand gets the same reminder the
    agent would have produced.
    """
    service = _service(db)
    try:
        flow = await service.flow(user, site_id)
        plan = await flow.diff()
        if plan.empty:
            return [SuggestionRead(**chip.to_json()) for chip in service.suggestions_after_pull(site_id, 0, None)]
        report = await flow.verify()
        chips = service.suggestions_after_verify(
            site_id, plan=plan, report=report, preview_url=flow.preview_url("/")
        )
    except CmsError as exc:
        _raise(exc)
    return [SuggestionRead(**chip.to_json()) for chip in chips]


@router.get("/sites/{site_id}/preview-url", response_model=PreviewRead)
async def preview_url(
    site_id: int, request: Request, user: CurrentUser, db: DBSession, path: str = Query("/", max_length=500)
) -> Any:
    """A signed link to the CMS's edit-time rendering of this site."""
    try:
        flow = await _service(db).flow(user, site_id)
        return PreviewRead(url=flow.preview_url(path, prefix=_preview_prefix(request)))
    except CmsError as exc:
        _raise(exc)


@router.api_route("/preview/{ticket}/{path:path}", methods=["GET"])
async def preview_proxy(ticket: str, path: str, request: Request, user: CurrentUser) -> Response:
    """Serve the CMS's debug rendering, with the cookie kept server-side.

    The ticket is verified here rather than by a dependency: it names the site,
    and the site is what decides which cookie to send. `site_id` travels in the
    ticket, so a link for site 7 cannot be replayed against site 9.
    """
    if not settings.CMS_BASE_URL:
        raise HTTPException(status_code=503, detail={"code": "cms_unreachable", "message": "CMS 未配置"})

    from app.cms.preview import PreviewSigner

    signer = PreviewSigner(settings.CMS_PREVIEW_SECRET or settings.SECRET_KEY)
    site_id = _site_from_ticket(ticket, path)
    if site_id is None:
        raise HTTPException(status_code=404, detail={"code": "cms_not_found", "message": "预览链接无效"})
    try:
        signer.verify(ticket, site_id=site_id)
    except CmsError as exc:
        _raise(exc)

    identity = platform_identity(user)
    cookie = f"{debug_cookie_name(site_id)}={debug_cookie_value(identity[1] if identity else 0, 0)}"
    upstream = debug_url(settings.CMS_BASE_URL, path)
    headers = {"cookie": cookie, "accept": request.headers.get("accept", "*/*")}
    if request.headers.get("accept-language"):
        headers["accept-language"] = request.headers["accept-language"]

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        try:
            answer = await client.get(upstream, headers=headers)
        except httpx.HTTPError:
            raise HTTPException(
                status_code=502, detail={"code": "cms_unreachable", "message": "CMS 没有响应"}
            ) from None

    content_type = answer.headers.get("content-type", "application/octet-stream")
    # Rewritten against the path the browser asked for, not the one this route is
    # mounted at: the console's forwarder maps `/api/cms/...` onto
    # `/api/v1/cms/...`, so a relative asset URL has to name the former - and it
    # has to keep the segment the console is mounted under, or every stylesheet
    # in the frame resolves against the host root instead.
    prefix = _preview_prefix(request)
    body = rewrite(answer.content, content_type=content_type, base_url=f"{prefix}{PREVIEW_PATH}/{ticket}")
    return Response(
        content=body,
        status_code=answer.status_code,
        media_type=content_type.split(";")[0],
        headers=forwardable_headers(dict(answer.headers)),
    )


def _preview_prefix(request: Request) -> str:
    """The path this console is mounted under, as the browser sees it.

    Compiled into the frontend (`NEXT_PUBLIC_BASE_PATH`) rather than configured
    here, so the backend has no way to know it. The BFF stamps
    `X-Forwarded-Prefix` on every forwarded request instead, and both the link
    this route returns and the `<base>` it rewrites are built from it.

    Anything that is not a rooted path is ignored rather than trusted: the header
    ends up inside a URL a browser follows, and `//evil.example` is a valid way to
    write one that leaves the site.
    """
    prefix = request.headers.get("x-forwarded-prefix", "").strip()
    if not prefix.startswith("/") or prefix.startswith("//"):
        return ""
    return prefix.rstrip("/")


def _site_from_ticket(ticket: str, path: str) -> int | None:
    """The site a ticket names, read without trusting it - `verify` does that.

    The payload is readable by design (`{u, s, e}`), so the proxy can choose the
    cookie before it has a secret to check with. Reading it is not believing it:
    the signature is checked on the next line, against the same site id.
    """
    import base64
    import json

    encoded = ticket.partition(".")[0]
    if not encoded:
        return None
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw)
        return int(payload["s"])
    except (ValueError, KeyError, TypeError):
        return None


def _extensions() -> set[str]:
    from app.agents.capabilities.unione_cms import DEFAULT_ALLOWED_EXT

    return set(DEFAULT_ALLOWED_EXT)
