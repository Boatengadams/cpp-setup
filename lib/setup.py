"""The setup pipeline: every step, in order, with resume support.

Each step is small, idempotent and records its outcome in the state store, so
a failure - network drop, closed lid, Ctrl+C - never loses more than the step
that was running.  Re-running ``cpp`` picks up at the first step that is not
marked done.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import compiler as compiler_mod
from . import envpath, net, state, ui, util, verify, vscode


class SetupError(Exception):
    """A critical step failed; the run stops with an actionable message."""

    def __init__(self, message: str, hint: str = ""):
        super(SetupError, self).__init__(message)
        self.hint = hint


class Paths(object):
    """Where everything lives: user data, caches, the toolchain, the workspace."""

    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home else Path(os.path.expanduser("~"))
        self.root = self.home / ".cpp-setup"
        self.cache = self.root / "cache"
        self.toolchain = self.root / "toolchain"
        self.state_file = self.root / "state.json"
        self.journal = self.root / "setup.log"
        self.vscode_dir = self.root / "vscode"
        self.workspace = self.root / "workspace"

    def ensure(self) -> "Paths":
        for directory in (self.root, self.cache, self.toolchain, self.workspace):
            util.ensure_dir(directory)
        return self


class Setup(object):
    """Runs the steps.  ``run`` is the only entry point callers need."""

    def __init__(
        self,
        console=None,
        paths: Optional[Paths] = None,
        offline: bool = False,
        force_steps: Optional[List[str]] = None,
        only_steps: Optional[List[str]] = None,
        skip_vscode: bool = False,
        user_name: str = "",
        assume_yes: bool = False,
    ):
        self.console = console or ui.console()
        self.paths = (paths or Paths()).ensure()
        self.offline = offline
        self.force_steps = set(force_steps or [])
        self.only_steps = set(only_steps or [])
        self.skip_vscode = skip_vscode
        self.assume_yes = assume_yes
        self.user_name = user_name
        self.store = state.StateStore(self.paths.state_file, cli_version=version())
        self.downloader = net.Downloader(console=self.console, log=self.store.log, offline=offline)
        self.paths_manager = envpath.PathManager(console=self.console, log=self.store.log)
        self._name_resolved = bool(user_name)
        self._verify_output = ""

    # -- helpers -----------------------------------------------------------
    def log(self, event: str, **fields) -> None:
        self.store.log(event, **fields)

    def _data(self, key: str, default=None):
        return self.store.get(key, default)

    def _remember(self, key: str, value) -> None:
        self.store.put(key, value)
        self.store.save()

    def _active(self, step_id: str) -> bool:
        if self.only_steps:
            return step_id in self.only_steps
        return True

    def _should_run(self, step: state.StepSpec) -> bool:
        if not self._active(step.id):
            return False
        if step.id in self.force_steps:
            return True
        return not self.store.is_done(step.id)

    def _resolve_name(self) -> str:
        if self._name_resolved and self.user_name:
            return self.user_name
        remembered = self._data("user_name", "")
        if remembered:
            self.console.detail("using the name you entered last time: %s" % remembered)
            self.user_name = remembered
        else:
            self.user_name = self.console.ask("What is your name?", default="Coder", allow_empty=True) or "Coder"
        self._name_resolved = True
        self._remember("user_name", self.user_name)
        return self.user_name

    # ==================================================================
    # steps
    # ==================================================================

    def step_preflight(self, step: state.StepSpec) -> str:
        """Sanity-check the platform and create the working directories."""
        self.console.detail("platform: %s %s (%s)" % (util.os_name(), util.arch(), util.os_release()))
        self.console.detail("workspace: %s" % self.paths.workspace)
        self.console.detail("state file: %s" % self.paths.state_file)
        for tool, purpose in (
            ("tar", "extracting the toolchain"),
            ("curl", "downloading releases"),
        ):
            if not util.which(tool) and not util.which("wget"):
                self.console.warn("%s is missing; downloads may be slower or fail" % tool)
            else:
                break
        if util.IS_MACOS and not util.which("xcode-select"):
            self.console.note("install the Xcode command line tools with: xcode-select --install")
        if not self._name_resolved:
            self._resolve_name()
        return "%s / %s, workspace %s" % (util.os_name(), util.arch(), self.paths.workspace)

    def step_compiler_detect(self, step: state.StepSpec) -> str:
        """Reuse a managed toolchain or an already installed compiler."""
        managed = compiler_mod.cached_toolchain(self.paths.toolchain)
        if managed:
            self.console.ok("Using the toolchain installed by a previous run")
            self.console.detail("%s (%s)" % (managed.exe, managed.version))
            self._remember("compiler", managed.as_dict())
            return "%s (%s)" % (managed.exe, managed.version)
        if self.offline:
            existing = compiler_mod.detect()
            if existing:
                self._remember("compiler", existing.as_dict())
                return "%s (offline mode)" % existing.version
            raise SetupError(
                "Offline mode is on and no compiler is installed.",
                "Run 'cpp' without --offline so the toolchain can be downloaded.",
            )
        existing = compiler_mod.detect()
        if existing:
            self.console.ok("A C++ compiler is already installed")
            self.console.detail("%s (%s)" % (existing.exe, existing.version))
            self._remember("compiler", existing.as_dict())
            return "%s (%s)" % (existing.exe, existing.version)
        # Nothing on PATH: make sure a host compiler exists so we can run
        # the test program natively, and MinGW for cross builds.
        self.console.info("No C++ compiler found yet - one will be installed")
        host = compiler_mod.ensure_native(console=self.console, log=self.log)
        if host:
            self.console.ok("Host compiler available: %s" % host.version)
            self._remember("host_compiler", host.as_dict())
            return "host compiler %s" % host.version
        raise SetupError(
            "No C++ compiler is available on this machine.",
            "Install build tools and re-run 'cpp':\n"
            "  Ubuntu/Debian : sudo apt install build-essential\n"
            "  Fedora        : sudo dnf install gcc-c++\n"
            "  Arch          : sudo pacman -S gcc\n"
            "  macOS         : xcode-select --install",
        )

    def step_compiler_mingw(self, step: state.StepSpec) -> str:
        """Install the newest MinGW-w64 toolchain."""
        if self.offline:
            raise SetupError(
                "Offline mode is on, so the MinGW-w64 toolchain cannot be downloaded.",
                "Re-run 'cpp' without --offline.",
            )
        self.console.info("Installing the latest MinGW-w64 toolchain (this is the big download)")
        toolchain = compiler_mod.install_mingw(
            self.paths.toolchain,
            self.paths.cache,
            self.downloader,
            console=self.console,
            log=self.log,
            force=step.id in self.force_steps,
        )
        data = toolchain.as_dict()
        self._remember("compiler", data)
        self._remember("toolchain_bin", str(toolchain.bin_dir))
        return "%s (%s) at %s" % (Path(toolchain.exe).name, toolchain.version, toolchain.root)

    def step_compiler_path(self, step: state.StepSpec) -> str:
        """Put the toolchain on PATH permanently and for this session."""
        toolchain_bin = self._data("toolchain_bin")
        if not toolchain_bin:
            current = self._data("compiler") or {}
            if current.get("bin_dir"):
                toolchain_bin = current["bin_dir"]
        directories: List[str] = []
        if toolchain_bin:
            directories.append(str(toolchain_bin))
            parent = Path(toolchain_bin).parent
            if (parent / "bin").is_dir() and str(parent / "bin") not in directories:
                directories.append(str(parent / "bin"))
        if not directories:
            self.console.skip("PATH already contains a working C++ compiler")
            return "no managed toolchain to add"
        added = self.paths_manager.add(directories)
        if added:
            self.console.ok("Added to the PATH for every new terminal: %s" % ", ".join(added))
        else:
            self.console.skip("The PATH already contains the toolchain")
        self.console.detail("persisted in %s" % self.paths_manager.describe())
        self._remember("path_dirs", directories)
        return "PATH updated (%s)" % ", ".join(directories)

    def step_compiler_verify(self, step: state.StepSpec) -> str:
        """Prove the compiler is reachable from a brand new shell."""
        name = Path((self._data("compiler") or {}).get("exe", "g++")).name
        ok, info = self.paths_manager.verify_fresh_shell(name)
        if ok:
            self.console.ok("%s is available globally in a new shell" % name)
            self.console.detail(info)
            return info
        self.console.warn("%s is not visible in a brand new shell yet" % name)
        self.console.detail("open a new terminal, or source your shell profile, and re-run 'cpp'")
        self.console.detail("searched: %s" % info)
        raise SetupError(
            "The compiler could not be found from a fresh shell process.",
            "Close every open terminal and open a new one, then run 'cpp' again.\n"
            "The setup state is saved, so the next run continues from this point.",
        )

    # -- VS Code -----------------------------------------------------------

    def step_vscode_detect(self, step: state.StepSpec) -> str:
        found = vscode.detect(self.console)
        if found:
            self._remember("vscode", found.as_dict())
            return "%s at %s" % (found.version, found.cli)
        self.console.info("Visual Studio Code is not installed yet - it will be installed")
        self.store.put("vscode", None)
        self.store.save()
        return "not installed"

    def step_vscode_install(self, step: state.StepSpec) -> str:
        existing = self._data("vscode")
        if existing:
            self.console.skip("Visual Studio Code is already installed")
            return existing.get("version", "installed")
        if self.skip_vscode:
            self.store.skipped(step.id, "--skip-vscode was passed")
            return "skipped"
        if self.offline:
            self.store.skipped(step.id, "offline mode")
            return "skipped (offline)"
        if util.IS_LINUX and not util.which("apt-get") and not util.which("dnf"):
            self.console.detail("no system package manager detected, using a user-local install")
        self.console.info("Downloading Visual Studio Code")
        installed = vscode.install(
            self.downloader,
            self.paths.vscode_dir,
            self.paths.cache,
            console=self.console,
            log=self.log,
        )
        self._remember("vscode", installed.as_dict())
        return "%s at %s" % (installed.version, installed.cli)

    def _vscode(self) -> Optional[vscode.VsCode]:
        data = self._data("vscode")
        if not data:
            return None
        # Trust the recorded path only if it still answers.
        cli = data.get("cli")
        if cli and Path(cli).exists() and vscode.query_version(cli):
            return vscode.VsCode(cli, data.get("version", ""), app=Path(data["app"]) if data.get("app") else None)
        return vscode.detect(self.console)

    def step_vscode_extensions(self, step: state.StepSpec) -> str:
        app = self._vscode()
        if app is None:
            raise SetupError("Visual Studio Code is unavailable, so extensions cannot be installed.")
        wanted = vscode.CORE_PACKS
        present = set(vscode.list_extensions(app))
        missing = [p for p in wanted if p["id"].lower() not in present]
        if not missing and step.id not in self.force_steps:
            self.console.ok("All %d C++ extension packs are already installed" % len(wanted))
            return "%d/%d present" % (len(wanted), len(wanted))
        if not missing:
            self.console.info("Reinstalling the extension packs (--force)")
            missing = list(wanted)
        installed, failed = vscode.install_extensions(app, missing, console=self.console, log=self.log)
        if not installed and failed:
            raise SetupError(
                "None of the C++ extension packs could be installed.",
                "Check your internet connection or proxy, then re-run 'cpp'. "
                "You can also install them by hand from the Extensions view: %s"
                % ", ".join(pack["id"] for pack in vscode.CORE_PACKS),
            )
        total = len(present) + len(installed)
        return "%d installed, %d failed" % (len(installed), len(failed))

    def step_vscode_extras(self, step: state.StepSpec) -> str:
        """Prettier, Error Lens and auto-complete helpers (first-time setup)."""
        app = self._vscode()
        if app is None:
            raise SetupError("Visual Studio Code is unavailable, so the extra extensions cannot be installed.")
        if step.id in self.force_steps:
            self.console.info("Reinstalling the first-time extras (--force)")
            missing = list(vscode.FIRST_TIME_EXTRAS)
        else:
            present = set(vscode.list_extensions(app))
            missing = [p for p in vscode.FIRST_TIME_EXTRAS if p["id"].lower() not in present]
        if not missing:
            self.console.ok("Prettier, Error Lens and auto-complete helpers are already installed")
            return "already present"
        installed, failed = vscode.install_extensions(app, missing, console=self.console, log=self.log)
        return "%d installed, %d failed" % (len(installed), len(failed))

    def step_vscode_config(self, step: state.StepSpec) -> str:
        app = self._vscode()
        if app is None:
            raise SetupError("Visual Studio Code is unavailable, so the project config cannot be generated.")
        extension_ids = vscode.all_extension_ids(vscode.CORE_PACKS) + vscode.all_extension_ids(vscode.FIRST_TIME_EXTRAS)
        written = vscode.write_config(
            self.paths.workspace,
            self._data("compiler"),
            util.os_name(),
            util.arch(),
            extension_ids,
            console=self.console,
        )
        self._remember("vscode_config", written)
        return "%d files in %s" % (len(written), self.paths.workspace / ".vscode")

    # -- the C++ test ------------------------------------------------------

    def _compiler_exe(self) -> str:
        """Prefer a host compiler for the runnable test; MinGW for Windows builds."""
        data = self._data("compiler") or {}
        exe = data.get("exe")
        if exe and Path(exe).exists():
            return exe
        for name in ("g++", "c++", "clang++"):
            found = util.which(name)
            if found:
                return found
        raise SetupError(
            "No usable C++ compiler was found for the test program.",
            "Re-run 'cpp' to finish the compiler steps.",
        )

    def step_test_project(self, step: state.StepSpec) -> str:
        """Create the C++ program in the workspace and compile it."""
        exe = self._compiler_exe()
        name = self._resolve_name()
        self.console.info("Creating the C++ test program for %s" % name)
        source, _created = verify.write_program(
            self.paths.workspace, force=step.id in self.force_steps, console=self.console
        )
        self.console.detail("source: %s" % source)
        build = verify.compile_program(
            exe,
            source,
            verify.binary_path(self.paths.workspace),
            cwd=self.paths.workspace,
            console=self.console,
        )
        self._remember("build_standard", build["standard"])
        self.console.ok("Compiled cleanly with -std=%s" % build["standard"])
        return "%s (-std=%s)" % (source.name, build["standard"])

    def step_test_run(self, step: state.StepSpec) -> str:
        """Run the program, feed it the name and check the greeting."""
        exe = self._compiler_exe()
        name = self._resolve_name()
        binary = verify.binary_path(self.paths.workspace)
        if not binary.exists():
            raise SetupError(
                "The test program was not built, so it cannot be run.",
                "Re-run 'cpp'; the setup resumes at the compile step.",
            )
        self.console.info("Running the C++ test program")
        run = verify.run_program(binary, name, cwd=self.paths.workspace, console=self.console)
        if run["rc"] != 0:
            raise SetupError(
                "The test program exited with code %d." % run["rc"],
                "Re-run 'cpp' to try again; the toolchain itself is already installed.",
            )
        ok, missing = verify.check_output(name, run["stdout"] + run["stderr"])
        if not ok:
            raise SetupError(
                "The test program ran but did not print the expected greeting (missing %s)."
                % ", ".join(repr(m) for m in missing),
                "This usually means a wrong or wrapped compiler. Re-run 'cpp --force compiler_mingw'.",
            )
        self._verify_output = run["stdout"]
        self._remember("last_test_output", run["stdout"][-2000:])
        self.console.ok("The program ran and printed the expected congratulations")
        return "verified for %s" % name

    def _cross_check(self) -> None:
        """Best-effort extra proof that the MinGW toolchain links Windows code."""
        data = self._data("compiler") or {}
        if not data.get("managed") or util.IS_WINDOWS:
            return
        exe = data.get("exe")
        if not exe or not Path(exe).exists():
            return
        result = verify.cross_compile_check(exe, self.paths.workspace, console=self.console)
        if result and result.get("ok"):
            self.console.detail("cross-compiled %s" % Path(result["binary"]).name)
        elif result:
            self.console.detail("cross-compile check did not pass: %s" % str(result.get("error", ""))[:90])

    # -- summary -----------------------------------------------------------

    def step_summary(self, step: state.StepSpec) -> str:
        """Show what is installed, where, and how to start coding."""
        self._cross_check()
        compiler_data = self._data("compiler") or {}
        name = self._resolve_name()
        rows = [
            ["Compiler", "%s (%s)" % (compiler_data.get("exe", "?"), compiler_data.get("version", "?"))],
            ["Toolchain", str(compiler_data.get("root") or "system")],
            ["PATH entries", ", ".join(self._data("path_dirs") or []) or "system compiler"],
            ["Workspace", str(self.paths.workspace)],
            ["Test program", str(verify.source_path(self.paths.workspace))],
        ]
        vscode_data = self._data("vscode") or {}
        if vscode_data:
            rows.insert(3, ["VS Code", "%s (%s)" % (vscode_data.get("version", "?"), vscode_data.get("cli", "?"))])
        config = self._data("vscode_config") or []
        if config:
            rows.append(["VS Code config", str(Path(config[0]).parent) if config else ""])
        self.console.table(rows, headers=["Setting", "Value"])

        if self._verify_output:
            self.console.blank()
            self.console.raw(self._verify_output.rstrip())
            self.console.blank()
        elif (self.paths.workspace / "cpp_environment_check.exe").exists() or verify.binary_path(self.paths.workspace).exists():
            self.console.blank()
            self.console.info("Run the test program yourself with: %s" % verify.binary_path(self.paths.workspace))
            self.console.blank()
        return "summary written"

    # ==================================================================
    # driver
    # ==================================================================

    def _handler_for(self, step_id: str) -> Callable[[state.StepSpec], str]:
        return {
            "preflight": self.step_preflight,
            "compiler_detect": self.step_compiler_detect,
            "compiler_mingw": self.step_compiler_mingw,
            "compiler_path": self.step_compiler_path,
            "compiler_verify": self.step_compiler_verify,
            "vscode_detect": self.step_vscode_detect,
            "vscode_install": self.step_vscode_install,
            "vscode_extensions": self.step_vscode_extensions,
            "vscode_extras": self.step_vscode_extras,
            "vscode_config": self.step_vscode_config,
            "test_project": self.step_test_project,
            "test_run": self.step_test_run,
            "summary": self.step_summary,
        }[step_id]

    def run(self) -> int:
        """Execute every pending step.  Returns the process exit code."""
        lock = state.SingleInstanceLock(self.paths.root / "cpp.lock")
        if not lock.acquire(timeout=0.0):
            self.console.error("Another 'cpp' setup is already running for this user account.")
            self.console.detail("wait for it to finish, or remove %s if you are sure it is gone" % (self.paths.root / "cpp.lock"))
            return 4
        try:
            return self._run_locked()
        finally:
            lock.release()

    def _run_locked(self) -> int:
        self.console.banner("C++ environment setup", "%s %s" % (util.os_name(), util.arch()))
        pending = [s for s in state.STEPS if self._should_run(s)]
        resume = self.store.resume_point()
        if resume and self.store.has_progress() and not self.only_steps and not self.force_steps:
            self.console.info("Resuming from: %s" % dict((s.id, s.title) for s in state.STEPS).get(resume, resume))
        if not pending:
            self.console.ok("Everything is already set up.")
            self.console.detail("run 'cpp --force summary' to redo the final report, or 'cpp --status' to inspect it")
            if self._verify_output:
                self.console.blank()
                self.console.raw(self._verify_output.rstrip())
            return 0

        timer = ui.Timer()
        total = len(pending)
        for index, step in enumerate(pending, start=1):
            self.console.step(index, total, step.title)
            handler = self._handler_for(step.id)
            self.store.start(step.id)
            self.store.save()
            step_timer = ui.Timer()
            try:
                detail = handler(step)
            except KeyboardInterrupt:
                self.store.failed(step.id, "interrupted by the user (Ctrl+C)")
                self.store.save()
                self.console.blank()
                self.console.error("Setup interrupted. Progress was saved - run 'cpp' again to continue.")
                return 130
            except SetupError as exc:
                self.store.failed(step.id, str(exc), exc.hint)
                self.store.save()
                self.console.blank()
                self.console.error(str(exc))
                if exc.hint:
                    self.console.blank()
                    for line in exc.hint.splitlines():
                        self.console.raw("  " + line)
                self.console.blank()
                self.console.info("Fix the problem above, then run 'cpp' again - it resumes at this step.")
                return 1
            except (compiler_mod.CompilerError, verify.VerifyError, vscode.VsCodeError, net.NetError) as exc:
                self.store.failed(step.id, str(exc))
                self.store.save()
                self.console.blank()
                self.console.error(str(exc))
                self.console.blank()
                self.console.info("Run 'cpp' again to retry - completed steps are skipped.")
                return 1
            except Exception as exc:  # unexpected, but must not lose the state
                self.store.failed(step.id, "%s: %s" % (type(exc).__name__, exc))
                self.store.save()
                self.console.blank()
                self.console.error("Unexpected error in step '%s': %s: %s" % (step.id, type(exc).__name__, exc))
                self.console.detail("the journal at %s has the full traceback" % self.paths.journal)
                self.console.info("Run 'cpp' again to retry - completed steps are skipped.")
                return 1
            self.store.done(step.id, step_timer.human(), {"detail": detail} if detail else None)
            self.console.detail("finished in %s" % step_timer.human())
            self.console.blank()

        self.console.ok("Setup finished in %s" % timer.human())
        return 0


VERSION = "1.0.0"


def version() -> str:
    return VERSION
