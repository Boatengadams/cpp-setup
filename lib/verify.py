"""Compile and run the generated C++ program, then check the real output.

This is the proof that the whole chain works: toolchain, linker, standard
library, and a program that asks for the developer's name.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import templates, util


class VerifyError(Exception):
    """Raised when the verification program does not build or misbehaves."""


def source_path(workspace) -> Path:
    return Path(workspace) / templates.VERIFICATION_FILENAME


def binary_path(workspace) -> Path:
    workspace = Path(workspace)
    name = templates.VERIFICATION_BINARY + (".exe" if util.IS_WINDOWS else "")
    return workspace / name


def write_program(workspace, force: bool = False, console=None) -> Tuple[Path, bool]:
    """Write the verification source; returns ``(path, rewritten)``."""
    target = source_path(workspace)
    if target.exists() and not force:
        return target, False
    util.atomic_write_text(target, templates.WELCOME_PROGRAM)
    if console:
        console.ok("Created %s" % target)
    return target, True


def compile_program(
    compiler_exe: str,
    source,
    output,
    cwd=None,
    console=None,
    standards: Tuple[str, ...] = ("c++20", "c++17", "c++14"),
) -> Dict:
    """Compile ``source`` with the newest standard the compiler accepts."""
    attempts: List[Dict] = []
    flags_base = ["-Wall", "-Wextra", "-g", "-O0"]
    for standard in standards:
        args = [compiler_exe, "-std=%s" % standard] + flags_base + [str(source), "-o", str(output)]
        if console:
            console.detail("%s -std=%s %s" % (Path(compiler_exe).name, standard, Path(source).name))
        result = util.run(args, cwd=str(cwd) if cwd else None, timeout=600)
        attempts.append({"standard": standard, "rc": result.returncode, "output": result.text()})
        if result.ok:
            return {"ok": True, "standard": standard, "command": args, "attempts": attempts, "output": result.text()}
    last = attempts[-1] if attempts else {"output": "no attempt was made"}
    diagnostics = (last.get("output") or "").strip()
    raise VerifyError(
        "Compiling %s failed with %s.\n%s"
        % (Path(source).name, ", ".join(a["standard"] for a in attempts), _indent(diagnostics[:1200]) or "(no compiler output)")
    )


def run_program(binary, name: str, cwd=None, console=None, timeout: int = 120) -> Dict:
    """Run the program, feed it ``name`` and capture stdout+stderr."""
    binary = str(binary)
    if not os.path.exists(binary):
        raise VerifyError("the compiled program %s does not exist" % binary)
    if not util.IS_WINDOWS:
        try:
            mode = os.stat(binary).st_mode
            os.chmod(binary, mode | 0o111)
        except OSError:
            pass
    if console:
        console.detail("running %s" % Path(binary).name)
    result = util.run([binary], cwd=str(cwd) if cwd else None, input_text=(name or "") + "\n", timeout=timeout)
    return {"rc": result.returncode, "stdout": result.stdout or "", "stderr": result.stderr or "", "output": result.text()}


def check_output(name: str, output: str) -> Tuple[bool, List[str]]:
    """Confirm every congratulation marker is present for ``name``."""
    haystack = output.replace("\r\n", "\n")
    missing = [marker for marker in templates.success_markers(name) if marker not in haystack]
    return (not missing), missing


def verify(
    compiler_exe: str,
    workspace,
    user_name: str,
    console=None,
    force_rewrite: bool = False,
) -> Dict:
    """Full create -> build -> run -> assert cycle.

    Returns a dict describing the run (source, binary, standard, output).
    """
    workspace = util.ensure_dir(workspace)
    source, _rewritten = write_program(workspace, force=force_rewrite, console=console)
    output_binary = binary_path(workspace)
    if console:
        console.info("Compiling with %s" % compiler_exe)
    build = compile_program(compiler_exe, source, output_binary, cwd=workspace, console=console)
    if console:
        console.ok("Compiled with -std=%s" % build["standard"])
    run = run_program(output_binary, user_name, cwd=workspace, console=console)
    if run["rc"] != 0:
        raise VerifyError(
            "the program exited with code %d.\n%s" % (run["rc"], _indent(run["output"][:1200]))
        )
    ok, missing = check_output(user_name, run["stdout"] + run["stderr"])
    if not ok:
        raise VerifyError(
            "the program ran but its output was missing the expected text: %s\n%s"
            % (", ".join(repr(m) for m in missing), _indent(run["stdout"][:1200]))
        )
    return {
        "source": str(source),
        "binary": str(output_binary),
        "standard": build["standard"],
        "command": build["command"],
        "name": user_name,
        "stdout": run["stdout"],
        "ok": True,
    }


def cross_compile_check(compiler_exe: str, workspace, console=None) -> Optional[Dict]:
    """Build a Windows binary with the MinGW compiler (compile-only check).

    On Linux and macOS the produced executable cannot be run here, but a
    successful compile proves the MinGW headers, libraries and linker are
    installed correctly.  Returns ``None`` when there is nothing to check.
    """
    if util.IS_WINDOWS:
        return None
    workspace = util.ensure_dir(workspace)
    source = source_path(workspace)
    if not source.exists():
        return None
    out = workspace / "mingw_cross_compile_check.exe"
    try:
        build = compile_program(compiler_exe, source, out, cwd=workspace, console=console)
    except VerifyError as exc:
        if console:
            console.warn("MinGW cross-compile check failed: %s" % str(exc).splitlines()[0])
        return {"ok": False, "error": str(exc)[:500]}
    if console:
        console.ok("MinGW cross-compiled a Windows binary (%s)" % util.human_size(out.stat().st_size))
    return {"ok": True, "standard": build["standard"], "binary": str(out)}


def _indent(text: str, prefix: str = "    ") -> str:
    if not text:
        return ""
    return "\n".join(prefix + line for line in text.splitlines())
