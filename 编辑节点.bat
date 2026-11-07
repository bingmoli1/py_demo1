@echo off
title init.json Node Editor
rem Plain cmd launcher. Keep this window open while editing;
rem closing the browser page stops the server and closes this window.
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
    py -3 "%~dp0node_editor.py"
) else (
    python "%~dp0node_editor.py"
)

if errorlevel 1 (
    echo.
    echo Startup failed. Make sure Python 3 is installed and init.json exists.
    pause
)
exit /b 0
