"""The HTTP client that speaks to a Unione CMS.

Every CMS call in this feature goes through here, so this file decides two
things: what a request looks like, and what a failure means. Both are asserted
against `httpx.MockTransport` rather than a live CMS - the interesting
behaviour is the mapping, and a test that needs a running CMS can only check
the happy path.

The envelope matters more than it looks. Unione answers `{success, code,
message, body, total, page}`, and a refusal arrives as HTTP 200 with
`success: false` about as often as it arrives as a 4xx. A client that only
looks at the status code reports "published successfully" for a resource the
CMS refused, which is exactly the failure that reaches visitors.
"""

from __future__ import annotations

import httpx
import pytest

from app.cms.client import CmsClient, debug_url
from app.cms.errors import (
    FORBIDDEN,
    NOT_FOUND,
    PUBLISHED,
    TOKEN_EXPIRED,
    UNREACHABLE,
    VERIFY_FAILED,
    CmsError,
)


def client_for(handler, *, token: str = "tenant@user@hash") -> CmsClient:
    """A client wired to a handler, with no network and no retries."""
    return CmsClient(
        base_url="https://cms.example.com",
        token=token,
        transport=httpx.MockTransport(handler),
    )


def envelope(body=None, *, success=True, message=None, total=None, page=None):
    return {
        "success": success,
        "code": 200 if success else 500,
        "message": message,
        "body": body,
        "total": total,
        "page": page,
    }


@pytest.mark.anyio
async def test_the_platform_token_travels_in_the_header_the_cms_reads():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get("token", "")
        seen["path"] = request.url.path
        return httpx.Response(200, json=envelope([]))

    await client_for(handler).find_sites()

    assert seen["token"] == "tenant@user@hash"
    assert seen["path"] == "/cms/api/site/find"


@pytest.mark.anyio
async def test_find_sites_asks_for_a_page_and_returns_what_the_cms_sent():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        payload = __import__("json").loads(request.content)
        assert payload["page"] == 2 and payload["pageSize"] == 25
        assert payload["keywords"] == "main"
        assert payload["body"] == {}, "the CMS asserts body is present"
        return httpx.Response(
            200,
            json=envelope(
                [
                    {
                        "id": 7,
                        "title": "Main site",
                        "ctx": "/cms/main",
                        "domain": "main.example.com",
                        "spaceId": 11,
                        "spaceVers": 3,
                        "adminUserid": 42,
                        "createdBy": 42,
                        "coverPic": "/file/cover.png",
                    }
                ],
                total=1,
                page=2,
            ),
        )

    sites = await client_for(handler).find_sites(keywords="main", page=2, page_size=25)

    assert [(site.id, site.title, site.space_id) for site in sites] == [(7, "Main site", 11)]
    assert sites[0].domain == "main.example.com"
    assert sites[0].admin_userid == 42


@pytest.mark.anyio
async def test_an_expired_session_is_its_own_code_because_retrying_cannot_fix_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"success": False, "code": 401, "message": "登录已过期"})

    with pytest.raises(CmsError) as caught:
        await client_for(handler).find_sites()

    assert caught.value.code == TOKEN_EXPIRED


def test_the_debug_console_lives_under_the_cms_context_not_the_host_root():
    """The deployed CMS hangs off `/cms`, and so does its debug console.

    nginx sends `/cms` to the CMS and everything else to the platform's own
    single-page apps, which answer `/debug/...` with a portal page and HTTP 200.
    Building the preview URL from the host root therefore produces a frame that
    looks like it loaded and is not the site at all - the worst kind of wrong.
    """

    assert debug_url("http://192.168.56.1", "/views/index.html") == (
        "http://192.168.56.1/cms/debug/views/index.html"
    )
    assert debug_url("http://192.168.56.1/", "") == "http://192.168.56.1/cms/debug/"


