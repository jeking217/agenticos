"""Unpacking a site's archive without letting it write wherever it likes.

A CMS export is a ZIP whose entries come from a database of resource names, and
those names are editable by anyone with the site's editor role. So the archive
is untrusted input even though it comes from our own CMS: an entry called
`../../app/main.py`, an absolute path, a Windows drive path, a symlink pointing
at `/etc`, or a 2 GB "image" are all things a resource name can express.

`zipfile.extractall` guards against none of them. This module does the guarding,
and it does it before a byte is written: the destination is resolved and checked
against the workspace root, the extension against the capability's allow-list,
and the size against the per-file ceiling. A refused entry is reported, not
silently dropped - a pull that quietly skipped half the site would look like a
site that lost half its pages.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.cms.errors import PATH_REJECTED, CmsError

#: Characters that have no business in a resource path and make a mess of one:
#: control characters, and the separator Windows accepts as well as `/` so a
#: name cannot smuggle a second directory level past a POSIX check.
_FORBIDDEN = re.compile(r"[\x00-\x1f\\]")

#: How much a single site pull may unpack, across all entries. A real site is a
#: few megabytes; this is a ceiling on an accident, not a quota.
MAX_TOTAL_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True)
class ExtractedFile:
    """One file that reached the workspace."""

    path: str
    size: int


@dataclass(frozen=True)
class SkippedEntry:
    """One entry that did not, and the reason a person can act on."""

    path: str
    reason: str


@dataclass
class ExtractReport:
    """What a pull produced, so the console can say so."""

    files: list[ExtractedFile] = field(default_factory=list)
    skipped: list[SkippedEntry] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.files


def normalize_path(raw: str) -> str:
    """A resource path as this feature writes it: `/views/index.html`.

    Every CMS resource name is already absolute and slash-separated, so this is
    mostly about refusing the ones that are not: `..` anywhere, a backslash, a
    drive letter, control characters. The check is a refusal rather than a
    cleanup - silently rewriting `../../x` into `x` would turn an attack into a
    file in the site, which is worse than a failed pull.
    """
    if not raw or raw.strip() != raw:
        raise CmsError(PATH_REJECTED, "a resource path cannot be empty or padded")
    if _FORBIDDEN.search(raw):
        raise CmsError(PATH_REJECTED, f"{raw!r} contains a character a path cannot hold")
    # Collapsed rather than refused: `//views/x.html` is a doubled separator in
    # every archive that has one, and the collapsed result is still checked
    # below. A Windows UNC path arrives with backslashes and is refused by
    # `_FORBIDDEN` before it gets here.
    candidate = "/" + raw.lstrip("/")
    if re.match(r"^/[A-Za-z]:", candidate):
        raise CmsError(PATH_REJECTED, f"{raw!r} is a drive path")
    parts = [part for part in candidate.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise CmsError(PATH_REJECTED, f"{raw!r} climbs out of the site")
    if not parts:
        raise CmsError(PATH_REJECTED, f"{raw!r} names no file")
    return "/" + "/".join(parts)


def extension_of(path: str) -> str:
    """The lower-case extension without its dot, or empty for a file without one."""
    name = posixpath.basename(path)
    _, dot, suffix = name.rpartition(".")
    return suffix.lower() if dot and suffix else ""


def is_allowed(path: str, allowed_ext: frozenset[str] | set[str] | tuple[str, ...]) -> bool:
    """Whether this file type may reach the workspace."""
    return extension_of(path) in set(allowed_ext)


def resolve_inside(root: Path, path: str) -> Path:
    """`path` under `root`, having proved it stays under it.

    The resolved path is checked, not the string: `root/a/../../b` looks
    containable and is not, and a symlinked directory inside the workspace
    would defeat a string check as well.
    """
    normalized = normalize_path(path)
    candidate = (root / normalized.lstrip("/")).resolve()
    base = root.resolve()
    if candidate != base and base not in candidate.parents:
        raise CmsError(PATH_REJECTED, f"{path!r} resolves outside the workspace")
    return candidate


def write_file(
    root: Path,
    path: str,
    data: bytes,
    *,
    allowed_ext: frozenset[str] | set[str] | tuple[str, ...],
    max_file_bytes: int,
) -> ExtractedFile:
    """Write one file, or refuse it - the same rules for a pull and an edit."""
    normalized = normalize_path(path)
    if not is_allowed(normalized, allowed_ext):
        raise CmsError(PATH_REJECTED, f"{normalized} is not a file type this site may hold")
    if len(data) > max_file_bytes:
        raise CmsError(PATH_REJECTED, f"{normalized} is larger than the {max_file_bytes} byte ceiling")
    destination = resolve_inside(root, normalized)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return ExtractedFile(path=normalized, size=len(data))


def extract_site_zip(
    payload: bytes,
    root: Path,
    *,
    allowed_ext: frozenset[str] | set[str] | tuple[str, ...],
    max_file_bytes: int,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> ExtractReport:
    """Unpack a site's export into `root`, refusing anything suspicious.

    Errors are collected per entry rather than raised: one unusable file in an
    archive of two hundred should not cost the pull. The two exceptions are a
    payload that is not a ZIP at all and an archive that would exceed the total
    ceiling - both mean "this is not the site you think it is", and continuing
    would produce a workspace that looks complete and is not.
    """
    report = ExtractReport()
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise CmsError(PATH_REJECTED, "the site export is not a ZIP archive") from exc

    total = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename
        try:
            normalized = normalize_path(name)
        except CmsError as exc:
            report.skipped.append(SkippedEntry(path=name, reason=exc.message))
            continue
        if not is_allowed(normalized, allowed_ext):
            report.skipped.append(
                SkippedEntry(path=normalized, reason=f"{extension_of(normalized) or 'no'} files are not synced")
            )
            continue
        if info.file_size > max_file_bytes:
            report.skipped.append(
                SkippedEntry(path=normalized, reason=f"larger than {max_file_bytes} bytes")
            )
            continue
        total += info.file_size
        if total > max_total_bytes:
            raise CmsError(
                PATH_REJECTED,
                f"the site export is larger than {max_total_bytes} bytes; refusing to unpack it",
            )
        data = archive.read(info)
        try:
            report.files.append(
                write_file(
                    root,
                    normalized,
                    data,
                    allowed_ext=allowed_ext,
                    max_file_bytes=max_file_bytes,
                )
            )
        except CmsError as exc:
            report.skipped.append(SkippedEntry(path=normalized, reason=exc.message))
    return report


def read_workspace(
    root: Path,
    *,
    allowed_ext: frozenset[str] | set[str] | tuple[str, ...],
) -> dict[str, bytes]:
    """Every editable file in the workspace, keyed by its resource path.

    `.git` is skipped rather than filtered by extension: it holds objects with
    no extension, and a pull that reported them as "not synced" every time
    would train a reader to ignore the skipped list.
    """
    files: dict[str, bytes] = {}
    if not root.exists():
        return files
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative.startswith(".git/"):
            continue
        resource = "/" + relative
        if not is_allowed(resource, allowed_ext):
            continue
        files[resource] = path.read_bytes()
    return files


def delete_file(root: Path, path: str) -> None:
    """Remove a workspace file, having proved it is inside the workspace."""
    destination = resolve_inside(root, path)
    if destination.exists():
        destination.unlink()
