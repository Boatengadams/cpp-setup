@echo off
REM ---------------------------------------------------------------------------
REM  install.bat - put the `cpp` command on your PATH (Windows)
REM
REM  After cloning the repository, run:
REM      install.bat
REM  and then just type:  cpp   (in any Command Prompt or PowerShell)
REM
REM  It copies cpp.bat, cpp.py and the lib\ package to
REM  %LOCALAPPDATA%\cpp-setup\bin, creates a cpp.cmd shim, and adds that folder to
REM  your user PATH.  No administrator rights are required and nothing outside
REM  your user profile is touched.
REM ---------------------------------------------------------------------------
setlocal enabledelayedexpansion

set "SRC_DIR=%~dp0"
if "%SRC_DIR:~-1%"=="\" set "SRC_DIR=%SRC_DIR:~0,-1%"

set "BIN_DIR=%LOCALAPPDATA%\cpp-setup\bin"
if not exist "%BIN_DIR%" mkdir "%BIN_DIR%" 2>nul
if not exist "%BIN_DIR%" (
    echo install.bat: could not create %BIN_DIR% 1>&2
    exit /b 1
)

REM The .cmd shim is what makes PowerShell and cmd both find `cpp`.
copy /y "%SRC_DIR%\cpp.bat"  "%BIN_DIR%\cpp.bat"  >nul
copy /y "%SRC_DIR%\cpp.py"   "%BIN_DIR%\cpp.py"   >nul
if errorlevel 1 (
    echo install.bat: could not copy the launcher files 1>&2
    exit /b 1
)

REM cpp.py imports the lib\ package, so the whole tree has to come along.
if exist "%BIN_DIR%\lib" rmdir /s /q "%BIN_DIR%\lib" 2>nul
xcopy /e /i /q /y "%SRC_DIR%\lib" "%BIN_DIR%\lib" >nul
if errorlevel 1 (
    echo install.bat: could not copy the lib\ package 1>&2
    exit /b 1
)
REM Drop any stale bytecode from a previous copy of a different Python version.
for /r "%BIN_DIR%\lib" %%D in (__pycache__) do rmdir /s /q "%%D" 2>nul

REM Point the shim at the bin folder so it does not depend on the clone path.
> "%BIN_DIR%\cpp.cmd" echo @echo off
>>"%BIN_DIR%\cpp.cmd" echo set "CPP_HOME=%BIN_DIR%"
>>"%BIN_DIR%\cpp.cmd" echo call "%BIN_DIR%\cpp.bat" %%*

echo cpp installed: %BIN_DIR%\cpp.cmd

REM --- add to the user PATH (HKCU, no admin needed) -------------------------
set "USER_PATH="
REM PATH may be stored as REG_SZ or REG_EXPAND_SZ, so match either.
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul ^| find "REG_"') do set "USER_PATH=%%B"
set "ADDED="
echo !USER_PATH! | find /i "%BIN_DIR%" >nul && goto :pathdone
if not "!USER_PATH!"=="" (
    set "NEW_PATH=!USER_PATH!;%BIN_DIR%"
) else (
    set "NEW_PATH=%BIN_DIR%"
)
reg add "HKCU\Environment" /v Path /t REG_EXPAND_SZ /d "!NEW_PATH!" /f >nul
if errorlevel 1 (
    echo install.bat: could not update HKCU\Environment 1>&2
    echo              add %BIN_DIR% to your PATH by hand. 1>&2
    exit /b 1
)
set "ADDED=yes"

:pathdone
REM Tell already-running programs that the environment changed.
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" (
    "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Command ^
        "Add-Type -Namespace Win -Name Native -MemberDefinition '[DllImport(\"user32.dll\", SetLastError=true, CharSet=CharSet.Auto)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);'; $r=[UIntPtr]::Zero; [void][Win.Native]::SendMessageTimeout([IntPtr]0xffff, 0x1A, [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$r)" >nul 2>&1
)

echo.
if "%ADDED%"=="yes" (
    echo Added %BIN_DIR% to your user PATH.
) else (
    echo %BIN_DIR% is already on your PATH.
)
echo Done. Open a NEW terminal and then run:  cpp
endlocal
