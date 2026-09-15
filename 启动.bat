@echo off
cd /d "%~dp0"
title Piano Transcriber

set "PY=runtime\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Runtime environment is missing.
  echo Run the environment installer first.
  pause
  exit /b 1
)

echo Starting server at http://127.0.0.1:8765
echo Close this window to stop the server.

start "" "http://127.0.0.1:8765"
"%PY%" app.py
if errorlevel 1 (
  echo START FAILED. See the error above.
  pause
)
