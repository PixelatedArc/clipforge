@echo off
setlocal
cd /d "%~dp0"
echo === ClipForge setup ===

rem --- Python: needs 3.10-3.13 (3.14+ is too new for some video/AI libraries)
set "PY="
for %%V in (3.13 3.12 3.11 3.10) do (
  if not defined PY (
    py -%%V -c "import sys" >nul 2>&1 && set "PY=py -%%V"
  )
)
if not defined PY (
  echo Python 3.10-3.13 not found. Installing Python 3.13 with winget...
  winget install -e --id Python.Python.3.13 --accept-source-agreements --accept-package-agreements
  set "PY=py -3.13"
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment with %PY% ...
  %PY% -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

rem --- ffmpeg
where ffmpeg >nul 2>&1
if errorlevel 1 (
  if not exist "%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*" (
    echo Installing ffmpeg with winget...
    winget install -e --id Gyan.FFmpeg --accept-source-agreements --accept-package-agreements
  )
)

rem --- Node.js (yt-dlp needs a JavaScript runtime for YouTube)
where node >nul 2>&1
if errorlevel 1 (
  echo Installing Node.js LTS with winget...
  winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
)

echo.
echo Setup complete. Double-click start.bat to run ClipForge.
pause
exit /b 0

:fail
echo.
echo Setup FAILED - see the error above.
pause
exit /b 1
