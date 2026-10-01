"""Unit tests for the pieces that must never regress.

Run with:  python3 tests/test_units.py
"""

import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import archive, compiler, envpath, net, state, templates, ui, util, verify, vscode  # noqa: E402
from lib import setup as setup_mod  # noqa: E402


def make_compiler(bin_dir: Path) -> str:
    """A fake compiler that behaves like a real one for the unit tests."""
    util.ensure_dir(bin_dir)
    exe = bin_dir / ("g++.exe" if util.IS_WINDOWS else "g++")
    if util.IS_WINDOWS:
        exe.write_text("@echo g++ (fake) 13.2.0\r\n")
    else:
        exe.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            '  --version|-dumpversion) echo "g++ (fake) 13.2.0"; exit 0 ;;\n'
            "esac\n"
            'if [ "$1" = "-std=c++20" ]; then exit 0; fi\n'
            'out=""; for a in "$@"; do case "$a" in -o) next=1 ;; *) if [ "$next" = "1" ]; then out=$a; next=0; fi ;; esac; done\n'
            'printf "#!/bin/sh\\necho fake program\\n" > "$out"; chmod +x "$out"\n'
        )
        os.chmod(str(exe), 0o755)
    return str(exe)


class TestTemplates(unittest.TestCase):
    def test_program_contains_required_message(self):
        program = templates.WELCOME_PROGRAM
        self.assertIn("Enter your name:", program)
        self.assertIn("Congratulations", program)
        self.assertIn("You have set up your C++ environment", program)
        self.assertIn("continue with your first program", program)
        self.assertTrue(all(ord(c) < 128 for c in program), "program must be ASCII for Windows consoles")

    def test_markers_match_program_text(self):
        # The literal lines must appear verbatim; the name is printed from a
        # variable, so its marker is checked by splitting on the concatenation.
        program = templates.WELCOME_PROGRAM
        self.assertIn('"   Congratulations " << name', program)
        for marker in templates.success_markers("Zed")[1:]:
            self.assertIn(marker, program)

    def test_program_is_ascii_and_balanced(self):
        self.assertTrue(all(ord(c) < 128 for c in templates.WELCOME_PROGRAM), "must be ASCII for Windows")
        for opener, closer in (("{", "}"), ("(", ")"), ("[", "]")):
            self.assertEqual(
                templates.WELCOME_PROGRAM.count(opener),
                templates.WELCOME_PROGRAM.count(closer),
                "unbalanced %s%s in the generated program" % (opener, closer),
            )

    def test_parse_jsonc_handles_comments_and_trailing_commas(self):
        text = '{\n  // line\n  "a": 1,\n  /* block */\n  "b": [1, 2,],\n}'
        self.assertEqual(templates.parse_jsonc(text), {"a": 1, "b": [1, 2]})

    def test_parse_jsonc_keeps_slashes_inside_strings(self):
        text = '{"url": "https://example.com//x", "note": "// not a comment"}'
        self.assertEqual(
            templates.parse_jsonc(text),
            {"url": "https://example.com//x", "note": "// not a comment"},
        )

    def test_parse_jsonc_survives_garbage(self):
        for junk in ("", "   ", "not json at all", "[1,2]", '{"unclosed": '):
            self.assertEqual(templates.parse_jsonc(junk), {})

    def test_merge_into_preserves_user_keys_and_backs_up(self):
        directory = Path(tempfile.mkdtemp())
        target = directory / "settings.json"
        target.write_text('{\n  // mine\n  "my.setting": true,\n}\n')
        changed = templates.merge_into(target, {"C_Cpp.default.compilerPath": "/usr/bin/g++"})
        self.assertTrue(changed)
        data = templates.parse_jsonc(target.read_text())
        self.assertTrue(data["my.setting"])
        self.assertEqual(data["C_Cpp.default.compilerPath"], "/usr/bin/g++")
        self.assertTrue((directory / "settings.json.cpp-setup.bak").exists())

    def test_merge_into_is_idempotent(self):
        target = Path(tempfile.mkdtemp()) / "settings.json"
        templates.merge_into(target, {"a": 1})
        self.assertFalse(templates.merge_into(target, {"a": 1}), "second merge must be a no-op")

    def test_settings_reference_the_compiler_and_cpp_standard(self):
        settings = templates.settings_for({"exe": "/opt/tc/bin/g++"}, "linux", "x86_64", ["esbenp.prettier-vscode"])
        self.assertEqual(settings["C_Cpp.default.compilerPath"], "/opt/tc/bin/g++")
        self.assertEqual(settings["C_Cpp.default.cppStandard"], "c++20")
        self.assertEqual(settings["[json]"]["editor.defaultFormatter"], "esbenp.prettier-vscode")
        self.assertTrue(settings["errorLens.enabledDiagnostics"], "Error Lens must be enabled")

    def test_intellisense_mode_covers_all_platforms(self):
        combos = [("windows", "x86_64"), ("windows", "arm64"), ("linux", "x86_64"),
                  ("linux", "arm64"), ("macos", "x86_64"), ("macos", "arm64")]
        for os_name, arch in combos:
            self.assertTrue(templates.intelliSense_mode(os_name, arch))


