"""Visual Studio Code: detection, installation, extensions and configuration.

The CLI (``code``) is the integration point: it is used to install extensions
and to verify the C++ tooling is actually present.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import net, templates, util

VSCODE_UPDATE_BASE = "https://update.code.visualstudio.com/api"
VSCODE_DOWNLOAD_BASE = "https://update.code.visualstudio.com/latest"

# The four extension packs every C++ project needs.
CORE_PACKS: List[Dict[str, str]] = [
    {"id": "ms-vscode.cpptools-extension-pack", "why": "C/C++ Extension Pack: language server, debugger and CMake integration in one install"},
    {"id": "ms-vscode.cpptools", "why": "C/C++ language support: IntelliSense auto-complete, debugging, call hierarchy"},
    {"id": "ms-vscode.cmake-tools", "why": "CMake Tools: configure, build and debug with CMake projects"},
    {"id": "ms-vscode.makefile-tools", "why": "Makefile Tools: build targets straight from a Makefile"},
]

# Installed the first time VS Code is set up.
FIRST_TIME_EXTRAS: List[Dict[str, str]] = [
    {"id": "esbenp.prettier-vscode", "why": "Prettier: one-click formatting for JSON, YAML, Markdown and CSS"},
    {"id": "usernamehw.errorlens", "why": "Error Lens: compiler errors and warnings rendered inline on the code"},
    {"id": "usernamehw.cpptools-header-foundry", "why": "Header Foundry: auto-complete and navigation for #include files"},
    {"id": "codelldb.codelldb", "why": "CodeLLDB: native debugging that also powers debug-aware auto-complete"},
]


class VsCodeError(Exception):
    """Raised when VS Code could not be detected or installed."""


class VsCode(object):
    """A located VS Code CLI."""

    def __init__(self, cli: str, version: str = "", app: Optional[Path] = None, installed_by_us: bool = False):
        self.cli = cli
        self.version = version
        self.app = Path(app) if app else None
        self.installed_by_us = installed_by_us

    def as_dict(self) -> dict:
        return {"cli": self.cli, "version": self.version, "app": str(self.app) if self.app else None}

    def __repr__(self) -> str:  # pragma: no cover
        return "VsCode(%s, %s)" % (self.cli, self.version)


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------


def _cli_candidates() -> List[str]:
    names = ["code"]
    if util.IS_LINUX:
        names = ["code", "codium"]
    for name in names:
        found = util.which(name)
        if found:
            return [found]
    if util.IS_MACOS:
        for bundle in (
            "/Applications/Visual Studio Code.app/Contents/Resources/app/bin/code",
            "/Applications/Visual Studio Code - Insiders.app/Contents/Resources/app/bin/code",
            str(Path.home() / "Applications/Visual Studio Code.app/Contents/Resources/app/bin/code"),
        ):
            if Path(bundle).exists():
                return [bundle]
    if util.IS_WINDOWS:
        for candidate in (
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code", "bin", "code.cmd"),
            r"C:\Program Files\Microsoft VS Code\bin\code.cmd",
            os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Microsoft VS Code", "bin", "code.cmd"),
        ):
            if candidate and Path(candidate).exists():
                return [candidate]
    return []


def query_version(cli: str) -> str:
    result = util.run([cli, "--version"], timeout=90)
    if result.ok and result.first_line():
        return result.first_line().strip()
    return ""


def detect(console=None) -> Optional[VsCode]:
    """Return a working VS Code CLI, or ``None`` when it is not installed."""
    for cli in _cli_candidates():
        version = query_version(cli)
        if version:
            app = None
            if util.IS_MACOS and ".app" in cli:
                app = Path(cli).parents[2]
            if console:
                console.ok("Visual Studio Code found: %s" % version)
                console.detail(cli)
            return VsCode(cli, version, app=app)
        if console:
            console.detail("%s exists but did not answer --version" % cli)
    return None


def latest_stable_version(console=None) -> str:
    """Ask Microsoft's update service for the current stable version."""
    try:
        payload = net.fetch_json(VSCODE_UPDATE_BASE + "/releases/stable", timeout=30)
        if isinstance(payload, list) and payload:
            return str(payload[-1])
    except net.NetError as exc:
        if console:
            console.detail("could not read the VS Code version feed (%s)" % str(exc)[:70])
    return "latest"


# --------------------------------------------------------------------------
# installation
# --------------------------------------------------------------------------


def _download_platform() -> str:
    arch_name = util.arch()
    if util.IS_WINDOWS:
        return "win32-%s-user" % ("x64" if arch_name == "x86_64" else "arm64")
    if util.IS_MACOS:
        return "darwin-universal"
    if arch_name == "arm64":
        return "linux-deb-arm64"
    return "linux-deb-x64"


