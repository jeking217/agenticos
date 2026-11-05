"""Ask a deployed CMS the questions the Web-site module asks, in order.

Not a test. The unit tests already pin how a response becomes a decision; this
is the check that the *deployment* agrees with them - the real nginx, the real
auth filter, the real Beetl, the real ZIP. It walks the same path a pull takes
and stops at the first step that disagrees, so a wrong `CMS_BASE_URL` or a dead
token is one readable line instead of a stack trace from inside a page.

    python scripts/cms_smoke.py --token "$TOKEN"
    python scripts/cms_smoke.py --token "$TOKEN" --site-id 1958495584934289408 \\
        --workspace /tmp/cms-smoke

The token is a password. It comes from the environment or the command line, is
never printed, and never lands in the workspace.

Exit codes, because a check that is scripted needs to be readable without
parsing prose: 0 everything passed, 1 a step failed, 2 the run could not start
(no address, no token, or a token the CMS does not accept).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

# `python scripts/cms_smoke.py` puts the script's own directory on sys.path, not
# the backend root, so `app` would not import. Only when run as a file: `-m`
# already has the root and re-inserting it would shadow the real package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.capabilities.unione_cms import DEFAULT_ALLOWED_EXT
from app.cms.archive import extract_site_zip
from app.cms.client import CmsClient
from app.cms.errors import CmsError
from app.cms.workspace import SiteWorkspace
from app.core.config import settings

#: Two megabytes is the capability's default ceiling, and a smoke test that used
#: a different one would pass for a pull that the console then rejects.
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024


class Report:
    """A pass/fail line per step, and a count of the failures.

    Deliberately plain text: this runs in a terminal on a customer's host, often
    through `docker exec`, where colour is stripped and a table wraps.
    """

    def __init__(self) -> None:
        self.failures = 0

    def step(self, name: str) -> None:
        print(f"  .. {name}", end="\r", flush=True)

    def ok(self, name: str, detail: str = "") -> None:
        print(f"  ok  {name}" + (f" - {detail}" if detail else ""))

    def fail(self, name: str, detail: str) -> None:
        self.failures += 1
        print(f"  FAIL {name} - {detail}")


def _site_of(rows: list, requested: int | None):
    """The site to pull: the one asked for, or the first the token can see."""
    if requested is None:
        return rows[0] if rows else None
    for row in rows:
        if row.id == requested:
            return row
    return None


async def check(report: Report, args: argparse.Namespace) -> int:
    base_url = args.base_url or settings.CMS_BASE_URL
    if not base_url:
        report.fail("configuration", "CMS_BASE_URL is empty, so the module is switched off")
        return 2

    token = args.token or os.environ.get("CMS_SMOKE_TOKEN", "")
    if not token:
        report.fail(
            "configuration",
            "no token: pass --token or set CMS_SMOKE_TOKEN "
            "(it is the platform's `token` header, from a signed-in browser session)",
        )
        return 2

    print(f"cms: {base_url}")

    client = CmsClient(base_url=base_url, token=token, timeout=args.timeout)
    try:
        if not await client.is_authed():
            report.fail(
                "session",
                "the CMS does not accept this token - sign in again and copy the "
                "`token` header the browser sends",
            )
            return 2
        report.ok("session")

        sites = await client.find_sites()
        report.ok("site list", f"{len(sites)} site(s) visible")
        for row in sites[:10]:
            print(f"        {row.id}  {row.ctx:<16} {row.title}")

        site = _site_of(sites, args.site_id)
        if site is None:
            report.fail("site", f"site {args.site_id} is not in the list this token can see")
            return 1

        detail = await client.site_detail(site.id)
        report.ok("site detail", f"{detail.title} @ {detail.ctx}, space {detail.space_id} v{detail.space_vers}")

        resources = await client.find_resources(site.id)
        texts = [row for row in resources if row.is_text]
        report.ok(
            "resource tree",
            f"{len(resources)} resource(s), {len(texts)} template(s), "
            f"{sum(1 for row in resources if row.is_dir)} folder(s)",
        )

        payload = await client.export_site(site.id)
        report.ok("export", f"{len(payload)} bytes")

        workspace_root = Path(args.workspace) if args.workspace else Path(
            tempfile.mkdtemp(prefix="cms-smoke-")
        )
        if workspace_root.exists() and args.workspace:
            shutil.rmtree(workspace_root)
        workspace_root.mkdir(parents=True, exist_ok=True)

        extracted = extract_site_zip(
            payload,
            workspace_root,
            allowed_ext=DEFAULT_ALLOWED_EXT,
            max_file_bytes=args.max_file_bytes,
        )
        total = sum(item.size for item in extracted.files)
        report.ok("unpack", f"{len(extracted.files)} file(s), {total} bytes, {len(extracted.skipped)} skipped")
        for item in extracted.skipped[:5]:
            print(f"        skipped {item.path} - {item.reason}")

        if extracted.empty:
            report.fail(
                "unpack",
                "no file survived the extension whitelist; the archive layout is not "
                "what a pull expects",
            )
            return 1

        workspace = SiteWorkspace(workspace_root)
        await workspace.ensure_repository()
        await workspace.commit(f"pull site {site.id} from cms (smoke)")
        if site.space_vers:
            # The same baseline tag a real pull leaves, so `discard` can find it.
            await workspace.tag(f"cms-release-{site.space_vers}")
        status = await workspace.status()
        report.ok(
            "git baseline",
            f"branch {await workspace.current_branch()}, {len(await workspace.releases())} tag(s), "
            f"{len(status)} pending change(s)",
        )

        print("")
        report.ok("done", f"workspace at {workspace_root}")
        return 0
    except CmsError as exc:
        report.fail(exc.code, exc.message)
        return 1
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="", help="defaults to CMS_BASE_URL")
    parser.add_argument("--token", default="", help="the platform `token` header; defaults to CMS_SMOKE_TOKEN")
    parser.add_argument("--site-id", type=int, default=None, help="defaults to the first visible site")
    parser.add_argument("--workspace", default="", help="where to unpack; defaults to a temp dir")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_BYTES,
        help="per-file ceiling, matching the capability's default",
    )
    args = parser.parse_args(argv)

    report = Report()
    try:
        return asyncio.run(check(report, args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
