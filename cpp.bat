@echo off
REM ---------------------------------------------------------------------------
REM  cpp - one command C++ environment setup
REM
REM  Windows Command Prompt and PowerShell.
REM
REM  Usage:
REM      cpp                run (or resume) the whole setup
REM      cpp --help         every available option
REM
REM  This launcher is deliberately tiny.  Its only jobs are:
REM    1. find a usable Python 3 (the py launcher, then python),
REM    2. make sure the repository files are where they should be,
REM    3. hand over to cpp.py, where all the real work happens.
REM
REM  Keep this folder on your PATH (install.bat does that) to type cpp anywhere.
REM ---------------------------------------------------------------------------
setlocal

set "CPP_DIR=%~dp0"
if "%CPP_DIR:~-1%"=="\" set "CPP_DIR=%CPP_DIR:~0,-1%"
set "CPP_ENTRY=%CPP_DIR%\cpp.py"
set "CPP_ENGINE=%CPP_DIR%\lib\cli.py"

if not exist "%CPP_ENTRY%" (
    echo cpp: cannot find cpp.py in "%CPP_DIR%" 1>&2
    echo      re-clone the repository, then run cpp again. 1>&2
    exit /b 1
)
if not exist "%CPP_ENGINE%" (
    echo cpp: the setup engine lib\cli.py is missing from "%CPP_DIR%" 1>&2
    echo      re-clone the repository, then run cpp again. 1>&2
    exit /b 1
)

REM --- UTF-8 so the output and the C++ test program's greeting stay readable --
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM --- find Python 3.6+ (the minimum the engine supports) ----------------------
set "CPP_PY="
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 6) else 1)" >nul 2>&1
if not errorlevel 1 set "CPP_PY=py -3"
if not defined CPP_PY (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 6) else 1)" >nul 2>&1
    if not errorlevel 1 set "CPP_PY=python"
)
if not defined CPP_PY (
    where python3 >nul 2>&1
    if not errorlevel 1 (
        python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 6) else 1)" >nul 2>&1
        if not errorlevel 1 set "CPP_PY=python3"
    )
)

if not defined CPP_PY (
    echo cpp: Python 3.6 or newer is required but was not found. 1>&2
    echo. 1>&2
    echo      1. Install it from https://www.python.org/downloads/ 1>&2
    echo         ^(tick "Add python.exe to PATH" during setup^) 1>&2
    echo      2. Or from the Microsoft Store:  winget install Python.Python.3.12 1>&2
    echo. 1>&2
    echo      Then open a NEW terminal and run cpp again. 1>&2
    exit /b 1
)

REM --- run --------------------------------------------------------------------
pushd "%CPP_DIR%" >nul 2>&1
%CPP_PY% "%CPP_ENTRY%" %*
set "CPP_EXIT=%ERRORLEVEL%"
popd >nul 2>&1
exit /b %CPP_EXIT%
