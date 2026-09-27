"""Command line interface: argument parsing and the top-level commands."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path
from typing import List, Optional

from . import setup as setup_mod
from . import state, ui, util, verify


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--no-color", action="store_true", help="disable coloured output")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print warnings and errors")
    parser.add_argument("-v", "--verbose", action="store_true", help="show the extra detail lines")
    parser.add_argument("--version", action="version", version="cpp-setup %s" % setup_mod.version())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cpp",
        description="Set up a complete C++ environment (MinGW-w64 toolchain, PATH, VS Code) in one command.",
        epilog="The setup is resumable: if a step fails, run 'cpp' again to continue where it stopped.",
    )
    _add_common(parser)
    parser.add_argument("--name", help="your name, used by the test program (skips the prompt)")
    parser.add_argument("--offline", action="store_true", help="never download; use what is already installed")
    parser.add_argument("--skip-vscode", action="store_true", help="set up the toolchain only, skip VS Code")
    parser.add_argument("-y", "--yes", action="store_true", help="assume yes for every question")
    parser.add_argument(
        "--force",
        nargs="+",
        metavar="STEP",
        help="re-run these steps even if they already succeeded (see --list-steps)",
    )
    parser.add_argument("--only", nargs="+", metavar="STEP", help="run only these steps")
    parser.add_argument("--list-steps", action="store_true", help="print the step ids and exit")
    parser.add_argument("--status", action="store_true", help="show what is already done and exit")
    parser.add_argument("--reset", action="store_true", help="forget the recorded state so everything runs again")
    parser.add_argument("--run-test", action="store_true", help="only run the C++ test program")
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="undo the setup: remove the PATH entries and files this tool added",
    )
    parser.add_argument("--workspace", metavar="DIR", help="where to keep the project files (default ~/.cpp-setup/workspace)")
    parser.add_argument("--state-file", metavar="FILE", help="path to the state file (default ~/.cpp-setup/state.json)")
    return parser


def _resolve_paths(args) -> setup_mod.Paths:
    paths = setup_mod.Paths()
    if args.workspace:
        paths.workspace = Path(os.path.expanduser(args.workspace))
    if args.state_file:
        paths.state_file = Path(os.path.expanduser(args.state_file))
    return paths.ensure()


def _print_steps(console) -> int:
    console.table(
        [[str(index), step.id, step.title] for index, step in enumerate(state.STEPS, start=1)],
        headers=["#", "Step id (--force/--only)", "What it does"],
    )
    return 0


def _print_status(console, paths) -> int:
    store = state.StateStore(paths.state_file, cli_version=setup_mod.version())
    rows = []
    for step in state.STEPS:
        record = store.record(step.id)
        rows.append([step.id, step.title, record.get("status", state.STATUS_PENDING), record.get("duration_human", "")])
    console.table(rows, headers=["Step", "What it does", "Status", "Took"])
    counts = store.counts()
    console.blank()
    console.info(
        "done %d, failed %d, pending %d (resume point: %s)"
        % (counts.get(state.STATUS_DONE, 0), counts.get(state.STATUS_FAILED, 0), counts.get(state.STATUS_PENDING, 0), store.resume_point())
    )
    failure = store.first_failure()
    if failure:
        record = store.record(failure)
        console.error("last failure in '%s': %s" % (failure, record.get("error", "")))
        if record.get("hint"):
            for line in str(record["hint"]).splitlines():
                console.raw("  " + line)
    return 0 if not failure else 1


def _run_uninstall(console, paths, assume_yes: bool = False) -> int:
    """Undo the setup. Asks first, because it deletes the toolchain."""
    from . import uninstall

    console.info("This will remove the PATH entries and files that 'cpp' added.")
    console.detail("The toolchain and VS Code stay installed - other software may use them.")
    if not console.confirm("Remove the C++ setup now?", default=False, assume_yes=assume_yes):
        console.skip("cancelled, nothing was changed")
        return 0
    try:
        return uninstall.run(paths.root, paths.workspace, console)
    except OSError as exc:
        console.error("uninstall failed: %s" % exc)
        return 1


def _run_test(console, paths) -> int:
    from . import compiler as compiler_mod

    found = compiler_mod.detect()
    if not found:
        console.error("no C++ compiler is available to run the test")
        return 1
    console.info("using %s (%s)" % (found.exe, found.version))
    try:
        result = verify.verify(found.exe, paths.workspace, _ask_name(console), console=console, force_rewrite=True)
    except verify.VerifyError as exc:
        console.error(str(exc))
        return 1
    console.blank()
    console.raw(result["stdout"].rstrip())
    return 0


def _ask_name(console) -> str:
    return console.ask("What is your name?", default="Coder", allow_empty=True) or "Coder"


def main(argv: Optional[List[str]] = None) -> int:
    try:
        return _main(argv)
    except BrokenPipeError:
        # Output was piped into something that stopped reading (`cpp --list-steps
        # | head`).  Detach stdout so the interpreter's final flush cannot turn
        # a successful run into a noisy traceback.
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
        except (OSError, ValueError):  # pragma: no cover
            pass
        return 0
    except KeyboardInterrupt:
        sys.stderr.write("\ncpp: interrupted - progress was saved, run 'cpp' to continue\n")
        return 130


def _main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = ui.console(no_color=args.no_color, quiet=args.quiet or not args.verbose)
    paths = _resolve_paths(args)

    if args.list_steps:
        return _print_steps(console)
    if args.reset:
        store = state.StateStore(paths.state_file, cli_version=setup_mod.version())
        removed = store.clear_all()
        console.ok("state cleared (%d entries removed)" % len(removed))
        console.detail("every step will run again on the next 'cpp'")
    if args.status:
        return _print_status(console, paths)
    if args.uninstall:
        return _run_uninstall(console, paths, assume_yes=args.yes)
    if args.run_test:
        return _run_test(console, paths)

    known = set(state.STEP_INDEX)
    for flag, value in (("--force", args.force), ("--only", args.only)):
        if value:
            unknown = [s for s in value if s not in known]
            if unknown:
                console.error("unknown step(s) for %s: %s" % (flag, ", ".join(unknown)))
                console.detail("run 'cpp --list-steps' to see the valid ids")
                return 2

    try:
        runner = setup_mod.Setup(
            console=console,
            paths=paths,
            offline=args.offline,
            force_steps=args.force or [],
            only_steps=args.only or [],
            skip_vscode=args.skip_vscode,
            user_name=args.name or "",
            assume_yes=args.yes,
        )
        return runner.run()
    except KeyboardInterrupt:
        console.blank()
        console.error("interrupted - progress was saved, run 'cpp' to continue")
        return 130
    except Exception as exc:  # last-resort guard, never a raw traceback
        console.error("unexpected error: %s: %s" % (type(exc).__name__, exc))
        if args.verbose:
            traceback.print_exc()
        else:
            console.detail("re-run with -v for a traceback")
        return 1


if __name__ == "__main__":
    sys.exit(main())