class TestPathManager(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.manager = envpath.PathManager(home=self.home)
        self.bin_dir = self.home / "toolchain" / "bin"
        util.ensure_dir(self.bin_dir)

    def test_add_is_idempotent(self):
        first = self.manager.add([str(self.bin_dir)])
        second = self.manager.add([str(self.bin_dir)])
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        text = (self.home / ".zshrc").read_text()
        self.assertEqual(text.count(envpath.BEGIN), 1, "the marker must appear exactly once")

    @unittest.skipIf(util.IS_WINDOWS, "posix rc file behaviour")
    def test_remove_clears_the_block(self):
        self.manager.add([str(self.bin_dir)])
        self.assertTrue(self.manager.remove([str(self.bin_dir)]))
        self.assertNotIn(envpath.BEGIN, (self.home / ".zshrc").read_text())
        self.assertEqual(self.manager.remove([str(self.bin_dir)]), [])

    @unittest.skipIf(util.IS_WINDOWS, "posix rc file behaviour")
    def test_block_does_not_duplicate_path(self):
        self.manager.add([str(self.bin_dir)])
        block = (self.home / ".bashrc").read_text()
        self.assertIn('case ":$PATH:" in', block)
        self.assertIn('*":%s:"*)' % self.bin_dir, block, "the guard must test for the directory")
        self.assertIn('*) export PATH="%s:$PATH" ;;' % self.bin_dir, block)
        # Running the block twice must not grow PATH.
        script = "%s\ncase \":$PATH:\" in\n  *\":%s:\"*) ;;\n  *) export PATH=\"%s:$PATH\" ;;\nesac\necho \"$PATH\" | tr ':' '\\n' | grep -cx '%s'\n" % (
            block,
            self.bin_dir,
            self.bin_dir,
            self.bin_dir,
        )
        result = util.run(["sh", "-c", script], timeout=30)
        self.assertEqual(result.stdout.strip(), "1", "the directory must appear exactly once")

    @unittest.skipIf(util.IS_WINDOWS, "posix rc file behaviour")
    def test_persisted_reports_entries(self):
        self.manager.add([str(self.bin_dir)])
        self.assertIn(str(self.bin_dir), self.manager.persisted())
        self.assertTrue(self.manager.has(self.bin_dir))

    @unittest.skipIf(util.IS_WINDOWS, "posix rc file behaviour")
    def test_existing_content_is_preserved(self):
        rc = self.home / ".zshrc"
        rc.write_text("export EDITOR=vim\n")
        self.manager.add([str(self.bin_dir)])
        self.assertIn("export EDITOR=vim", rc.read_text())


class TestArchiveSafety(unittest.TestCase):
    def test_detect_kind(self):
        self.assertEqual(archive.detect_kind("a.zip"), "zip")
        self.assertEqual(archive.detect_kind("a.tar.xz"), "tar")
        self.assertEqual(archive.detect_kind("a.7z"), "7z")
        self.assertEqual(archive.detect_kind("a.dmg"), "dmg")
        self.assertEqual(archive.detect_kind("a.exe"), "exe")

    def test_extract_tar_ball(self):
        import tarfile

        root = Path(tempfile.mkdtemp())
        payload = root / "src.txt"
        payload.write_text("hello")
        tarball = root / "bundle.tar.gz"
        with tarfile.open(str(tarball), "w:gz") as tf:
            tf.add(str(payload), arcname="inner/src.txt")
        dest = root / "out"
        archive.extract(tarball, dest)
        self.assertEqual((dest / "inner" / "src.txt").read_text(), "hello")

    def test_traversal_zip_is_refused(self):
        import zipfile

        root = Path(tempfile.mkdtemp())
        evil = root / "evil.zip"
        with zipfile.ZipFile(str(evil), "w") as zf:
            zf.writestr("../escape.txt", "nope")
        with self.assertRaises(archive.ExtractError):
            archive.extract(evil, root / "out")

    def test_missing_archive_raises(self):
        with self.assertRaises(archive.ExtractError):
            archive.extract(Path(tempfile.mkdtemp()) / "nothing.zip", Path(tempfile.mkdtemp()) / "o")


class TestCompilerDetection(unittest.TestCase):
    def test_detect_returns_none_when_names_are_absent(self):
        self.assertIsNone(compiler.detect(names=("definitely-not-installed-xyz",)))

    def test_query_version_of_real_compiler(self):
        found = compiler.detect()
        if not found:
            self.skipTest("no C++ compiler on this machine")
        self.assertTrue(found.version)
        self.assertTrue(os.path.exists(found.exe))

    def test_broken_compiler_on_path_is_rejected(self):
        directory = Path(tempfile.mkdtemp())
        broken = directory / "c++"
        broken.write_text("#!/bin/sh\nexit 1\n")
        os.chmod(str(broken), 0o755)
        original = os.environ.get("PATH", "")
        os.environ["PATH"] = str(directory) + os.pathsep + original
        try:
            self.assertIsNone(compiler.detect(names=("c++",)))
        finally:
            os.environ["PATH"] = original

    def test_cached_toolchain_detects_a_managed_install(self):
        root = Path(tempfile.mkdtemp())
        bin_dir = root / "bin"
        exe = make_compiler(bin_dir)
        found = compiler.cached_toolchain(root)
        self.assertIsNotNone(found)
        self.assertEqual(found.exe, exe)
        self.assertTrue(found.managed)

    def test_cached_toolchain_ignores_a_broken_install(self):
        root = Path(tempfile.mkdtemp())
        bin_dir = root / "bin"
        util.ensure_dir(bin_dir)
        broken = bin_dir / "g++"
        broken.write_text("#!/bin/sh\nexit 1\n")
        os.chmod(str(broken), 0o755)
        self.assertIsNone(compiler.cached_toolchain(root))

    def test_providers_exist_for_every_platform(self):
        for os_name in ("windows", "linux", "macos"):
            for arch in ("x86_64", "arm64"):
                self.assertTrue(compiler.providers_for(os_name, arch), "%s/%s" % (os_name, arch))

    def test_find_bin_dir_locates_the_compiler(self):
        root = Path(tempfile.mkdtemp())
        make_compiler(root / "bin")
        self.assertEqual(compiler.find_bin_dir(root), root / "bin")

    def test_find_bin_dir_returns_none_when_empty(self):
        self.assertIsNone(compiler.find_bin_dir(Path(tempfile.mkdtemp())))


class TestStateResumption(unittest.TestCase):
    def setUp(self):
        self.directory = Path(tempfile.mkdtemp())
        self.state_file = self.directory / "state.json"

    def store(self):
        return state.StateStore(self.state_file, cli_version=setup_mod.version())

    def test_steps_survive_a_reload(self):
        store = self.store()
        store.start("preflight")
        store.done("preflight", "0.1s", {"detail": "ok"})
        reloaded = self.store()
        self.assertTrue(reloaded.is_done("preflight"))
        self.assertFalse(reloaded.is_done("compiler_mingw"))
        self.assertEqual(reloaded.record("preflight").get("data", {}).get("detail"), "ok")

    def test_resume_point_is_the_first_unfinished_step(self):
        store = self.store()
        store.done("preflight")
        store.done("compiler_detect")
        self.assertEqual(store.resume_point(), "compiler_mingw")

    def test_resume_point_after_failure_is_that_step(self):
        store = self.store()
        store.done("preflight")
        store.done("compiler_detect")
        store.start("compiler_mingw")
        store.failed("compiler_mingw", "network down", "check your connection")
        self.assertEqual(store.resume_point(), "compiler_mingw")
        self.assertEqual(store.first_failure(), "compiler_mingw")
        self.assertEqual(store.record("compiler_mingw").get("hint"), "check your connection")

    def test_running_step_counts_as_resumable(self):
        store = self.store()
        store.done("preflight")
        store.done("compiler_detect")
        store.start("compiler_mingw")  # crashed here
        self.assertEqual(store.resume_point(), "compiler_mingw")
        self.assertEqual(store.status("compiler_mingw"), state.STATUS_RUNNING)

    def test_corrupt_state_file_does_not_crash(self):
        self.state_file.write_text("{not json at all")
        store = self.store()
        self.assertFalse(store.is_done("preflight"))
        self.assertTrue((self.directory / "state.corrupt.json").exists())

    def test_reset_clears_steps(self):
        store = self.store()
        store.done("preflight")
        store.clear_all()
        self.assertFalse(self.store().is_done("preflight"))

    def test_data_survives_a_reload(self):
        store = self.store()
        store.put("user_name", "Ada")
        self.assertEqual(self.store().get("user_name"), "Ada")

    def test_attempt_count_increases(self):
        store = self.store()
        store.start("compiler_mingw")
        store.failed("compiler_mingw", "boom")
        self.assertEqual(self.store().attempt_count("compiler_mingw"), 1)

    def test_state_file_is_valid_json(self):
        store = self.store()
        store.done("preflight")
        store.save()
        data = json.loads(self.state_file.read_text())
        self.assertEqual(data["schema"], state.SCHEMA_VERSION)
        self.assertEqual(data["steps"]["preflight"]["status"], state.STATUS_DONE)


class TestSingleInstanceLock(unittest.TestCase):
    def test_second_acquire_is_refused(self):
        path = Path(tempfile.mkdtemp()) / "cpp.lock"
        first = state.SingleInstanceLock(path)
        self.assertTrue(first.acquire())
        try:
            second = state.SingleInstanceLock(path)
            self.assertFalse(second.acquire())
        finally:
            first.release()
        third = state.SingleInstanceLock(path)
        self.assertTrue(third.acquire(), "the lock must be reusable after release")
        third.release()


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp())
        self.real = compiler.detect()

    def test_check_output_accepts_the_right_name(self):
        output = "".join(marker + "\n" for marker in templates.success_markers("Ada"))
        ok, missing = verify.check_output("Ada", output)
        self.assertTrue(ok)
        self.assertEqual(missing, [])

    def test_check_output_rejects_a_different_name(self):
        output = "".join(marker + "\n" for marker in templates.success_markers("Ada"))
        ok, missing = verify.check_output("Grace", output)
        self.assertFalse(ok)
        self.assertIn("Congratulations Grace", missing)

    def test_check_output_handles_crlf(self):
        output = "\r\n".join(templates.success_markers("Ada"))
        self.assertTrue(verify.check_output("Ada", output)[0])

    def test_write_program_is_idempotent(self):
        first, created_first = verify.write_program(self.workspace)
        second, created_second = verify.write_program(self.workspace)
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first, second)
        self.assertIn("Congratulations", first.read_text())

    def test_write_program_force_rewrites(self):
        verify.write_program(self.workspace)
        source = verify.source_path(self.workspace)
        source.write_text("// user edited me")
        _path, created = verify.write_program(self.workspace, force=True)
        self.assertTrue(created)
        self.assertIn("Congratulations", source.read_text())

    def test_missing_binary_raises_a_clear_error(self):
        with self.assertRaises(verify.VerifyError):
            verify.run_program(self.workspace / "nope", "Ada")

    def test_compile_failure_reports_all_standards(self):
        if not self.real:
            self.skipTest("no C++ compiler on this machine")
        broken = self.workspace / "broken.cpp"
        broken.write_text("this is not valid C++ at all;")
        with self.assertRaises(verify.VerifyError) as caught:
            verify.compile_program(self.real.exe, broken, self.workspace / "out")
        message = str(caught.exception)
        self.assertIn("c++20", message)
        self.assertIn("c++17", message)

    def test_cross_compile_check_is_skipped_on_windows(self):
        if not util.IS_WINDOWS:
            self.skipTest("windows only branch")
        self.assertIsNone(verify.cross_compile_check("g++", self.workspace))


