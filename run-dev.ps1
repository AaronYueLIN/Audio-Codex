$ErrorActionPreference="Stop"
Set-Location $PSScriptRoot
$py=Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if(-not(Test-Path $py)){throw 'Run Audio Codex Maintenance.exe --repair or reinstall first.'}
$env:PYTHONPATH=Join-Path $PSScriptRoot "backend\src"
$env:AUDIO_CODEX_BUILD_ID="1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1"
& $py -m uvicorn podcast_codex.server:app --host 127.0.0.1 --port 8765 --reload
