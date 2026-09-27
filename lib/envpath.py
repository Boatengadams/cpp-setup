"""Make the toolchain ``bin`` directories available *globally*.

Windows : updates ``HKCU\\Environment`` PATH (no admin needed) and broadcasts
          ``WM_SETTINGCHANGE`` so already-open programs pick it up.
macOS   : writes guarded blocks into ~/.zshenv, ~/.zprofile, ~/.zshrc,
          ~/.profile, ~/.bash_profile, ~/.bashrc.
Linux   : same shell rc files plus a systemd ``environment.d`` drop-in.

Every write is idempotent: the block between the markers is replaced, never
duplicated.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import util

BEGIN = "# >>> cpp-setup (C++ toolchain) >>>"
END = "# <<< cpp-setup (C++ toolchain) <<<"

POSIX_RC_FILES = (
    ".zshenv",
    ".zprofile",
    ".zshrc",
    ".profile",
    ".bash_profile",
    ".bashrc",
)


def posix_block(directories: List[str]) -> str:
    lines = [
        BEGIN,
        "# Added by the 'cpp' setup tool - edits between the markers may be overwritten.",
    ]
    for directory in directories:
        lines += [
            'case ":$PATH:" in',
            '  *":%s:"*) ;;' % directory,
            '  *) export PATH="%s:$PATH" ;;' % directory,
            "esac",
        ]
    lines.append(END)
    return "\n".join(lines) + "\n"


class PathManager(object):
    """Add / remove / inspect persistent PATH entries."""

    def __init__(self, console=None, log=None, home: Optional[Path] = None):
        self.console = console
        self.log = log or (lambda *a, **k: None)
        self.home = Path(home) if home else Path(os.path.expanduser("~"))

    # -- public API --------------------------------------------------------
    def add(self, directories, session: bool = True) -> List[str]:
        """Persist ``directories`` in the OS PATH. Returns what was added."""
        wanted = [str(Path(d).expanduser()) for d in directories if d and str(d).strip()]
        wanted = [d for d in dict.fromkeys(wanted)]
        if not wanted:
            return []
        if session:
            for directory in wanted:
                util.session_prepend_path(directory)
        if util.IS_WINDOWS:
            added = self._windows_add(wanted)
        else:
            added = self._posix_add(wanted)
        return added

    def remove(self, directories) -> List[str]:
        targets = [str(Path(d).expanduser()) for d in directories if d]
        if util.IS_WINDOWS:
            return self._windows_remove(targets)
        return self._posix_remove(targets)

    def persisted(self) -> List[str]:
        """The PATH entries as stored by the OS (not the current process)."""
        if util.IS_WINDOWS:
            return self._windows_persisted()
        return self._posix_persisted()

    def has(self, directory) -> bool:
        target = os.path.normcase(os.path.abspath(str(Path(directory))))
        for entry in self.persisted():
            try:
                if os.path.normcase(os.path.abspath(os.path.expanduser(entry))) == target:
                    return True
            except OSError:
                continue
        return False

    def verify_fresh_shell(self, binary: str) -> Tuple[bool, str]:
        """Prove the entry survives a brand new shell process.

        A PATH change that only exists in the running process would not be
        visible here - this is the "works globally" check.
        """
        if util.IS_WINDOWS:
            return self._windows_verify(binary)
        return self._posix_verify(binary)

    # -- windows -----------------------------------------------------------
    def _winreg(self):  # pragma: no cover - windows only
        import winreg

        return winreg

    def _windows_read(self) -> Tuple[str, int]:  # pragma: no cover - windows only
        winreg = self._winreg()
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
                value, kind = winreg.QueryValueEx(key, "PATH")
                return value or "", kind
        except OSError:
            return "", winreg.REG_EXPAND_SZ

    def _windows_write(self, value: str, kind: int) -> None:  # pragma: no cover - windows only
        winreg = self._winreg()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            winreg.SetValueEx(key, "PATH", 0, kind, value)

    def _windows_add(self, directories: List[str]) -> List[str]:  # pragma: no cover - windows only
        current, kind = self._windows_read()
        entries = [e for e in current.split(";") if e]
        added = []
        for directory in directories:
            if not any(os.path.normcase(e.rstrip("\\")) == os.path.normcase(directory) for e in entries):
                entries.insert(0, directory)
                added.append(directory)
        if added:
            self._windows_write(";".join(entries), kind or 2)
            self._broadcast()
            self.log("windows_path_updated", added=added)
        return added

    def _windows_remove(self, directories: List[str]) -> List[str]:  # pragma: no cover - windows only
        current, kind = self._windows_read()
        entries = [e for e in current.split(";") if e]
        targets = {os.path.normcase(d) for d in directories}
        kept = [e for e in entries if os.path.normcase(e.rstrip("\\")) not in targets]
        if len(kept) != len(entries):
            self._windows_write(";".join(kept), kind or 2)
            self._broadcast()
        return [d for d in directories if os.path.normcase(d) in targets]

    def _windows_persisted(self) -> List[str]:  # pragma: no cover - windows only
        value, _kind = self._windows_read()
        return [e for e in value.split(";") if e]

    def _broadcast(self) -> None:  # pragma: no cover - windows only
        try:
            import ctypes

            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment", SMTO_ABORTIFHUNG, 5000, None
            )
        except Exception:
            pass

    def _windows_verify(self, binary: str) -> Tuple[bool, str]:  # pragma: no cover - windows only
        persisted = os.pathsep.join(self._windows_persisted())
        env = dict(os.environ)
        env["PATH"] = persisted
        exe = binary if binary.lower().endswith(".exe") else binary + ".exe"
        where = util.run(["where", exe], env=env, timeout=60)
        if where.ok and where.stdout.strip():
            return True, where.stdout.strip().splitlines()[0].strip()
        # PATH is correct but the shell has not been restarted yet: probe directly.
        for entry in self._windows_persisted():
            candidate = Path(entry) / exe
            if candidate.exists():
                return True, str(candidate)
        return False, "not found in the persisted PATH (%s)" % (where.text()[:120] or "no output")

    # -- posix -------------------------------------------------------------
    def _rc_targets(self) -> List[Path]:
        return [self.home / name for name in POSIX_RC_FILES]

    def _posix_add(self, directories: List[str]) -> List[str]:
        """Write (or refresh) the guarded block in every shell rc file."""
        targets = self._rc_targets()
        missing = [d for d in directories if not self._is_in_rc(d, targets)]
        if not missing:
            return []
        for rc in targets:
            self._write_block(rc, posix_block(missing))
        # systemd based Linux desktops pick this up without a new shell.
        envd = self.home / ".config" / "environment.d"
        if util.IS_LINUX and envd.parent.is_dir():
            conf = envd / "60-cpp-setup.conf"
            existing = util.read_text(conf)
            body = "# Added by the 'cpp' setup tool.\n"
            for directory in missing:
                body += 'PATH="%s:$PATH"\n' % directory
            if BEGIN in existing:
                existing = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", existing, flags=re.DOTALL)
            util.atomic_write_text(conf, existing.rstrip("\n") + "\n" + body)
        return missing

    def _posix_remove(self, directories: List[str]) -> List[str]:
        """Drop the guarded cases (and the block itself when it empties out)."""
        removed: List[str] = []
        for rc in self._rc_targets():
            text = util.read_text(rc)
            new_text = text
            for directory in directories:
                pattern = re.compile(
                    r'case ":\$PATH:" in\s*\n'
                    r'\s*\*":%s:"\*\) ;;\s*\n'
                    r'\s*\*\) export PATH="%s:\$PATH" ;;\s*\n'
                    r"esac\s*\n" % (re.escape(directory), re.escape(directory))
                )
                if pattern.search(new_text):
                    new_text = pattern.sub("", new_text)
                    if directory not in removed:
                        removed.append(directory)
            if BEGIN in new_text and "export PATH=" not in new_text.split(BEGIN, 1)[-1].split(END, 1)[0]:
                new_text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", new_text, flags=re.DOTALL)
            new_text = re.sub(r"\n{3,}", "\n\n", new_text)
            if new_text != text:
                util.atomic_write_text(rc, new_text)
        return removed

    def _has_marker(self, rc: Path) -> bool:
        return BEGIN in util.read_text(rc)

    def _write_block(self, rc: Path, block: str) -> None:
        text = util.read_text(rc)
        pattern = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", re.DOTALL)
        if pattern.search(text):
            new_text = pattern.sub(lambda _m: block, text)
        else:
            prefix = text
            if prefix and not prefix.endswith("\n"):
                prefix += "\n"
            new_text = prefix + ("\n" if prefix.strip() else "") + block
        util.atomic_write_text(rc, new_text)

    def _is_in_rc(self, directory: str, targets: List[Path]) -> bool:
        for rc in targets:
            if directory in util.read_text(rc):
                return True
        return False

    def _posix_persisted(self) -> List[str]:
        """Extract PATH-ish directory entries from the shell rc files."""
        entries: List[str] = []
        sources = list(self._rc_targets())
        envd = self.home / ".config" / "environment.d" / "60-cpp-setup.conf"
        if util.IS_LINUX:
            sources.append(envd)
        for source in sources:
            for line in util.read_text(source).splitlines():
                if not re.search(r"(^|[\s;(])export\s+PATH=|^\s*PATH=", line):
                    continue
                for quoted in re.findall(r'"([^"]+)"', line) + re.findall(r"'([^']+)'", line):
                    for part in quoted.split(":"):
                        part = part.strip()
                        if not part or part.startswith("$"):
                            continue
                        entries.append(os.path.expanduser(part))
        return list(dict.fromkeys(entries))

    def _posix_verify(self, binary: str) -> Tuple[bool, str]:
        attempts: List[Tuple[str, list]] = []
        for shell, args in (
            ("zsh", ["-ic"]),
            ("bash", ["-lc"]),
            ("bash", ["-ic"]),
            ("sh", ["-lc"]),
        ):
            binary_path = util.which(shell)
            if not binary_path:
                continue
            command = 'command -v %s' % binary
            attempts.append(("%s %s" % (shell, " ".join(args)), [binary_path] + args + [command]))
        if not attempts:
            return False, "no POSIX shell available to verify PATH"
        errors = []
        for label, argv in attempts:
            result = util.run(argv, timeout=60)
            # A login/interactive shell may print a banner (bash and zsh both
            # do), so only accept a line that really points at an existing file.
            found = [line.strip() for line in (result.stdout or "").splitlines() if _looks_like_executable(line)]
            if found:
                return True, found[-1]
            errors.append("%s: %s" % (label, result.text()[:80] or "rc=%d" % result.returncode))
        return False, "; ".join(errors)

    # -- reporting ---------------------------------------------------------
    def describe(self) -> str:
        if util.IS_WINDOWS:
            return "HKCU\\Environment (PATH)"
        return ", ".join(str(rc) for rc in self._rc_targets()[:2]) + " (+ ~/.config/environment.d)"


def _looks_like_executable(line: str) -> bool:
    """True when ``line`` is a plausible absolute path to an existing file.

    Used to pick the real answer out of ``command -v`` output that may be
    surrounded by shell banner noise.
    """
    candidate = (line or "").strip()
    if not candidate or not (candidate.startswith("/") or candidate.startswith("./") or re.match(r"^[A-Za-z]:[\\/]", candidate)):
        return False
    return os.path.isfile(candidate)


def session_env_with(directories: List[str], base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Return a copy of the environment with ``directories`` prepended."""
    env = dict(base if base is not None else os.environ)
    current = env.get("PATH", "")
    for directory in reversed(directories):
        current = str(directory) + os.pathsep + current
    env["PATH"] = current
    return env