class TestVsCodeTemplates(unittest.TestCase):
    def test_four_core_packs_are_present(self):
        self.assertEqual(len(vscode.CORE_PACKS), 4)
        ids = vscode.all_extension_ids(vscode.CORE_PACKS)
        self.assertIn("ms-vscode.cpptools-extension-pack", ids)

    def test_first_time_extras_include_prettier_and_errorlens(self):
        ids = vscode.all_extension_ids(vscode.FIRST_TIME_EXTRAS)
        self.assertIn("esbenp.prettier-vscode", ids)
        self.assertIn("usernamehw.errorlens", ids)

    def test_no_duplicate_extension_ids(self):
        every = vscode.all_extension_ids(vscode.CORE_PACKS) + vscode.all_extension_ids(vscode.FIRST_TIME_EXTRAS)
        self.assertEqual(len(every), len(set(every)))

    def test_every_pack_explains_itself(self):
        for pack in vscode.CORE_PACKS + vscode.FIRST_TIME_EXTRAS:
            self.assertTrue(pack.get("why"), "%s needs a description" % pack["id"])

    def test_config_files_are_written_and_parseable(self):
        workspace = Path(tempfile.mkdtemp())
        ids = vscode.all_extension_ids(vscode.CORE_PACKS) + vscode.all_extension_ids(vscode.FIRST_TIME_EXTRAS)
        written = vscode.write_config(workspace, {"exe": "/usr/bin/g++"}, "linux", "x86_64", ids)
        self.assertEqual(len(written), 5)
        for path in written:
            self.assertTrue(templates.parse_jsonc(Path(path).read_text()), "%s must be valid JSONC" % path)
        extensions = json.loads((workspace / ".vscode" / "extensions.json").read_text())
        self.assertIn("ms-vscode.cpptools", extensions["recommendations"])

    def test_config_write_preserves_user_settings(self):
        workspace = Path(tempfile.mkdtemp())
        util.ensure_dir(workspace / ".vscode")
        (workspace / ".vscode" / "settings.json").write_text('{"editor.fontSize": 20}')
        vscode.write_config(workspace, {"exe": "/usr/bin/g++"}, "linux", "x86_64", ["a.b"])
        data = templates.parse_jsonc((workspace / ".vscode" / "settings.json").read_text())
        self.assertEqual(data["editor.fontSize"], 20)

    def test_cli_shim_is_executable(self):
        directory = Path(tempfile.mkdtemp())
        shim = vscode._write_cli_shim(directory / "code", Path("/opt/vscode/bin/code"))
        if util.IS_WINDOWS:
            self.assertTrue(shim.exists())
        else:
            self.assertTrue(shim.stat().st_mode & stat.S_IXUSR)
            self.assertIn("exec", shim.read_text())