def _download_url(platform: Optional[str] = None) -> str:
    return "%s/%s/stable" % (VSCODE_DOWNLOAD_BASE, platform or _download_platform())


def _write_cli_shim(target: Path, code_bin: Path) -> Path:
    """Create a small launcher so ``code`` works from a tarball install."""
    util.ensure_parent(target)
    body = "#!/bin/sh\nexec %s \"$@\"\n" % str(code_bin)
    util.atomic_write_text(target, body)
    try:
        os.chmod(str(target), os.stat(str(target)).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    return target


def _install_linux(downloader, install_root: Path, cache_dir: Path, console, log) -> VsCode:
    """Prefer the distribution package, fall back to a user-local tarball."""
    platform = _download_platform()
    if platform.startswith("linux-deb"):
        manager = util.which("apt-get") or util.which("dnf")
        deb_url = _download_url(platform)
        if manager and util.is_admin():
            deb = cache_dir / ("vscode." + ("deb" if "apt-get" in manager else "rpm"))
            if console:
                console.info("Downloading the Visual Studio Code %s" % ("deb" if "apt-get" in manager else "rpm"))
            downloader.download([deb_url], deb, label=deb.name)
            if "apt-get" in manager:
                args = [manager, "install", "-y", str(deb)]
            else:
                args = [manager, "install", "-y", str(deb)]
            if console:
                console.info("Installing with %s (a system package gives desktop integration)" % Path(manager).name)
            result = util.run(args, timeout=1800)
            log("vscode_system_install", rc=result.returncode)
            found = detect()
            if found:
                return found
            if console:
                console.warn("the package manager reported rc=%d, falling back to a user install" % result.returncode)
        elif console:
            console.detail("no passwordless package manager available, installing into your home directory")

    # User-local install: no admin rights, no desktop entry, full functionality.
    version = latest_stable_version(console)
    tar_url = _download_url("linux-%s-x64" % ("arm64" if util.arch() == "arm64" else "x64"))
    tarball = cache_dir / ("vscode-%s.tar.gz" % version)
    if console:
        console.info("Downloading Visual Studio Code %s (user install)" % version)
    downloader.download([tar_url], tarball, label=tarball.name)
    from . import archive

    target = install_root / ("vscode-%s" % version)
    if util.safe_rmtree(target):
        log("vscode_replaced", path=str(target))
    archive.extract(tarball, target, kind="tar", console=console)
    code_bin = target / "VSCode-linux-x64" / "bin" / "code"
    if not code_bin.exists():
        code_bin = target / "VSCode-linux-arm64" / "bin" / "code"
    if not code_bin.exists():
        candidates = list(target.glob("*/bin/code"))
        code_bin = candidates[0] if candidates else None
    if not code_bin or not code_bin.exists():
        raise VsCodeError("the VS Code archive unpacked but bin/code was not found inside %s" % target)
    for binary in (code_bin, code_bin.parent / "code", code_bin.parent.parent / "code"):
        if binary.exists():
            try:
                os.chmod(str(binary), os.stat(str(binary)).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except OSError:
                pass
    shim = install_root / "bin" / "code"
    _write_cli_shim(shim, code_bin)
    util.session_prepend_path(str(shim.parent))
    log("vscode_user_install", path=str(target), shim=str(shim))
    found = detect()
    if not found:
        raise VsCodeError("VS Code was installed to %s but its CLI did not respond" % target)
    found.installed_by_us = True
    return found


def _install_macos(downloader, install_root: Path, cache_dir: Path, console, log) -> VsCode:
    version = latest_stable_version(console)
    dmg_url = _download_url("darwin-universal")
    dmg = cache_dir / ("vscode-%s.dmg" % version)
    if console:
        console.info("Downloading Visual Studio Code %s" % version)
    downloader.download([dmg_url], dmg, label=dmg.name)

    hdiutil = util.which("hdiutil")
    apps_dir = Path("/Applications")
    if not os.access(str(apps_dir), os.W_OK):
        apps_dir = Path.home() / "Applications"
        if console:
            console.detail("/Applications is not writable, using %s instead" % apps_dir)
    util.ensure_dir(apps_dir)

    if hdiutil:
        mountpoint = Path(temp_mount_base()) / "vscode-mount"
        util.ensure_dir(mountpoint)
        attach = util.run(
            [hdiutil, "attach", str(dmg), "-nobrowse", "-readonly", "-mountpoint", str(mountpoint)],
            timeout=300,
        )
        if not attach.ok:
            raise VsCodeError("hdiutil could not mount %s: %s" % (dmg.name, attach.text()[:140]))
        try:
            source = None
            for candidate in mountpoint.glob("*.app"):
                source = candidate
                break
            if source is None:
                raise VsCodeError("no .app bundle found inside %s" % dmg.name)
            destination = apps_dir / source.name
            if destination.exists():
                util.safe_rmtree(destination)
            result = util.run(["cp", "-R", str(source), str(destination)], timeout=600)
            if not result.ok:
                raise VsCodeError("could not copy the app bundle: %s" % result.text()[:140])
            log("vscode_app_installed", path=str(destination))
        finally:
            util.run([hdiutil, "detach", str(mountpoint), "-force"], timeout=120)
    else:
        raise VsCodeError(
            "hdiutil is unavailable, so the .dmg cannot be mounted. Install VS Code with "
            "'brew install --cask visual-studio-code' and then re-run 'cpp'."
        )

    cli = apps_dir / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "bin" / "code"
    shim = install_root / "bin" / "code"
    _write_cli_shim(shim, cli)
    util.session_prepend_path(str(shim.parent))
    found = detect()
    if not found:
        raise VsCodeError("VS Code was installed to %s but its CLI did not respond" % apps_dir)
    found.installed_by_us = True
    return found


def temp_mount_base() -> str:
    import tempfile

    return tempfile.gettempdir()


def _install_windows(downloader, install_root: Path, cache_dir: Path, console, log) -> VsCode:
    version = latest_stable_version(console)
    url = _download_url()
    installer = cache_dir / ("VSCodeUserSetup-%s.exe" % version)
    if console:
        console.info("Downloading the Visual Studio Code user installer %s" % version)
    downloader.download([url], installer, label=installer.name)
    flags = [
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/SP-",
        "/CURRENTUSER",
        '/MERGETASKS="!runcode,!runatstartup"',
    ]
    if console:
        console.detail("running the per-user installer (no administrator rights needed)")
    result = util.run([str(installer)] + flags, timeout=1800)
    log("vscode_installer", rc=result.returncode)
    if result.timed_out:
        raise VsCodeError("the VS Code installer did not finish within 30 minutes")
    found = detect()
    if not found:
        raise VsCodeError(
            "the VS Code installer ran (rc=%d) but the 'code' command is still not on PATH. "
            "Start VS Code once, then re-run 'cpp'." % result.returncode
        )
    found.installed_by_us = True
    return found


def install(downloader, install_root, cache_dir, console=None, log=None) -> VsCode:
    """Install VS Code for the current OS and return its CLI wrapper."""
    log = log or (lambda *a, **k: None)
    install_root = util.ensure_dir(install_root)
    cache_dir = util.ensure_dir(cache_dir)
    if util.IS_WINDOWS:
        return _install_windows(downloader, install_root, cache_dir, console, log)
    if util.IS_MACOS:
        return _install_macos(downloader, install_root, cache_dir, console, log)
    return _install_linux(downloader, install_root, cache_dir, console, log)


# --------------------------------------------------------------------------
# extensions
# --------------------------------------------------------------------------


def list_extensions(vscode: VsCode) -> List[str]:
    result = util.run([vscode.cli, "--list-extensions"], timeout=300)
    if not result.ok:
        return []
    return sorted({line.strip().lower() for line in (result.stdout or "").splitlines() if line.strip()})


def _first_useful_line(text: str, extension_id: str) -> str:
    """Pick the meaningful line out of noisy CLI output.

    The VS Code CLI wraps extension failures in Node deprecation warnings
    (``url.parse() ...``) that say nothing about the actual problem.
    """
    noise = (
        "DeprecationWarning",
        "trace-deprecation",
        "ExperimentalWarning",
        "(node:",
        "Warning:",
        # Progress chatter, not diagnostics.
        "Installing extensions",
        "Downloading extension",
        "Extension is already installed",
    )
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or any(marker in stripped for marker in noise):
            continue
        return stripped[:180]
    return "the marketplace gave no reason - just run: code --install-extension %s" % extension_id


def install_extensions(
    vscode: VsCode, packs: Sequence[Dict[str, str]], console=None, log=None, timeout: int = 900, force: bool = False
) -> Tuple[List[str], List[Tuple[str, str]]]:
    """Install each extension, tolerating individual failures.

    Extensions that are already registered are left alone unless ``force`` is
    set, which keeps a resumed run from re-downloading large packs over a
    slow or flaky marketplace connection.

    Returns ``(installed, failed)``.  ``failed`` holds ``(id, reason)`` pairs
    where the reason is a short human sentence, never raw CLI output.  One
    broken extension must not abort the whole setup.
    """
    log = log or (lambda *a, **k: None)
    installed: List[str] = []
    failed: List[Tuple[str, str]] = []
    present = set(list_extensions(vscode))
    to_install: List[Dict[str, str]] = []
    for pack in packs:
        extension_id = pack["id"]
        if not force and extension_id.lower() in present:
            installed.append(extension_id)
            log("extension_already_installed", id=extension_id)
            if console:
                console.skip("%s is already installed" % extension_id)
            continue
        to_install.append(pack)
    if not to_install:
        return installed, failed
    # The marketplace can be slow enough that the CLI reports a network error
    # *after* the extension was already registered, so the real source of
    # truth is the editor's own extension list, not the exit code.
    for pack in to_install:
        extension_id = pack["id"]
        why = pack.get("why", "")
        if console:
            console.info("Installing %s" % extension_id)
            if why:
                console.detail(why)
        result = util.run([vscode.cli, "--install-extension", extension_id, "--force"], timeout=timeout)
        if result.ok:
            installed.append(extension_id)
            log("extension_installed", id=extension_id)
            if console:
                console.ok("%s installed" % extension_id)
        else:
            reason = _extension_failure_reason(extension_id, result)
            failed.append((extension_id, reason))
            log("extension_nonzero_exit", id=extension_id, error=reason)
            if console:
                console.detail("the CLI reported an error; checking whether it installed anyway")

    # Reconcile every attempted extension against the editor's own list.
    registered = set(list_extensions(vscode))
    still_failed: List[Tuple[str, str]] = []
    for extension_id, reason in failed:
        if extension_id.lower() in registered:
            installed.append(extension_id)
            log("extension_installed_despite_error", id=extension_id)
            if console:
                console.ok("%s is installed (the CLI only reported a slow-network error)" % extension_id)
        else:
            still_failed.append((extension_id, reason))
            if console:
                console.warn("%s could not be installed: %s" % (extension_id, reason))
    return list(dict.fromkeys(installed)), still_failed


def _extension_failure_reason(extension_id: str, result) -> str:
    """Turn CLI output into one short, actionable sentence."""
    if result.timed_out:
        return "the download timed out - the marketplace was too slow to respond"
    text = (result.text() or "").lower()
    if "etimedout" in text or "econnreset" in text or "enotfound" in text or "eai_again" in text:
        return "the marketplace could not be reached (network timeout or DNS failure)"
    if "econnrefused" in text:
        return "the connection to the marketplace was refused (proxy or firewall?)"
    if "certificate" in text or "ssl" in text:
        return "the marketplace TLS certificate was rejected (check the system clock or proxy)"
    if "403" in text or "unauthorized" in text or "401" in text:
        return "the marketplace refused the request (a proxy may need authentication)"
    if "404" in text or "not found" in text:
        return "the marketplace does not know this extension id"
    if "eexist" in text or "already installed" in text:
        return "it is already installed, but the CLI still reported a problem"
    return "the VS Code CLI exited with code %d (%s)" % (result.returncode, _first_useful_line(result.text(), extension_id))


def verify_extensions(vscode: VsCode, extension_ids: Sequence[str]) -> Tuple[List[str], List[str]]:
    """Confirm extensions are really registered by the editor."""
    present = list_extensions(vscode)
    missing = [eid for eid in extension_ids if eid.lower() not in present]
    return [eid for eid in extension_ids if eid.lower() in present], missing


def all_extension_ids(packs: Sequence[Dict[str, str]]) -> List[str]:
    return [pack["id"] for pack in packs]


# --------------------------------------------------------------------------
# project configuration
# --------------------------------------------------------------------------


def write_config(
    workspace,
    compiler: Optional[dict],
    os_name: str,
    arch_name: str,
    extension_ids: Sequence[str],
    console=None,
) -> List[str]:
    """Write .vscode/{settings,c_cpp_properties,tasks,launch,extensions}.json.

    Existing files are merged (never replaced) and backed up once.
    """
    workspace = Path(workspace)
    vscode_dir = util.ensure_dir(workspace / ".vscode")
    written: List[str] = []
    # VS Code documents JSON-with-comments for settings/c_cpp_properties/tasks/
    # launch; extensions.json is consumed by other tooling, so it stays strict.
    plan = [
        ("settings.json", templates.settings_for(compiler, os_name, arch_name, list(extension_ids)), True),
        (
            "c_cpp_properties.json",
            templates.c_cpp_properties_for(compiler, os_name, arch_name),
            True,
        ),
        ("tasks.json", templates.tasks_for(compiler, os_name), True),
        ("launch.json", templates.launch_for(os_name), True),
        ("extensions.json", templates.extensions_json(list(extension_ids)), False),
    ]
    for name, data, with_header in plan:
        target = vscode_dir / name
        try:
            changed = templates.merge_into(target, data, header=templates.BANNER if with_header else "")
        except OSError as exc:
            if console:
                console.warn("could not write %s (%s)" % (target, exc))
            continue
        written.append(str(target))
        if console:
            console.ok("%s %s" % (target.name, "updated" if changed else "already up to date"))
    return written
