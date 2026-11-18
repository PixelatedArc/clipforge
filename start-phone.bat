@echo off
rem Same as start.bat, but phones/tablets/other PCs on the same Wi-Fi can use it too.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run - running setup...
  call setup.bat || exit /b 1
)
".venv\Scripts\python.exe" -m pip install -q --upgrade "yt-dlp[default]" >nul 2>&1
".venv\Scripts\python.exe" app.py --lan
pause
