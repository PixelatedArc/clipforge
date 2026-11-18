@echo off
rem Desktop: "ClipForge".  Start menu: "ClipForge" and "ClipForge (phone mode)".
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$dir = (Get-Location).Path; $ws = New-Object -ComObject WScript.Shell;" ^
  "$desk = [Environment]::GetFolderPath('Desktop'); $menu = [Environment]::GetFolderPath('Programs');" ^
  "function Make($folder, $name, $bat, $desc) {" ^
  "  $l = $ws.CreateShortcut((Join-Path $folder ($name + '.lnk')));" ^
  "  $l.TargetPath = Join-Path $dir $bat; $l.WorkingDirectory = $dir;" ^
  "  $l.IconLocation = (Join-Path $dir 'assets\clipforge.ico') + ',0';" ^
  "  $l.Description = $desc; $l.WindowStyle = 7; $l.Save() }" ^
  "Make $desk 'ClipForge' 'start.bat' 'Free local YouTube-to-Shorts clipper';" ^
  "Make $menu 'ClipForge' 'start.bat' 'Free local YouTube-to-Shorts clipper';" ^
  "Make $menu 'ClipForge (phone mode)' 'start-phone.bat' 'Use ClipForge from your phone on the same Wi-Fi';" ^
  "Write-Host 'ClipForge shortcut created on the Desktop (and in the Start menu).'"
