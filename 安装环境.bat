@echo off
cd /d "%~dp0"
title Piano Transcriber - Install Environment
echo Starting environment installation...
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install_env.ps1"
if errorlevel 1 (
  echo INSTALL FAILED. See the error above.
  pause
  exit /b 1
)
echo INSTALL COMPLETE.
pause
