"""Generated artefacts: the verification C++ program and the VS Code config.

Everything the tool writes into the user's workspace is produced here so the
wording stays consistent between the program, the config and the final report.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from . import util

VERIFICATION_FILENAME = "cpp_environment_check.cpp"
VERIFICATION_BINARY = "cpp_environment_check"

# ASCII only on purpose: Windows consoles are frequently not UTF-8.
WELCOME_PROGRAM = r'''// ---------------------------------------------------------------------------
//  cpp_environment_check.cpp
//
//  Written by the `cpp` setup tool to prove that the C++ toolchain works
//  end to end: compile, link, run.  Delete it once you start your real work,
//  or keep it around as a "is my setup still healthy?" smoke test.
// ---------------------------------------------------------------------------

#include <algorithm>
#include <iostream>
#include <string>
#include <vector>

int main() {
    std::cout << "\n";
    std::cout << "============================================================\n";
    std::cout << "   C++ toolchain check\n";
    std::cout << "============================================================\n";
#if defined(__clang__)
    std::cout << "   compiler : clang " << __clang_major__ << "." << __clang_minor__ << "\n";
#elif defined(__GNUC__)
    std::cout << "   compiler : gcc " << __GNUC__ << "." << __GNUC_MINOR__ << "\n";
#else
    std::cout << "   compiler : " << __VERSION__ << "\n";
#endif
    std::cout << "   standard : " << __cplusplus << " (" << (long long)__cplusplus / 100 << ")\n";
    std::cout << "   platform : "
#if defined(_WIN32)
              << "Windows"
#elif defined(__APPLE__)
              << "macOS"
#else
              << "Linux"
#endif
              << "\n";
    std::cout << "============================================================\n\n";

    // A real STL exercise proves the standard library is wired up, not just
    // that a compiler binary exists.
    std::vector<std::string> skills;
    skills.push_back("compiling");
    skills.push_back("linking");
    skills.push_back("running");
    skills.push_back("writing C++");
    std::sort(skills.begin(), skills.end());
    std::cout << "   standard library works: ";
    for (std::size_t i = 0; i < skills.size(); ++i) {
        std::cout << skills[i] << (i + 1 < skills.size() ? ", " : "\n");
    }
    std::cout << "\n";

    std::cout << "Enter your name: " << std::flush;
    std::string name;
    if (!std::getline(std::cin, name)) {
        name.clear();
    }
    if (name.empty()) {
        name = "Coder";
    }

    std::cout << "\n";
    std::cout << "============================================================\n";
    std::cout << "   Congratulations " << name << "!\n";
    std::cout << "   You have set up your C++ environment.\n";
    std::cout << "   You are done with your C++ setup - continue with your first program.\n";
    std::cout << "============================================================\n";
    return 0;
}
'''

# What the program must print for the entered name to be considered a pass.
def success_markers(name: str) -> List[str]:
    return [
        "Congratulations %s" % name,
        "You have set up your C++ environment",
        "continue with your first program",
    ]


# Reference output for tests: it must satisfy success_markers() for "Ada".
WELCOME_OUTPUT_SAMPLE = (
    "============================================================\n"
    "   C++ toolchain check\n"
    "============================================================\n"
    "   compiler : gcc 15.3\n"
    "   standard : 202002 (2020)\n"
    "   platform : Linux\n"
    "============================================================\n"
    "\n"
    "   standard library works: compiling, linking, running, writing C++\n"
    "\n"
    "Enter your name: \n"
    "============================================================\n"
    "   Congratulations Ada!\n"
    "   You have set up your C++ environment.\n"
    "   You are done with your C++ setup - continue with your first program.\n"
    "============================================================\n"
)


def intelliSense_mode(os_name: str, arch_name: str) -> str:
    table = {
        ("windows", "x86_64"): "windows-gcc-x64",
        ("windows", "arm64"): "windows-gcc-arm64",
        ("linux", "x86_64"): "linux-gcc-x64",
        ("linux", "arm64"): "linux-gcc-arm64",
        ("macos", "x86_64"): "macos-clang-x64",
        ("macos", "arm64"): "macos-clang-arm64",
    }
    return table.get((os_name, arch_name), "gcc-x64")


def configuration_name(os_name: str) -> str:
    return "Win32" if os_name == "windows" else os_name.capitalize()


# --------------------------------------------------------------------------
# JSONC-tolerant merge helpers
# --------------------------------------------------------------------------

_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _strip_comments(text: str) -> str:
    """Remove // and /* */ comments without touching string contents.

    A regex cannot do this safely: ``"https://example.com"`` is a string, not
    a comment, so the text is walked character by character instead.
    """
    out = []
    index = 0
    length = len(text)
    in_string = False
    quote = ""
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                in_string = False
            index += 1
            continue
        if char in ('"', "'"):
            in_string = True
            quote = char
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length:
            nxt = text[index + 1]
            if nxt == "/":
                end = text.find("\n", index)
                index = length if end == -1 else end
                continue
            if nxt == "*":
                end = text.find("*/", index + 2)
                index = length if end == -1 else end + 2
                continue
        out.append(char)
        index += 1
    return "".join(out)


def parse_jsonc(text: str) -> Dict:
    """Parse JSON that may contain // and /* */ comments and trailing commas."""
    stripped = _strip_comments(text or "")
    stripped = _TRAILING_COMMA.sub(r"\1", stripped)
    if not stripped.strip():
        return {}
    try:
        data = json.loads(stripped)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path, data: Dict, header: str = "") -> Path:
    body = json.dumps(data, indent=2, sort_keys=False)
    return util.atomic_write_text(path, (header + body if header else body) + "\n")


