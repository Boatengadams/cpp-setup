#!/usr/bin/env sh
# ---------------------------------------------------------------------------
#  cpp - one command C++ environment setup
#
#  Linux, macOS, WSL, Git Bash, MSYS2 and Cygwin.
#
#  Usage:
#      ./cpp.sh            run (or resume) the whole setup
#      ./cpp.sh --help     every available option
#
#  This launcher is deliberately tiny.  Its only jobs are:
#    1. find a usable Python 3 (bootstrapping one on minimal systems),
#    2. make sure the repository files are where they should be,
#    3. hand over to lib/cli.py, where all the real work happens.
#
#  It is safe to symlink it onto your PATH as `cpp` - symlinks are resolved
#  below, so `git pull` updates the tool in place.
# ---------------------------------------------------------------------------
set -eu

# --- locate the real repository directory (following symlinks) -------------
SOURCE=$0
while [ -L "$SOURCE" ]; do
    LINK=$(readlink "$SOURCE")
    case $LINK in
        /*) SOURCE=$LINK ;;
        *)  SOURCE=$(dirname -- "$SOURCE")/$LINK ;;
    esac
done
CPP_HOME=$(CDPATH='' cd -- "$(dirname -- "$SOURCE")" && pwd)
export CPP_HOME

if [ ! -f "$CPP_HOME/lib/cli.py" ]; then
    printf 'cpp: the setup engine is missing from %s\n' "$CPP_HOME" >&2
    printf 'cpp: re-clone the repository, then run cpp again.\n' >&2
    exit 1
fi

# --- encoding: keep the box-drawing output intact on legacy terminals -------
PYTHONUTF8=1
PYTHONIOENCODING=utf-8
export PYTHONUTF8 PYTHONIOENCODING

# --- find a Python 3.6+ interpreter -----------------------------------------
python_ok() {
    [ -n "${1:-}" ] || return 1
    command -v "$1" >/dev/null 2>&1 || return 1
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 6) else 1)' >/dev/null 2>&1
}

PY=""
for candidate in "${CPP_PYTHON:-}" python3 python; do
    if python_ok "$candidate"; then
        PY=$candidate
        break
    fi
done

# --- bootstrap Python on minimal systems ------------------------------------
if [ -z "$PY" ]; then
    printf 'cpp: Python 3.6 or newer is required but was not found.\n' >&2
    INSTALLED=0
    if [ "$(id -u)" = "0" ]; then
        SUDO=""
    elif command -v sudo >/dev/null 2>&1; then
        SUDO="sudo"
    else
        SUDO=""
    fi
    if command -v apt-get >/dev/null 2>&1; then
        printf 'cpp: trying to install python3 with apt-get\n' >&2
        # shellcheck disable=SC2086
        $SUDO apt-get install -y python3 >/dev/null 2>&1 && INSTALLED=1 || true
    elif command -v dnf >/dev/null 2>&1; then
        printf 'cpp: trying to install python3 with dnf\n' >&2
        # shellcheck disable=SC2086
        $SUDO dnf install -y python3 >/dev/null 2>&1 && INSTALLED=1 || true
    elif command -v pacman >/dev/null 2>&1; then
        printf 'cpp: trying to install python with pacman\n' >&2
        # shellcheck disable=SC2086
        $SUDO pacman -S --noconfirm python >/dev/null 2>&1 && INSTALLED=1 || true
    elif command -v apk >/dev/null 2>&1; then
        printf 'cpp: trying to install python3 with apk\n' >&2
        # shellcheck disable=SC2086
        $SUDO apk add --no-cache python3 >/dev/null 2>&1 && INSTALLED=1 || true
    elif command -v brew >/dev/null 2>&1; then
        printf 'cpp: trying to install python3 with Homebrew\n' >&2
        brew install python3 >/dev/null 2>&1 && INSTALLED=1 || true
    fi
    if [ "$INSTALLED" = "1" ]; then
        for candidate in python3 python; do
            if python_ok "$candidate"; then
                PY=$candidate
                break
            fi
        done
    fi
fi

if [ -z "$PY" ]; then
    cat >&2 <<'MSG'
cpp: could not find or install Python 3.

Install it with one of:
  Ubuntu / Debian : sudo apt install python3
  Fedora / RHEL   : sudo dnf install python3
  Arch            : sudo pacman -S python
  Alpine          : sudo apk add python3
  macOS           : brew install python3
  Windows (WSL)   : sudo apt install python3

Or download it from https://www.python.org/downloads/
Then run 'cpp' again - your progress is saved.
MSG
    exit 1
fi

# --- run --------------------------------------------------------------------
# exec keeps signal handling (Ctrl+C) and the exit code intact.
cd "$CPP_HOME"
exec "$PY" -m lib.cli "$@"
