@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
title init.json Node Editor

set "PYCMD="
where py >nul 2>nul
if %errorlevel%==0 set "PYCMD=py -3"
if defined PYCMD goto RUN

where python >nul 2>nul
if %errorlevel%==0 set "PYCMD=python"
if defined PYCMD goto RUN

echo [ERROR] Python 3 not found. Please install Python and add it to PATH.
echo.
pause
exit /b 1

:RUN
echo.
echo   init.json Node Editor
echo   ---------------------------------------------
echo   The browser will open automatically.
echo   Keep this window open while editing.
echo   Close this window (or press Ctrl+C) to stop.
echo   ---------------------------------------------
echo.
%PYCMD% "%~dp0node_editor.py"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [ERROR] Server exited with code %RC%. See the message above.
  pause
)
exit /b %RC%