def merge_into(path, updates: Dict, header: str = "") -> bool:
    """Merge ``updates`` into an existing JSONC file, keeping other keys.

    A ``.cpp-setup.bak`` copy is taken the first time an existing file is
    changed, so nothing the user wrote is ever lost.
    """
    path = Path(path)
    existing_text = util.read_text(path) if path.exists() else ""
    existing = parse_jsonc(existing_text) if existing_text.strip() else {}
    merged = dict(existing)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            combined = dict(merged[key])
            combined.update(value)
            merged[key] = combined
        else:
            merged[key] = value
    if existing_text and merged == existing:
        return False
    if existing_text and not (path.parent / (path.name + ".cpp-setup.bak")).exists():
        try:
            util.atomic_write_text(path.parent / (path.name + ".cpp-setup.bak"), existing_text)
        except OSError:
            pass
    write_json(path, merged, header=header)
    return True


# --------------------------------------------------------------------------
# VS Code configuration files
# --------------------------------------------------------------------------

BANNER = "// Generated by the 'cpp' setup tool. Re-run 'cpp' to refresh; a .bak copy keeps your old file.\n"


def settings_for(compiler: Optional[dict], os_name: str, arch_name: str, extensions: List[str]) -> Dict:
    compiler_path = (compiler or {}).get("exe") or ""
    settings: Dict = {
        "C_Cpp.default.compilerPath": compiler_path,
        "C_Cpp.default.cppStandard": "c++20",
        "C_Cpp.default.cStandard": "c17",
        "C_Cpp.default.includePath": ["${workspaceFolder}/**", "${workspaceFolder}/include/**"],
        "C_Cpp.default.defines": ["_CRT_SECURE_NO_WARNINGS"],
        "C_Cpp.intelliSenseEngine": "default",
        "C_Cpp.intelliSenseEngineFallback": "enabled",
        "C_Cpp.autocomplete": "default",
        "C_Cpp.errorSquiggles": "enabled",
        "C_Cpp.dimInactiveRegions": True,
        "C_Cpp.formatting": "default",
        # Auto-complete behaviour for the editor itself.
        "editor.quickSuggestions": {"other": True, "comments": False, "strings": True},
        "editor.suggestOnTriggerCharacters": True,
        "editor.acceptSuggestionOnEnter": "on",
        "editor.wordBasedSuggestions": "matchingDocuments",
        "editor.rulers": [100],
        "files.associations": {"*.h": "cpp", "*.hpp": "cpp", "*.cc": "cpp", "*.cpp": "cpp"},
        "[cpp]": {
            "editor.defaultFormatter": "ms-vscode.cpptools",
            "editor.wordBasedSuggestions": "off",
        },
        # Error Lens renders diagnostics inline on the line that owns them.
        "errorLens.enabledDiagnostics": True,
        "errorLens.enabledDecorators": {"error": True, "warning": True, "info": True, "hint": False},
    }
    # Prettier owns the file types it supports, C++ keeps cpptools.
    if "esbenp.prettier-vscode" in extensions:
        settings["[json]"] = {"editor.defaultFormatter": "esbenp.prettier-vscode"}
        settings["[jsonc]"] = {"editor.defaultFormatter": "esbenp.prettier-vscode"}
        settings["[yaml]"] = {"editor.defaultFormatter": "esbenp.prettier-vscode"}
        settings["[markdown]"] = {"editor.defaultFormatter": "esbenp.prettier-vscode"}
    if os_name == "windows":
        settings["terminal.integrated.defaultProfile.windows"] = "Developer PowerShell"
    settings["files.eol"] = "\r\n" if os_name == "windows" else "\n"
    return settings


