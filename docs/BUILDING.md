# Building and development

## Backend / UI

The backend serves the SPA directly; no Node.js build step is required.

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-core.txt
.\venv\Scripts\python.exe -m pip install -r requirements-transcription.txt
$env:PYTHONPATH = "$PWD\backend\src"
$env:AUDIO_CODEX_BUILD_ID = "1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1"
.\venv\Scripts\python.exe -m uvicorn podcast_codex.server:app --host 127.0.0.1 --port 8765
```

## Windows AI Speech host

Requires .NET 8 SDK on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\backend\windows\Build-WindowsAISpeechHost.ps1
```

## Launcher wrapper

`tools/launcher-wrapper.go` is recovered directly from R12.1.

## Installer

`installer/setup.go` is reconstructed rather than byte-original because the
released outer EXE did not embed its own Go source.

Rebuilding the historical official installer exactly also depends on
`AudioCodex-R9.exe`, a compiled native shell whose original source was not
present in the R12.1 payload. It is intentionally not committed here.
