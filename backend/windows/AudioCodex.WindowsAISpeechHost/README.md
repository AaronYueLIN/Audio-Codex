# Audio Codex Windows AI Speech host

This is the R9 command-line bridge for `Microsoft.Windows.AI.Speech`.
It is adapted from the supplied `SpeechTranscriber` source, but runs headlessly so
Audio Codex can invoke it from the existing local Python backend.

The host is intentionally published **self-contained** (both .NET and Windows App SDK
app-local components) to reduce dependency/version conflicts with whatever Windows App
SDK runtime happens to be installed globally.

Build on Windows with .NET 8 SDK:

```powershell
..\Build-WindowsAISpeechHost.ps1
```

Output:

`backend\windows\host\win-x64\AudioCodex.WindowsAISpeechHost.exe`

The executable is launched inside Audio Codex's sparse package identity so the
`systemAIModels` capability declared in `AppxManifest.xml` applies to the process.

Protocol (one JSON object per line):

- `--status` — query model readiness.
- `--ensure` — prepare/download the optional Windows AI speech model.
- `--input <path>` — transcribe without downloading a missing model.
- `--input <path> --allow-model-download` — transcribe and allow model preparation.

Logs are written to `%LOCALAPPDATA%\AudioCodex\windows-ai-speech.log`.
