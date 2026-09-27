"""Terminal presentation: colours, banners, step output, progress, prompts."""

from __future__ import annotations

import os
import re
import shutil
import sys
import time
from typing import List, Optional

from . import util

_COLORS = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "magenta": "\033[35m",
    "cyan": "\033[36m",
    "grey": "\033[90m",
}

_GLYPHS = {
    "windows": {"ok": "[+]", "fail": "[x]", "warn": "[!]", "info": "[i]", "skip": "[-]", "arrow": "->"},
    "macos": {"ok": "\u2714", "fail": "\u2718", "warn": "\u26a0", "info": "\u2139", "skip": "\u2013", "arrow": "\u2192"},
    "linux": {"ok": "\u2714", "fail": "\u2718", "warn": "\u26a0", "info": "\u2139", "skip": "\u2013", "arrow": "\u2192"},
}

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def supports_color(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("CPP_FORCE_COLOR"):
        return True
    if os.environ.get("TERM", "") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except Exception:
        return False


class Console(object):
    """Colourised console output that stays readable when piped to a file."""

    def __init__(self, no_color: bool = False, quiet: bool = False, stream=None):
        self.stream = stream or sys.stdout
        self.color = (not no_color) and supports_color(self.stream)
        self.quiet = quiet
        self.glyphs = _GLYPHS.get(util.os_name(), _GLYPHS["linux"])
        self.is_tty = False
        try:
            self.is_tty = bool(self.stream.isatty())
        except Exception:
            self.is_tty = False
        try:
            self.width = max(52, min(shutil.get_terminal_size((80, 24)).columns - 2, 110))
        except Exception:  # pragma: no cover
            self.width = 80

    # -- primitives --------------------------------------------------------
    def paint(self, text: str, *styles: str) -> str:
        if not self.color or not styles or not text:
            return text
        prefix = "".join(_COLORS.get(style, "") for style in styles)
        return prefix + text + _COLORS["reset"]

    def _write(self, text: str = "", end: str = "\n") -> None:
        try:
            self.stream.write(text + end)
            self.stream.flush()
        except Exception:  # pragma: no cover
            pass

    def _tag(self, glyph: str, style: str) -> str:
        return self.paint(self.glyphs[glyph], style)

    # -- semantic output ---------------------------------------------------
    def raw(self, text: str = "") -> None:
        self._write(text)

    def info(self, text: str) -> None:
        self._write("%s %s" % (self._tag("info", "blue"), text))

    def ok(self, text: str) -> None:
        self._write("%s %s" % (self._tag("ok", "green"), text))

    def warn(self, text: str) -> None:
        self._write("%s %s" % (self._tag("warn", "yellow"), self.paint(text, "yellow")))

    def error(self, text: str) -> None:
        self._write("%s %s" % (self._tag("fail", "red"), self.paint(text, "red")))

    def skip(self, text: str) -> None:
        self._write("%s %s" % (self._tag("skip", "grey"), self.paint(text, "grey")))

    def note(self, text: str) -> None:
        for line in (text or "").splitlines() or [""]:
            self._write(self.paint("    " + line, "grey"))

    def detail(self, text: str) -> None:
        """An explanatory sub-line. Suppressed by --quiet / without -v."""
        if self.quiet:
            return
        self._write(self.paint("    " + text, "grey"))

    def blank(self) -> None:
        self._write("")

    def rule(self, char: str = "-") -> None:
        self._write(self.paint(char * self.width, "grey"))

    def banner(self, title: str, subtitle: str = "") -> None:
        width = max(len(title) + 4, len(subtitle) + 4, 58)
        width = min(width, self.width)
        self.blank()
        self._write(self.paint("+" + "-" * (width - 2) + "+", "cyan"))
        self._write(self.paint("|", "cyan") + self.paint(title.center(width - 2), "bold", "cyan") + self.paint("|", "cyan"))
        if subtitle:
            self._write(self.paint("|", "cyan") + self.paint(subtitle.center(width - 2), "grey") + self.paint("|", "cyan"))
        self._write(self.paint("+" + "-" * (width - 2) + "+", "cyan"))
        self.blank()

    def step(self, index: int, total: int, title: str) -> None:
        counter = self.paint("[%d/%d]" % (index, total), "bold", "magenta")
        self._write("%s %s" % (counter, self.paint(title, "bold")))
        self._write(self.paint("  " + "-" * (self.width - 3), "grey"))

    def table(self, rows: List[List[str]], headers: Optional[List[str]] = None) -> None:
        all_rows = ([headers] if headers else []) + list(rows)
        if not all_rows:
            return
        columns = max(len(r) for r in all_rows)
        widths = [0] * columns
        for row in all_rows:
            for i in range(columns):
                widths[i] = max(widths[i], len(str(row[i]) if i < len(row) else ""))
        if headers:
            self._write("  " + "  ".join(self.paint(str(headers[i]).ljust(widths[i]), "bold") for i in range(columns)))
        for row in rows:
            self._write("  " + "  ".join((str(row[i]) if i < len(row) else "").ljust(widths[i]) for i in range(columns)))

    # -- progress ----------------------------------------------------------
    def progress(self, label: str, current: int, total: int, force: bool = False) -> None:
        """Render a single-line download progress bar (carriage return)."""
        if self.quiet and not force:
            return
        if not self.is_tty:
            if total > 0 and current >= total:
                self.detail("%s downloaded (%s)" % (label, util.human_size(total)))
            return
        if total <= 0:
            self._write("  " + label)
            return
        ratio = max(0.0, min(1.0, float(current) / float(total)))
        width = 28
        filled = int(width * ratio)
        bar = "#" * filled + "." * (width - filled)
        text = "[%s] %5.1f%%  %s / %s" % (bar, ratio * 100.0, util.human_size(current), util.human_size(total))
        line = self.paint("  " + label.ljust(20), "grey") + " " + self.paint(text, "green")
        self._write(line, end="\r")
        if ratio >= 1.0:
            self._write(" " * (len(strip_ansi(line)) + 4), end="\r")
            self.detail("%s done (%s)" % (label, util.human_size(total)))

    # -- interaction -------------------------------------------------------
    def ask(self, question: str, default: str = "", allow_empty: bool = True) -> str:
        suffix = " " + self.paint("[%s]" % default, "grey") if default else ""
        while True:
            self._write("%s %s%s " % (self.paint("?", "bold", "cyan"), question, suffix), end="")
            try:
                answer = sys.stdin.readline()
            except (KeyboardInterrupt, EOFError):
                self.blank()
                raise
            if answer == "":
                self.blank()
                return default
            answer = answer.strip()
            if answer:
                return answer
            if allow_empty:
                return default

    def ask_yes_no(self, question: str, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        while True:
            self._write(
                "%s %s %s " % (self.paint("?", "bold", "cyan"), question, self.paint("(%s)" % hint, "grey")),
                end="",
            )
            try:
                answer = sys.stdin.readline()
            except (KeyboardInterrupt, EOFError):
                self.blank()
                return default
            if answer == "":
                self.blank()
                return default
            answer = answer.strip().lower()
            if not answer:
                return default
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                return False
            self.warn("Please answer 'y' or 'n'.")

    def confirm(self, question: str, default: bool = True, assume_yes: bool = False) -> bool:
        if assume_yes:
            self.info("%s %s" % (question, self.paint("(auto-confirmed)", "grey")))
            return True
        return self.ask_yes_no(question, default)


class Timer(object):
    """Elapsed-time helper used to annotate step output."""

    def __init__(self):
        self.start = time.time()

    @property
    def elapsed(self) -> float:
        return time.time() - self.start

    def human(self) -> str:
        seconds = self.elapsed
        if seconds < 60:
            return "%.1fs" % seconds
        minutes, secs = divmod(int(seconds), 60)
        return "%dm%02ds" % (minutes, secs)


_console = None


def console(no_color: bool = False, quiet: bool = False) -> Console:
    """Return the process-wide console singleton."""
    global _console
    if _console is None:
        _console = Console(no_color=no_color, quiet=quiet)
    return _console
