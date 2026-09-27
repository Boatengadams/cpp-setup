"""Undo what the setup tool changed: PATH entries, files, state."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

from . import envpath, state, util


def run(root, workspace, console) -> int:
    root = Path(root)
    workspace = Path(workspace)
    store = state.StateStore(root / "state.json")
    dirs = store.get("path_dirs") or []
    if not dirs:
        dirs = _discover_dirs(root)

    if dirs:
        removed = envpath.PathManager().remove(dirs)
        for directory in removed:
            console.ok("removed from PATH: %s" % directory)
        if not removed:
            console.skip("no PATH entries from this tool were found")

    for relative in ("toolchain", "vscode", "cache"):
        target = root / relative
        if target.exists():
            if util.safe_rmtree(target):
                console.ok("deleted %s" % target)

    config_dir = workspace / ".vscode"
    if config_dir.exists():
        backups = list(config_dir.glob("*.cpp-setup.bak"))
        if backups:
            for backup in backups:
                shutil.copyfile(backup, backup.with_suffix(""))
                console.ok("restored %s" % backup.with_suffix(""))
        console.skip("kept %s (your files are still there, delete it if you want)" % config_dir)

    source = workspace / "cpp_environment_check.cpp"
    if source.exists():
        try:
            source.unlink()
            console.ok("deleted %s" % source)
        except OSError as exc:
            console.warn("could not delete %s (%s)" % (source, exc))

    store.clear_all()
    console.blank()
    console.ok("uninstall complete. The system PATH may need a new terminal to update.")
    return 0


def _discover_dirs(root) -> List[str]:
    """Recover the bin directories from the state file's recorded data."""
    found = []
    for exe_key in ("compiler_exe", "mingw_exe", "vscode_cli"):
        exe = _peek(root, exe_key)
        if exe:
            found.append(str(Path(exe).parent))
    return found


def _peek(root, key) -> Optional[str]:
    store = state.StateStore(root / "state.json")
    return store.get(key)