class TestNetHelpers(unittest.TestCase):
    def test_select_asset_picks_the_right_pattern(self):
        assets = [
            {"name": "llvm-mingw-20250101-ucrt-x86_64.zip", "size": 10, "url": "u1"},
            {"name": "llvm-mingw-20250101-ubuntu-x86_64.tar.xz", "size": 20, "url": "u2"},
        ]
        found = net.select_asset(assets, [r"^llvm-mingw.*ubuntu.*x86_64\.tar\.xz$"])
        self.assertIsNotNone(found)
        self.assertEqual(found["name"], "llvm-mingw-20250101-ubuntu-x86_64.tar.xz")

    def test_select_asset_returns_none_when_nothing_matches(self):
        self.assertIsNone(net.select_asset([{"name": "x.zip", "size": 1, "url": "u"}], [r"^nomatch$"]))

    def test_select_asset_ignores_empty_asset_lists(self):
        self.assertIsNone(net.select_asset([], [r".*"]))


class TestUtil(unittest.TestCase):
    def test_os_and_arch_are_known(self):
        self.assertIn(util.os_name(), ("windows", "linux", "macos"))
        self.assertIn(util.arch(), ("x86_64", "arm64", "x86", "arm"))

    def test_run_reports_a_missing_executable(self):
        result = util.run(["definitely-not-a-command-xyz"])
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 127)

    def test_run_captures_stdin(self):
        result = util.run(["cat"], input_text="Ada\n")
        self.assertTrue(result.ok)
        self.assertEqual(result.stdout.strip(), "Ada")

    def test_human_size_is_readable(self):
        self.assertTrue(util.human_size(1024).endswith("KB"))
        self.assertTrue(util.human_size(0).strip())

    def test_atomic_write_and_read(self):
        target = Path(tempfile.mkdtemp()) / "a" / "b.txt"
        util.atomic_write_text(target, "content")
        self.assertEqual(util.read_text(target), "content")

    def test_read_text_default_for_missing_file(self):
        self.assertEqual(util.read_text(Path(tempfile.mkdtemp()) / "missing"), "")

    def test_which_finds_and_misses(self):
        self.assertIsNone(util.which("definitely-not-a-command-xyz"))
        self.assertTrue(util.which("sh") or util.which("cmd"))


class TestUi(unittest.TestCase):
    def test_console_writes_to_the_given_stream(self):
        stream = io.StringIO()
        console = ui.Console(stream=stream, no_color=True)
        console.ok("done")
        console.detail("detail")
        self.assertIn("done", stream.getvalue())

    def test_quiet_suppresses_details(self):
        stream = io.StringIO()
        console = ui.Console(stream=stream, no_color=True, quiet=True)
        console.detail("hidden")
        self.assertNotIn("hidden", stream.getvalue())

    def test_ask_uses_the_default_without_input(self):
        console = ui.Console(stream=io.StringIO(), no_color=True)
        # ask() reads sys.stdin, so feed it an explicit empty stream. Relying on
        # the real stdin reaching EOF only works under `pytest -s`; under normal
        # output capture pytest swaps stdin for a stub that raises OSError.
        with mock.patch("sys.stdin", io.StringIO("")):
            self.assertEqual(console.ask("Name?", default="Coder"), "Coder")

    def test_strip_ansi_removes_colour_codes(self):
        self.assertEqual(ui.strip_ansi("\033[32mgreen\033[0m"), "green")


if __name__ == "__main__":
    unittest.main(verbosity=2)
