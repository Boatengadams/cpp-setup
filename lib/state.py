"""Resumable step state: a small, crash-safe, JSON-backed state machine.

Every setup step is recorded here *before* and *after* it runs.  If a step
fails the record keeps the error, so the next ``cpp`` invocation restarts at
exactly that step instead of from the beginning.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import util

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"

SCHEMA_VERSION = 1


class StepSpec(object):
    """Static description of one setup step."""

    __slots__ = ("id", "title", "critical", "group")

    def __init__(self, id: str, title: str, critical: bool = True, group: str = "setup"):
        self.id = id
        self.title = title
        self.critical = critical
        self.group = group


# The canonical step order.  Step ids are stable API: they appear in the state
# file and can be targeted with `cpp --force <id>` / `cpp --only <id>`.
STEPS: List[StepSpec] = [
    StepSpec("preflight", "Preflight checks and workspace setup"),
    StepSpec("compiler_detect", "Detect an existing C++ compiler"),
    StepSpec("compiler_mingw", "Install the latest MinGW-w64 toolchain"),
    StepSpec("compiler_path", "Add the toolchain bin directories to PATH"),
    StepSpec("compiler_verify", "Verify the compiler works globally"),
    StepSpec("vscode_detect", "Detect Visual Studio Code"),
    StepSpec("vscode_install", "Install Visual Studio Code"),
    StepSpec("vscode_extensions", "Install the C++ extension packs"),
    StepSpec("vscode_extras", "Install first-time extras (Prettier, Error Lens, auto-complete)"),
    StepSpec("vscode_config", "Generate the VS Code project configuration"),
    StepSpec("test_project", "Create and build the verification C++ program"),
    StepSpec("test_run", "Run the program and verify the output"),
    StepSpec("summary", "Final report"),
]

STEP_INDEX = {spec.id: index for index, spec in enumerate(STEPS)}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


class StateStore(object):
    """Durable record of which steps completed, failed or were skipped."""

    def __init__(self, path, mirror: Optional[Path] = None, cli_version: str = ""):
        self.path = Path(path)
        self.mirror = Path(mirror) if mirror else None
        self.cli_version = cli_version
        self.data: Dict = {}
        self.journal_path = self.path.with_suffix(".log")
        self.load()

    # -- persistence -------------------------------------------------------
    def load(self) -> Dict:
        raw = util.read_text(self.path, "")
        data = None
        if raw.strip():
            try:
                data = json.loads(raw)
            except ValueError:
                # A corrupt state file must never brick the tool: keep a copy
                # and start a fresh one.
                try:
                    util.atomic_write_text(self.path.with_suffix(".corrupt.json"), raw)
                except OSError:
                    pass
                data = None
        if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
            data = {
                "schema": SCHEMA_VERSION,
                "cli_version": self.cli_version,
                "created_at": _now(),
                "updated_at": _now(),
                "os": util.os_name(),
                "arch": util.arch(),
                "steps": {},
                "data": {},
            }
        data.setdefault("steps", {})
        data.setdefault("data", {})
        self.data = data
        return self.data

    def save(self) -> None:
        self.data["updated_at"] = _now()
        self.data["cli_version"] = self.cli_version
        self.data["os"] = util.os_name()
        self.data["arch"] = util.arch()
        try:
            util.atomic_write_text(
                self.path, json.dumps(self.data, indent=2, sort_keys=True, default=str) + "\n"
            )
        except OSError as exc:
            self.log("state_save_error", error=str(exc))
        if self.mirror is not None:
            try:
                util.atomic_write_text(
                    self.mirror, json.dumps(self.data, indent=2, sort_keys=True, default=str) + "\n"
                )
            except OSError:
                pass

    # -- journal -----------------------------------------------------------
    def log(self, event: str, **fields) -> None:
        record = {"ts": _now(), "event": event}
        record.update(fields)
        try:
            util.ensure_parent(self.journal_path)
            util.append_text(self.journal_path, json.dumps(record, default=str) + "\n")
        except OSError:
            pass

    # -- shared scratch data ----------------------------------------------
    def get(self, key: str, default=None):
        return self.data.get("data", {}).get(key, default)

    def put(self, key: str, value) -> None:
        self.data.setdefault("data", {})[key] = value
        self.save()

    # -- step records ------------------------------------------------------
    def record(self, step_id: str) -> Dict:
        steps = self.data.setdefault("steps", {})
        return steps.setdefault(
            step_id,
            {
                "status": STATUS_PENDING,
                "attempts": 0,
                "started_at": None,
                "finished_at": None,
                "duration_s": None,
                "error": None,
                "hint": None,
                "detail": None,
                "data": {},
            },
        )

    def status(self, step_id: str) -> str:
        return str(self.record(step_id).get("status") or STATUS_PENDING)

    def is_done(self, step_id: str) -> bool:
        return self.status(step_id) == STATUS_DONE

    def is_failed(self, step_id: str) -> bool:
        return self.status(step_id) == STATUS_FAILED

    def attempt_count(self, step_id: str) -> int:
        return int(self.record(step_id).get("attempts") or 0)

    def start(self, step_id: str) -> Dict:
        rec = self.record(step_id)
        rec["status"] = STATUS_RUNNING
        rec["attempts"] = int(rec.get("attempts") or 0) + 1
        rec["started_at"] = _now()
        rec["finished_at"] = None
        rec["error"] = None
        rec["hint"] = None
        self.save()
        self.log("step_start", step=step_id, attempt=rec["attempts"])
        return rec

    def done(self, step_id: str, detail: str = "", data: Optional[dict] = None) -> None:
        rec = self.record(step_id)
        rec["status"] = STATUS_DONE
        rec["finished_at"] = _now()
        rec["detail"] = detail or None
        rec["error"] = None
        rec["hint"] = None
        rec["duration_s"] = _self_duration(rec)
        if data:
            rec.setdefault("data", {}).update(data)
        self.save()
        self.log("step_done", step=step_id, detail=detail)

    def failed(self, step_id: str, error: str, hint: str = "") -> None:
        rec = self.record(step_id)
        rec["status"] = STATUS_FAILED
        rec["finished_at"] = _now()
        rec["error"] = error
        rec["hint"] = hint or None
        rec["duration_s"] = _self_duration(rec)
        self.save()
        self.log("step_failed", step=step_id, error=error, hint=hint)

    def skipped(self, step_id: str, reason: str = "") -> None:
        rec = self.record(step_id)
        rec["status"] = STATUS_SKIPPED
        rec["finished_at"] = _now()
        rec["detail"] = reason or None
        rec["duration_s"] = _self_duration(rec)
        self.save()
        self.log("step_skipped", step=step_id, reason=reason)

    def reset(self, step_ids: Optional[List[str]] = None) -> List[str]:
        """Clear (some) step records so they run again on the next invocation."""
        targets = step_ids or [spec.id for spec in STEPS]
        cleared = []
        for step_id in targets:
            if step_id in self.data.get("steps", {}):
                del self.data["steps"][step_id]
                cleared.append(step_id)
        self.save()
        self.log("state_reset", steps=cleared)
        return cleared

    def clear_all(self) -> None:
        self.data["steps"] = {}
        self.save()
        self.log("state_reset_all")

    # -- queries -----------------------------------------------------------
    def first_failure(self) -> Optional[str]:
        for spec in STEPS:
            if self.status(spec.id) == STATUS_FAILED:
                return spec.id
        return None

    def resume_point(self) -> str:
        """The step a re-run should start from (first non-done step)."""
        for spec in STEPS:
            if self.status(spec.id) not in (STATUS_DONE, STATUS_SKIPPED):
                return spec.id
        return "summary"

    def has_progress(self) -> bool:
        """True once a run has actually recorded some completed work.

        ``resume_point`` always names a step, even on a pristine state file
        (it is then just the first step), so the banner needs a separate test
        for "this is a genuine re-run".
        """
        if not self.data.get("steps"):
            return False
        for spec in STEPS:
            if self.status(spec.id) in (STATUS_DONE, STATUS_SKIPPED, STATUS_FAILED, STATUS_RUNNING):
                return True
        return False

    def counts(self) -> Dict[str, int]:
        counts = {STATUS_DONE: 0, STATUS_FAILED: 0, STATUS_PENDING: 0, STATUS_RUNNING: 0, STATUS_SKIPPED: 0}
        for spec in STEPS:
            counts[self.status(spec.id)] = counts.get(self.status(spec.id), 0) + 1
        return counts

    def rows(self) -> List[List[str]]:
        rows = []
        for spec in STEPS:
            rec = self.record(spec.id)
            status = rec.get("status", STATUS_PENDING)
            marker = {
                STATUS_DONE: "done",
                STATUS_FAILED: "FAILED",
                STATUS_SKIPPED: "skipped",
                STATUS_RUNNING: "interrupted",
                STATUS_PENDING: "pending",
            }.get(status, status)
            detail = rec.get("detail") or rec.get("error") or ""
            rows.append([spec.id, marker, "%dx" % int(rec.get("attempts") or 0), str(detail)[:70]])
        return rows


def _self_duration(rec: Dict) -> Optional[float]:
    started = rec.get("started_at")
    if not started:
        return None
    try:
        started_ts = time.mktime(time.strptime(started, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None
    return round(max(0.0, time.time() - started_ts), 2)


class SingleInstanceLock(object):
    """Advisory cross-platform lock so two ``cpp`` runs cannot collide.

    Uses ``fcntl.flock`` on POSIX and ``msvcrt.locking`` on Windows.  A stale
    lock left behind by a killed process is detected and broken.
    """

    def __init__(self, path):
        self.path = Path(path)
        self._handle = None
        self.acquired = False

    def acquire(self, timeout: float = 0.0) -> bool:
        import time as _time

        util.ensure_parent(self.path)
        deadline = _time.time() + max(0.0, timeout)
        while True:
            try:
                self._handle = open(str(self.path), "a+")
            except OSError:
                return False
            if _try_lock(self._handle):
                self.acquired = True
                try:
                    self._handle.seek(0)
                    self._handle.truncate()
                    self._handle.write("pid=%d\n" % os.getpid())
                    self._handle.flush()
                except OSError:
                    pass
                return True
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
            if _time.time() >= deadline:
                return False
            _time.sleep(0.4)

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if self.acquired:
                _unlock(self._handle)
        except OSError:
            pass
        try:
            self._handle.close()
        except OSError:
            pass
        self._handle = None
        self.acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc_info):
        self.release()
        return False


def _try_lock(handle) -> bool:
    if util.IS_WINDOWS:
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle) -> None:
    if util.IS_WINDOWS:
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        return
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
