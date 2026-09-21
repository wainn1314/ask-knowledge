@echo off
setlocal
rem ---------------------------------------------------------------------
rem  One-click STOP for the dev trio: just double-click this file.
rem  Same as: start-all.cmd -Stop
rem    main backend  FastAPI :8000
rem    ask ai backend        :8080
rem    frontend       Vite   :5173
rem  It only calls: powershell -ExecutionPolicy Bypass -File scripts\dev-all.ps1 -Stop
rem  ASCII-only on purpose, so it works on any Windows codepage.
rem ---------------------------------------------------------------------
set "SCRIPT=%~dp0scripts\dev-all.ps1"
if not exist "%SCRIPT%" goto no_script
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Stop
echo.
echo [i] Start again: start-all.cmd
pause
exit /b 0

:no_script
echo [x] Not found: "%SCRIPT%"
pause
exit /b 1
