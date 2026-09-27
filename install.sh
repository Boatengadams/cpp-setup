#!/usr/bin/env sh
# ---------------------------------------------------------------------------
#  install.sh - put the `cpp` command on your PATH (Linux, macOS, WSL, MSYS2)
#
#  After cloning the repository, run:
#      ./install.sh
#  and then just type:  cpp
#
#  It symlinks (or copies) cpp.sh to ~/.local/bin/cpp and adds that folder to
#  your shell's PATH if it is not already there.  Nothing outside your home
#  directory is modified and no administrator rights are needed.
#
#  On Windows use install.bat instead.
# ---------------------------------------------------------------------------
set -eu

SOURCE_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
BIN_DIR=${CPP_BIN_DIR:-"$HOME/.local/bin"}
LAUNCHER="$BIN_DIR/cpp"
SOURCE="$SOURCE_DIR/cpp.sh"

if [ ! -f "$SOURCE" ]; then
    printf 'install.sh: cannot find %s\n' "$SOURCE" >&2
    exit 1
fi

mkdir -p "$BIN_DIR"

# A symlink is preferred so that `git pull` updates the installed command.
if ln -sf "$SOURCE" "$LAUNCHER" 2>/dev/null; then
    echo "cpp installed: $LAUNCHER -> $SOURCE"
else
    cp -f "$SOURCE" "$LAUNCHER"
    chmod 755 "$LAUNCHER"
    echo "cpp installed: $LAUNCHER (copy)"
fi

# --- add ~/.local/bin to PATH ------------------------------------------------
MARKER="# >>> cpp-setup launcher >>>"
add_to_rc() {
    rc=$1
    if [ ! -f "$rc" ]; then
        return 1
    fi
    if grep -qF "$MARKER" "$rc" 2>/dev/null; then
        echo "PATH entry already present in $rc"
        return 0
    fi
    {
        printf '\n%s\n' "$MARKER"
        printf 'export PATH="%s:$PATH"\n# <<< cpp-setup launcher <<<\n' "$BIN_DIR"
    } >> "$rc"
    echo "Added $BIN_DIR to PATH in $rc"
}

# The login shell is what most desktop environments start.
case "$(basename -- "${SHELL:-/bin/sh}")" in
    zsh)
        # Interactive zsh reads .zshrc; a login shell reads .zprofile first.
        # Write to whichever exists, otherwise create the interactive one.
        if [ -f "$HOME/.zshrc" ]; then
            RC_FILES="$HOME/.zshrc"
        elif [ -f "$HOME/.zprofile" ]; then
            RC_FILES="$HOME/.zprofile"
        else
            RC_FILES="$HOME/.zshrc"
        fi
        ;;
    bash)
        if [ -f "$HOME/.bashrc" ]; then
            RC_FILES="$HOME/.bashrc"
        elif [ -f "$HOME/.bash_profile" ]; then
            RC_FILES="$HOME/.bash_profile"
        else
            RC_FILES="$HOME/.bashrc"
        fi
        ;;
    *)
        RC_FILES="$HOME/.profile"
        ;;
esac

# Create the chosen file when this is a brand new account, then write to it.
for rc in $RC_FILES; do
    [ -f "$rc" ] || touch "$rc"
    if add_to_rc "$rc"; then
        ADDED=1
    fi
done

# --- verify ------------------------------------------------------------------
printf '\n'
if "$LAUNCHER" --version >/dev/null 2>&1; then
    echo "Verified: $("$LAUNCHER" --version 2>/dev/null)"
else
    echo "Warning: '$LAUNCHER --version' did not work." >&2
    echo "         Try:  $LAUNCHER --help   and check the output." >&2
fi

cat <<'MSG'

Done. Open a NEW terminal window (or run: exec $SHELL) and then run:

    cpp

It is resumable, so if any step fails, just run it again.
MSG
