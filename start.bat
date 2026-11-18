@echo off
title ClipForge - keep this window open while using the app
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run - running setup...
  call setup.bat || exit /b 1
)
rem Keep yt-dlp current: YouTube changes often and old versions stop working.
".venv\Scripts\python.exe" -m pip install -q --upgrade "yt-dlp[default]" >nul 2>&1
".venv\Scripts\python.exe" app.py
pause
