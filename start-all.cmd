@echo off
setlocal
rem ---------------------------------------------------------------------
rem  One-click dev launcher: just double-click this file.
rem  ASCII-only on purpose, so it works on any Windows codepage.
rem    main backend  FastAPI :8000   -> http://127.0.0.1:8000/docs
rem    ask ai backend        :8080   -> http://127.0.0.1:8080/docs
rem    frontend       Vite   :5173   -> http://localhost:5173/#/knowledge
rem  It only calls: powershell -ExecutionPolicy Bypass -File scripts\dev-all.ps1
rem  Extra args are forwarded, e.g.:
rem    start-all.cmd -Stop        stop the three ports
rem    start-all.cmd -DryRun      env check only, start nothing
rem    start-all.cmd -BackendPort 8001 -AskAiPort 8081 -FrontendPort 5180
rem ---------------------------------------------------------------------
set "SCRIPT=%~dp0scripts\dev-all.ps1"
if not exist "%SCRIPT%" goto no_script
powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
if errorlevel 1 goto failed

echo.
echo [i] URLs : http://127.0.0.1:8000/docs   http://127.0.0.1:8080/docs   http://localhost:5173/#/knowledge
echo [i] Login: owner@example.com / secret123
echo [i] Stop : start-all.cmd -Stop   or close the three windows
pause
exit /b 0

:failed
echo.
echo [x] Startup failed - read the error above.
echo     Port busy?  use:  start-all.cmd -Stop    or pass other ports
pause
exit /b 1

:no_script
echo [x] Not found: "%SCRIPT%"
pause
exit /b 1
