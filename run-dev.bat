@echo off
setlocal
cd /d "%~dp0"
set "PY=%CD%\venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Run "Audio Codex Maintenance.exe --repair" or reinstall Audio Codex first.
  pause
  exit /b 1
)
set "PYTHONPATH=%CD%\backend\src"
set "AUDIO_CODEX_BUILD_ID=1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1"
"%PY%" -m uvicorn podcast_codex.server:app --host 127.0.0.1 --port 8765 --reload
