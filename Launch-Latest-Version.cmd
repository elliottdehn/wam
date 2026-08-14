@echo off
setlocal
set "WAM_ROOT=%~dp0"
set "WAM_PYTHON=%WAM_ROOT%.venv\Scripts\python.exe"

rem Run only the validated checkout environment; never fall back to a different Python.
if not exist "%WAM_PYTHON%" (
  echo WAM is not installed. Run Setup-WAM.ps1 first. 1>&2
  exit /b 1
)

if /I "%~1"=="edit" goto open_editor

"%WAM_PYTHON%" -m wam.codex_cli %*
exit /b %ERRORLEVEL%

:open_editor
shift
rem %%* does not follow SHIFT in cmd.exe, so forward the supported editor
rem arguments explicitly after removing the edit subcommand.
"%WAM_PYTHON%" -m wam.editor_bridge "%~1" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%
