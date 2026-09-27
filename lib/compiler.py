"""C++ compiler discovery and MinGW-w64 installation.

The toolchain is installed into ``~/.cpp-setup/toolchain`` and its ``bin``
directory is added to the system PATH.  On Windows that is a native MinGW-w64
GCC build; on Linux and macOS it is llvm-mingw, which cross-compiles for
Windows while still letting us run native code through the host compiler.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import archive, net, util

# Executables we accept as "a working C++ compiler", best first.
NATIVE_NAMES = ("g++", "c++", "clang++")
MINGW_MARKERS = (
    "g++.exe",
    "g++",
    "x86_64-w64-mingw32-g++",
    "clang++.exe",
    "clang++",
    "x86_64-w64-mingw32-clang++",
    "x86_64-w64-mingw32-clang++.exe",
)


class Compiler(object):
    """A discovered compiler: where it is, what version, who installed it."""

    def __init__(self, exe: str, version: str = "", kind: str = "native", managed: bool = False, root: Optional[Path] = None):
        self.exe = exe
        self.version = version
        self.kind = kind
        self.managed = managed
        self.root = Path(root) if root else None

    @property
    def bin_dir(self) -> Optional[Path]:
        return Path(self.exe).parent

    def as_dict(self) -> dict:
        return {
            "exe": self.exe,
            "version": self.version,
            "kind": self.kind,
            "managed": self.managed,
            "root": str(self.root) if self.root else None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return "Compiler(%s, %s, %s)" % (self.exe, self.version, self.kind)


def query_version(exe: str, timeout: int = 60) -> str:
    """Return the first line of ``<exe> --version`` (empty when it fails)."""
    result = util.run([exe, "--version"], timeout=timeout)
    if result.ok:
        return result.first_line()
    dump = util.run([exe, "-dumpversion"], timeout=timeout)
    return dump.first_line() if dump.ok else ""


def detect(native: bool = True, names: Sequence[str] = NATIVE_NAMES) -> Optional[Compiler]:
    """Find an already installed compiler on PATH."""
    if not native:
        return None
    for name in names:
        exe = util.which(name)
        if not exe:
            continue
        version = query_version(exe)
        if not version:
            continue  # present but not runnable (wrong arch, broken wrapper)
        managed = ".cpp-setup" in str(exe)
        return Compiler(exe, version, kind="mingw" if managed else "native", managed=managed, root=_guess_root(exe))
    return None


def _guess_root(exe: str) -> Optional[Path]:
    """Walk up from ``exe`` to the toolchain root (the dir holding bin/)."""
    path = Path(exe)
    for parent in list(path.parents)[:4]:
        if parent.name in ("bin", "mingw64") and parent.parent.name:
            return parent.parent
    return path.parent.parent if path.parent.name == "bin" else None


def find_bin_dir(root: Path, markers: Sequence[str] = MINGW_MARKERS, max_depth: int = 4) -> Optional[Path]:
    """Locate the ``bin`` directory inside an extracted toolchain."""
    root = Path(root)
    candidates = [root] + list(root.glob("*")) + list(root.glob("*/*"))
    for directory in candidates[: max(2, max_depth * 4)]:
        if not directory.is_dir():
            continue
        if directory.name == "bin" or (directory / "bin").is_dir():
            bin_dir = directory if directory.name == "bin" else directory / "bin"
            for marker in markers:
                if (bin_dir / marker).exists() or (bin_dir / (marker + ".exe")).exists():
                    return bin_dir
    return None


# --------------------------------------------------------------------------
# MinGW-w64 providers (newest-first asset patterns)
# --------------------------------------------------------------------------

WINLIBS_REPO = "brechtsanders/winlibs_mingw"
LLVM_MINGW_REPO = "mstorsjo/llvm-mingw"


class Provider(object):
    """Where to fetch a toolchain from and how to recognise the right asset."""

    def __init__(self, name: str, repo: str, patterns: Sequence[str], mirrors: Sequence[str] = ()):
        self.name = name
        self.repo = repo
        self.patterns = list(patterns)
        self.mirrors = list(mirrors)

    def resolve(self, console, log) -> Dict:
        """Return ``{url, name, size, tag, kind, mirrors}`` for the newest asset."""
        release = net.github_release(self.repo)
        assets = net.release_assets(release)
        asset = net.select_asset(assets, self.patterns)
        if asset is not None:
            log("asset_selected", provider=self.name, asset=asset["name"], size=asset["size"])
            if console:
                console.detail("%s: %s (%s)" % (self.repo, asset["name"], util.human_size(asset["size"])))
            return {
                "url": asset["url"],
                "name": asset["name"],
                "size": asset["size"],
                "tag": release.get("tag_name", ""),
                "kind": archive.detect_kind(asset["name"]),
                "mirrors": [net.direct_url(self.repo, release.get("tag_name", ""), asset["name"])],
            }
        # API reachable but no asset matched: fall back to a known-good mirror.
        log("asset_miss", provider=self.name, patterns=self.patterns)
        if self.mirrors:
            for url in self.mirrors:
                name = url.rsplit("/", 1)[-1]
                return {
                    "url": url,
                    "name": name,
                    "size": 0,
                    "tag": "mirror",
                    "kind": archive.detect_kind(name),
                    "mirrors": list(self.mirrors),
                }
        raise net.NetError(
            "No matching %s release asset for this machine (looked for: %s)."
            % (self.name, ", ".join(self.patterns))
        )


def providers_for(os_name: str, arch_name: str) -> List[Provider]:
    """Ordered providers for the current OS (first one is preferred)."""
    if os_name == "windows":
        return [
            Provider(
                "WinLibs MinGW-w64 (GCC, UCRT, posix threads)",
                WINLIBS_REPO,
                [
                    r"^x86_64-posix-seh-ucrt-.*\.zip$",
                    r"^x86_64-ucrt-posix-.*\.zip$",
                    r"^x86_64.*ucrt.*\.zip$",
                ],
            ),
            Provider("WinLibs MinGW-w64 (fallback pattern)", WINLIBS_REPO, [r"^x86_64.*\.zip$"]),
        ]
    if os_name == "linux":
        if arch_name == "arm64":
            return [
                Provider(
                    "llvm-mingw (GCC/Clang cross toolchain)",
                    LLVM_MINGW_REPO,
                    [r"^llvm-mingw.*ubuntu.*aarch64\.tar\.xz$", r"^llvm-mingw.*aarch64.*\.tar\.xz$"],
                )
            ]
        return [
            Provider(
                "llvm-mingw (GCC/Clang cross toolchain)",
                LLVM_MINGW_REPO,
                [
                    r"^llvm-mingw.*ubuntu.*x86_64\.tar\.xz$",
                    r"^llvm-mingw.*-x86_64\.tar\.xz$",
                    r"^llvm-mingw.*ubuntu.*\.tar\.xz$",
                ],
            )
        ]
    if os_name == "macos":
        if arch_name == "arm64":
            patterns = [r"^llvm-mingw.*darwin-arm64\.tar\.xz$", r"^llvm-mingw.*darwin.*aarch64\.tar\.xz$"]
        else:
            patterns = [r"^llvm-mingw.*darwin-x86_64\.tar\.xz$", r"^llvm-mingw.*darwin.*\.tar\.xz$"]
        return [Provider("llvm-mingw (GCC/Clang cross toolchain)", LLVM_MINGW_REPO, patterns)]
    return []


class CompilerError(Exception):
    """Raised when a usable C++ toolchain could not be installed."""


def _slug(text: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in text.lower()).strip("-")


def cached_toolchain(toolchain_dir: Path) -> Optional[Compiler]:
    """Return the previously installed managed toolchain, if it still works."""
    toolchain_dir = Path(toolchain_dir)
    if not toolchain_dir.is_dir():
        return None
    bin_dir = find_bin_dir(toolchain_dir)
    if not bin_dir:
        return None
    for name in ("g++", "clang++", "g++.exe", "clang++.exe"):
        candidate = bin_dir / name
        if candidate.exists() and os.access(str(candidate), os.X_OK if not util.IS_WINDOWS else os.F_OK):
            version = query_version(str(candidate))
            if version:
                return Compiler(str(candidate), version, kind="mingw", managed=True, root=toolchain_dir)
    return None


def install_mingw(
    toolchain_dir,
    cache_dir,
    downloader: net.Downloader,
    console=None,
    log=None,
    force: bool = False,
) -> Compiler:
    """Download, extract and validate the newest MinGW-w64 toolchain.

    Existing installs are reused unless ``force`` is set, which keeps re-runs
    fast and makes the whole operation resumable.
    """
    log = log or (lambda *a, **k: None)
    toolchain_dir = util.ensure_dir(toolchain_dir)
    cache_dir = util.ensure_dir(cache_dir)

    if not force:
        existing = cached_toolchain(toolchain_dir)
        if existing:
            log("toolchain_reused", path=existing.exe, version=existing.version)
            if console:
                console.skip("Reusing the toolchain already in %s" % toolchain_dir)
                console.detail("%s (%s)" % (existing.exe, existing.version))
            return existing

    providers = providers_for(util.os_name(), util.arch())
    if not providers:
        raise CompilerError("Unsupported platform %s/%s for MinGW-w64." % (util.os_name(), util.arch()))

    failures: List[str] = []
    for provider in providers:
        target_dir = toolchain_dir / _slug(provider.name.split()[0])
        try:
            if console:
                console.detail("resolving the latest release from %s" % provider.repo)
            release = provider.resolve(console, log)
            archive_path = cache_dir / release["name"]
            if archive_path.exists() and archive_path.stat().st_size > 0 and not force:
                if console:
                    console.skip("using the cached download %s" % archive_path.name)
            else:
                if console:
                    console.info("Downloading %s" % release["name"])
                downloader.download(
                    release.get("mirrors") or [release["url"]],
                    archive_path,
                    label=release["name"],
                )

            staging = util.unique_dir(cache_dir)
            try:
                archive.extract(archive_path, staging, kind=release.get("kind"), console=console)
                bin_dir = find_bin_dir(staging)
                if not bin_dir:
                    raise CompilerError(
                        "the archive unpacked but no compiler bin/ directory was found (expected one of: %s)"
                        % ", ".join(MINGW_MARKERS)
                    )
                if util.safe_rmtree(target_dir):
                    log("toolchain_replaced", path=str(target_dir))
                target_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(bin_dir.parent), str(target_dir))
            finally:
                util.safe_rmtree(staging)

            bin_dir = find_bin_dir(target_dir)
            if not bin_dir:
                raise CompilerError("toolchain unpacked to %s but no bin/ directory was found" % target_dir)
            exe = _pick_exe(bin_dir)
            if not exe:
                raise CompilerError("no g++/clang++ executable inside %s" % bin_dir)
            version = query_version(exe)
            if not version:
                raise CompilerError("%s is present but does not run (wrong architecture or missing libraries)" % exe)
            log("toolchain_installed", path=exe, version=version, provider=provider.name)
            if console:
                console.ok("Installed %s" % version)
            return Compiler(exe, version, kind="mingw", managed=True, root=target_dir)
        except (net.NetError, archive.ExtractError, CompilerError, OSError) as exc:
            failures.append("%s: %s" % (provider.name, exc))
            log("provider_failed", provider=provider.name, error=str(exc))
            if console:
                console.warn("%s could not be installed (%s)" % (provider.name, str(exc)[:90]))

    raise CompilerError("No MinGW-w64 toolchain could be installed. " + " | ".join(failures))


def _pick_exe(bin_dir: Path) -> Optional[str]:
    for name in ("g++", "g++.exe", "clang++", "clang++.exe", "x86_64-w64-mingw32-g++", "x86_64-w64-mingw32-g++.exe"):
        candidate = bin_dir / name
        if candidate.exists():
            return str(candidate)
    return None


# --------------------------------------------------------------------------
# Native (host) compiler: detection and package-manager fallback
# --------------------------------------------------------------------------

PACKAGE_CANDIDATES = (
    ("apt-get", ["g++", "build-essential"]),
    ("dnf", ["gcc-c++"]),
    ("yum", ["gcc-c++"]),
    ("zypper", ["gcc-c++"]),
    ("pacman", ["gcc"]),
    ("apk", ["g++", "make"]),
    ("brew", ["gcc"]),
)


def _elevate(args: List[str], console) -> List[str]:
    """Prefix ``args`` with a non-interactive sudo when we are not root."""
    if util.is_admin():
        return args
    sudo = util.which("sudo")
    if not sudo:
        return args
    probe = util.run([sudo, "-n", "true"], timeout=20)
    if probe.ok:
        return [sudo, "-n"] + args
    if console:
        console.detail("sudo needs a password, so the package manager was not used")
    return []


def ensure_native(console=None, log=None, attempt_install: bool = True) -> Optional[Compiler]:
    """Return a host compiler, optionally installing one via the package manager."""
    log = log or (lambda *a, **k: None)
    found = detect(native=True)
    if found:
        return found
    if not attempt_install:
        return None
    if console:
        console.detail("no host C++ compiler found, trying the system package manager")
    if util.os_name() == "macos":
        xcode = util.which("xcode-select")
        if xcode:
            if console:
                console.detail("macOS ships AppleClang; run 'xcode-select --install' if clang++ is missing")
        return None
    for manager, packages in PACKAGE_CANDIDATES:
        binary = util.which(manager)
        if not binary:
            continue
        args = _elevate([binary, "install", "-y"] + packages, console)
        if not args:
            break
        if console:
            console.info("Installing a C++ compiler with %s" % manager)
        result = util.run(args, timeout=1800)
        log("package_install", manager=manager, rc=result.returncode)
        if result.ok or detect(native=True):
            break
    return detect(native=True)
