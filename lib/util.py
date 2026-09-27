"""Small OS / process / filesystem helpers used across the bootstrapper."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# platform identification
# --------------------------------------------------------------------------

IS_WINDOWS = os.name == "nt" or sys.platform.startswith("win")
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def os_name() -> str:
    """Return one of ``windows``, ``macos``, ``linux`` or ``unknown``."""
    if IS_WINDOWS:
        return "windows"
    if IS_MACOS:
        return "macos"
    if IS_LINUX:
        return "linux"
    return "unknown"


def arch() -> str:
    """Return the machine architecture as ``x86_64``, ``arm64`` or raw value."""
    machine = (platform.machine() or "").lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    return machine or "x86_64"


def os_release() -> str:
    try:
        return platform.platform(terse=True)
    except Exception:  # pragma: no cover
        return "unknown"


def is_admin() -> bool:
    if not IS_WINDOWS:
        try:
            return os.geteuid() == 0
        except AttributeError:  # pragma: no cover
            return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover
        return False


# --------------------------------------------------------------------------
# subprocess
# --------------------------------------------------------------------------


class CommandResult(object):
    """Result of :func:`run`."""

    __slots__ = ("args", "returncode", "stdout", "stderr", "timed_out")

    def __init__(self, args, returncode, stdout, stderr, timed_out=False):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def text(self) -> str:
        return ((self.stdout or "") + (self.stderr or "")).strip()

    def first_line(self) -> str:
        text = self.text()
        return text.splitlines()[0].strip() if text else ""

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return "CommandResult(rc=%r, out=%r)" % (self.returncode, (self.stdout or "")[:80])


def _popen_kwargs(shell: bool) -> dict:
    kwargs = {}
    if IS_WINDOWS:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return kwargs


def run(
    args: Sequence,
    timeout: Optional[float] = 120,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    input_text: Optional[str] = None,
    shell: bool = False,
) -> CommandResult:
    """Run a command and capture output.

    Never raises: problems are reported through ``CommandResult``.  A missing
    executable shows up as returncode 127, a timeout as returncode 124.
    """
    if not args:
        return CommandResult(args, 127, "", "empty command")
    # An interactive stdin must never leak into a child: tools such as the
    # VS Code CLI behave differently (and report failure) when they detect a
    # pipe on stdin, so only the caller that supplies input gets a pipe.
    stdin_mode = subprocess.PIPE if input_text is not None else subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=stdin_mode,
            cwd=cwd,
            env=env,
            shell=shell,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            **_popen_kwargs(shell)
        )
    except (OSError, ValueError) as exc:
        return CommandResult(args, 127, "", str(exc))
    try:
        out, err = proc.communicate(input=input_text, timeout=timeout)
        return CommandResult(args, proc.returncode, out or "", err or "")
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            out, err = proc.communicate(timeout=10)
        except Exception:  # pragma: no cover
            out, err = "", ""
        return CommandResult(args, 124, out or "", err or "", timed_out=True)
    except Exception as exc:  # pragma: no cover - defensive
        return CommandResult(args, 1, "", str(exc))


def which(name: str) -> Optional[str]:
    """Locate an executable, including Windows ``.cmd``/``.bat`` shims."""
    if not name:
        return None
    found = shutil.which(name)
    if found:
        return found
    if IS_WINDOWS and not os.path.splitext(name)[1]:
        for ext in (".exe", ".cmd", ".bat", ".com"):
            found = shutil.which(name + ext)
            if found:
                return found
    for entry in path_entries():
        candidate = os.path.join(entry, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        if IS_WINDOWS:
            for ext in (".exe", ".cmd", ".bat"):
                candidate = os.path.join(entry, name + ext)
                if os.path.isfile(candidate):
                    return candidate
    return None


# --------------------------------------------------------------------------
# PATH helpers (in-process only)
# --------------------------------------------------------------------------


def path_entries() -> List[str]:
    return [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]


def in_path(directory: str) -> bool:
    target = os.path.normcase(os.path.abspath(directory))
    for entry in path_entries():
        if os.path.normcase(os.path.abspath(entry)) == target:
            return True
    return False


def session_prepend_path(directory: str) -> None:
    """Prepend ``directory`` to the PATH of this process and its children."""
    if not directory or in_path(directory):
        return
    os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")


# --------------------------------------------------------------------------
# filesystem helpers
# --------------------------------------------------------------------------


def ensure_dir(path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_parent(path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> Path:
    """Write ``text`` to ``path`` atomically (temp file + os.replace)."""
    p = Path(path)
    ensure_parent(p)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix="." + p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, str(p))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def read_text(path, default: str = "") -> str:
    try:
        with open(str(path), "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except (OSError, IOError):
        return default


def append_text(path, text: str) -> None:
    p = ensure_parent(Path(path))
    with open(str(p), "a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def human_size(num_bytes: float) -> str:
    try:
        num = float(num_bytes)
    except (TypeError, ValueError):
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024.0 or unit == "GB":
            return ("%d %s" % (num, unit)) if unit == "B" else ("%.1f %s" % (num, unit))
        num /= 1024.0
    return "%.1f GB" % num  # pragma: no cover


def sha256_file(path, chunk_size: int = 1024 * 1024) -> Optional[str]:
    digest = hashlib.sha256()
    try:
        with open(str(path), "rb") as handle:
            while True:
                block = handle.read(chunk_size)
                if not block:
                    break
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def safe_rmtree(path) -> bool:
    target = Path(path)
    try:
        if not target.exists() and not target.is_symlink():
            return False
        if target.is_symlink() or target.is_file():
            target.unlink()
        else:
            shutil.rmtree(str(target), ignore_errors=False)
        return True
    except Exception:
        return False


def unique_dir(base) -> Path:
    """Create a fresh unique staging directory under ``base``."""
    base = ensure_dir(base)
    return Path(tempfile.mkdtemp(prefix="stage-", dir=str(base)))


def first_existing(paths: Iterable) -> Optional[Path]:
    for item in paths:
        if item is None:
            continue
        p = Path(item)
        if p.exists():
            return p
    return None


def version_tuple(version: str) -> Tuple[int, ...]:
    """Parse a leading dotted numeric version, e.g. ``13.2.0`` -> ``(13, 2, 0)``."""
    parts: List[int] = []
    for chunk in str(version).split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def looks_like_version(text: str) -> bool:
    return bool(version_tuple(text))
