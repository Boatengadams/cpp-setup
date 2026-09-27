"""Archive extraction helpers: zip, tar.*, 7z, plus macOS dmg handling.

Extraction is hardened against absolute paths and ``..`` traversal, because
archives come from the network.
"""

from __future__ import annotations

import os
import re
import tarfile
import zipfile
from pathlib import Path
from typing import List, Optional

from . import util


class ExtractError(Exception):
    """Raised when an archive cannot be unpacked."""


def detect_kind(path) -> str:
    """Classify an archive by file extension."""
    name = Path(path).name.lower()
    if name.endswith(".zip") or name.endswith(".vsix"):
        return "zip"
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        return "tar"
    if name.endswith(".tar.xz") or name.endswith(".txz"):
        return "tar"
    if name.endswith(".tar.zst"):
        return "tar"
    if name.endswith(".tar.bz2") or name.endswith(".tbz2"):
        return "tar"
    if name.endswith(".tar"):
        return "tar"
    if name.endswith(".7z"):
        return "7z"
    if name.endswith(".dmg"):
        return "dmg"
    if name.endswith(".pkg") or name.endswith(".mpkg"):
        return "pkg"
    if name.endswith(".msi"):
        return "msi"
    if name.endswith((".exe", ".msix")):
        return "exe"
    return "unknown"


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _safe_zip_members(zf: zipfile.ZipFile, dest: Path) -> List[zipfile.ZipInfo]:
    """Validate members, refusing the whole archive if any escape ``dest``.

    Silently dropping a member is not good enough here: a toolchain that is
    missing part of itself produces a far more confusing error later than
    saying the download was malformed.
    """
    safe = []
    rejected = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in Path(name).parts:
            rejected.append(info.filename)
            continue
        safe.append(info)
    if rejected:
        raise ExtractError(
            "the archive contains paths that would escape the destination "
            "(%s%s); refusing to extract it"
            % (", ".join(repr(r) for r in rejected[:3]), "..." if len(rejected) > 3 else "")
        )
    return safe


def extract_zip(archive, dest, strip_top: bool = False) -> Path:
    dest = util.ensure_dir(dest)
    with zipfile.ZipFile(str(archive)) as zf:
        members = _safe_zip_members(zf, dest)
        for info in members:
            name = info.filename.replace("\\", "/")
            if strip_top:
                parts = Path(name).parts[1:]
                if not parts:
                    continue
                name = str(Path(*parts))
            if not name or name.endswith("/"):
                continue
            out = dest / name
            if not _is_within(dest, out):
                continue
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(str(out), "wb") as dst:
                while True:
                    chunk = src.read(262144)
                    if not chunk:
                        break
                    dst.write(chunk)
            # Preserve the executable bit when the zip carries Unix modes.
            mode = (info.external_attr >> 16) & 0o7777
            if mode:
                try:
                    os.chmod(str(out), mode)
                except OSError:
                    pass
    return dest


def extract_tar(archive, dest) -> Path:
    dest = util.ensure_dir(dest)
    try:
        with tarfile.open(str(archive), "r:*") as tf:
            for member in tf.getmembers():
                name = member.name.replace("\\", "/")
                if name.startswith("/") or ".." in Path(name).parts:
                    continue
                out = dest / name
                if not _is_within(dest, out):
                    continue
                if member.islnk() or member.issym():
                    # Links can point outside the tree; recreate only if safe.
                    target = (out.parent / member.linkname).resolve()
                    if member.islnk() and _is_within(dest, out) and not target.exists():
                        continue
                try:
                    tf.extract(member, str(dest), set_attrs=True)
                except (OSError, tarfile.TarError):
                    continue
    except tarfile.TarError as exc:
        raise ExtractError("Could not read tar archive %s: %s" % (Path(archive).name, exc))
    return dest


def extract_7z(archive, dest) -> Path:
    """Extract .7z using an external tool (7-Zip / p7zip / bsdtar)."""
    dest = util.ensure_dir(dest)
    for tool in ("7z", "7zz", "7za", "7zr", "bsdtar"):
        binary = util.which(tool)
        if not binary:
            continue
        if tool == "bsdtar":
            args = [binary, "-x", "-f", str(archive), "-C", str(dest)]
        else:
            args = [binary, "x", "-y", "-bd", "-o" + str(dest), str(archive)]
        result = util.run(args, timeout=900)
        if result.ok:
            return dest
    raise ExtractError(
        "No 7-Zip capable tool found to unpack %s. Install p7zip (Debian/Ubuntu: "
        "'sudo apt install p7zip-full', Fedora: 'sudo dnf install p7zip p7zip-plugins', "
        "macOS: 'brew install p7zip')." % Path(archive).name
    )


def extract(archive, dest, kind: Optional[str] = None, console=None) -> Path:
    """Extract ``archive`` into ``dest`` and return the destination path."""
    archive = Path(archive)
    if not archive.exists():
        raise ExtractError("Archive not found: %s" % archive)
    kind = kind or detect_kind(archive)
    if console:
        console.detail("unpacking %s (%s)" % (archive.name, util.human_size(archive.stat().st_size)))
    if kind == "zip":
        return extract_zip(archive, dest)
    if kind == "tar":
        return extract_tar(archive, dest)
    if kind == "7z":
        return extract_7z(archive, dest)
    if kind in ("dmg", "pkg", "msi", "exe"):
        raise ExtractError("%s archives are installed, not extracted (see lib.vscode)." % kind)
    # Unknown extension: try each extractor in turn.
    for candidate in ("zip", "tar", "7z"):
        try:
            return extract(archive, dest, kind=candidate, console=console)
        except (ExtractError, zipfile.BadZipFile, tarfile.TarError):
            continue
    raise ExtractError("Unsupported archive format: %s" % archive.name)