@pytest.mark.anyio
async def test_a_redirect_to_the_login_page_is_an_expired_session_not_a_mystery():
    """The deployed CMS answers a dead session with 303 and an empty body.

    nginx sits in front of the CMS and its auth filter redirects to
    `/portal/login` instead of returning 401. There is no envelope to parse and
    no JSON to fail on, so a client that only knows about 401/403 reports
    "the CMS answered something that is not JSON" - which hides the one thing
    the person can act on: sign in again.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(303, headers={"location": "/portal/login"}, content=b"")

    with pytest.raises(CmsError) as caught:
        await client_for(handler).find_sites()

    assert caught.value.code == TOKEN_EXPIRED
    assert "登录" in caught.value.message


@pytest.mark.anyio
async def test_is_authed_answers_false_when_the_request_was_redirected():
    """The status endpoint feeds a banner, so it must not raise on a dead token."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/portal/login"}, content=b"")

    assert await client_for(handler).is_authed() is False


@pytest.mark.anyio
async def test_a_refusal_inside_a_200_is_still_a_refusal():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=envelope(None, success=False, message="无操作权限"))

    with pytest.raises(CmsError) as caught:
        await client_for(handler).release_all(7)

    assert caught.value.code == FORBIDDEN
    assert "无操作权限" in caught.value.message


@pytest.mark.anyio
async def test_editing_a_published_resource_names_the_upgrade_that_is_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=envelope(None, success=False, message="资源已发布，请先升级资源！")
        )

    with pytest.raises(CmsError) as caught:
        await client_for(handler).save_resource(
            site_id=7, space_id=11, name="/views/index.html", types="text", body_text="<div/>"
        )

    assert caught.value.code == PUBLISHED


@pytest.mark.anyio
async def test_save_sends_a_form_the_controller_can_bind():
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = request.content.decode()
        return httpx.Response(200, json=envelope(901))

    saved = await client_for(handler).save_resource(
        site_id=7,
        space_id=11,
        name="/views/index.html",
        types="text",
        body_text="<div>hi</div>",
        title="index",
    )

    assert saved == 901
    # A form, either encoding: the controller binds `CmsResource` field by field
    # and the optional `file` part is the only thing that would require multipart.
    assert str(seen["content_type"]).startswith(
        ("application/x-www-form-urlencoded", "multipart/form-data")
    )
    body = str(seen["body"])
    assert "siteId=7" in body
    assert "bodyText=" in body
    assert "%2Fviews%2Findex.html" in body, "the resource path, url-encoded"


@pytest.mark.anyio
async def test_verify_maps_a_beetl_failure_to_the_line_the_parser_named():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "success": False,
                "code": 500,
                "message": "模版异常",
                "body": {"line": 12, "message": "expect END"},
            },
        )

    with pytest.raises(CmsError) as caught:
        await client_for(handler).verify(site_id=7, name="/views/index.html", body_text="<% if %>")

    assert caught.value.code == VERIFY_FAILED
    assert "12" in caught.value.message


@pytest.mark.anyio
async def test_verify_that_passes_returns_quietly():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=envelope(None))

    await client_for(handler).verify(site_id=7, name="/views/index.html", body_text="ok")


@pytest.mark.anyio
async def test_export_returns_the_zip_bytes_rather_than_json():
    payload = b"PK\x03\x04fake-zip"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cms/api/resource/export/site/7"
        return httpx.Response(200, content=payload, headers={"content-type": "application/zip"})

    assert await client_for(handler).export_site(7) == payload


@pytest.mark.anyio
async def test_export_that_answers_a_login_page_is_not_a_zip():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>login</html>", headers={"content-type": "text/html"})

    with pytest.raises(CmsError) as caught:
        await client_for(handler).export_site(7)

    assert caught.value.code == TOKEN_EXPIRED


@pytest.mark.anyio
async def test_a_dead_address_is_unreachable_rather_than_unexpected():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    with pytest.raises(CmsError) as caught:
        await client_for(handler).find_sites()

    assert caught.value.code == UNREACHABLE


@pytest.mark.anyio
async def test_a_404_names_the_missing_site():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    with pytest.raises(CmsError) as caught:
        await client_for(handler).find_resources(7)

    assert caught.value.code == NOT_FOUND


@pytest.mark.anyio
async def test_a_token_never_appears_in_the_message_of_a_failure():
    """The token is a password: an error string is the easiest place to leak one."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom tenant@user@hash")

    with pytest.raises(CmsError) as caught:
        await client_for(handler).find_sites()

    assert "tenant@user@hash" not in str(caught.value)