def c_cpp_properties_for(compiler: Optional[dict], os_name: str, arch_name: str) -> Dict:
    return {
        "configurations": [
            {
                "name": configuration_name(os_name),
                "includePath": ["${workspaceFolder}/**", "${workspaceFolder}/include/**"],
                "defines": ["_DEBUG", "UNICODE", "_UNICODE", "_CRT_SECURE_NO_WARNINGS"],
                "cStandard": "c17",
                "cppStandard": "c++20",
                "intelliSenseMode": intelliSense_mode(os_name, arch_name),
                "compilerPath": (compiler or {}).get("exe") or "",
            }
        ],
        "version": 4,
    }


def tasks_for(compiler: Optional[dict], os_name: str) -> Dict:
    compiler_path = (compiler or {}).get("exe") or ("g++.exe" if os_name == "windows" else "g++")
    binary = "cpp_environment_check" + (".exe" if os_name == "windows" else "")
    return {
        "version": "2.0.0",
        "tasks": [
            {
                "type": "shell",
                "label": "C++: build and run the check program",
                "command": compiler_path,
                "args": [
                    "-std=c++20",
                    "-Wall",
                    "-Wextra",
                    "-g",
                    "-O0",
                    "-o",
                    binary,
                    VERIFICATION_FILENAME,
                ],
                "group": {"kind": "build", "isDefault": True},
                "problemMatcher": ["$gcc"],
                "presentation": {"reveal": "silent", "clear": True},
            },
            {
                "type": "shell",
                "label": "C++: run the check program",
                "command": "./" + binary,
                "dependsOn": "C++: build and run the check program",
                "dependsOrder": "sequence",
                "presentation": {"reveal": "always", "panel": "dedicated"},
            },
            {
                "type": "shell",
                "label": "C++: build the current file",
                "command": compiler_path,
                "args": [
                    "-std=c++20",
                    "-Wall",
                    "-Wextra",
                    "-g",
                    "${file}",
                    "-o",
                    "${fileDirname}/${fileBasenameNoExtension}${fileExtname:.cpp=.exe}",
                ],
                "group": {"kind": "build", "isDefault": False},
                "problemMatcher": ["$gcc"],
            },
            {
                "type": "shell",
                "label": "C++: clean",
                "command": "cmd" if os_name == "windows" else "rm",
                "args": ["/c", "del", binary] if os_name == "windows" else ["-f", binary],
                "problemMatcher": [],
            },
        ],
    }


def launch_for(os_name: str, task_label: str = "C++: build and run the check program") -> Dict:
    program = "${workspaceFolder}/cpp_environment_check" + (".exe" if os_name == "windows" else "")
    return {
        "version": "0.2.0",
        "configurations": [
            {
                "name": "Debug the check program (gdb/lldb)",
                "type": "cppdbg",
                "request": "launch",
                "program": program,
                "args": [],
                "stopAtEntry": False,
                "cwd": "${workspaceFolder}",
                "environment": [],
                "externalConsole": False,
                "MIMode": "gdb",
                "preLaunchTask": task_label,
            },
            {
                "name": "Debug the current file",
                "type": "cppdbg",
                "request": "launch",
                "program": "${fileDirname}/${fileBasenameNoExtension}",
                "args": [],
                "stopAtEntry": False,
                "cwd": "${fileDirname}",
                "environment": [],
                "externalConsole": False,
                "MIMode": "gdb",
            },
        ],
    }


def extensions_json(extension_ids: List[str]) -> Dict:
    return {"recommendations": list(extension_ids)}
