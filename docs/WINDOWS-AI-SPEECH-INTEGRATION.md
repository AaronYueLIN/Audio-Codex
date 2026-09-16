# R9 Windows AI Speech integration

This R9 source tree now integrates the uploaded `SpeechTranscriber` Windows AI speech path into Audio Codex instead of relying only on the earlier PowerShell/reflection experiment.

## Architecture

1. `AppxManifest.xml` keeps the existing sparse package identity and `systemAIModels` capability.
2. `backend/windows/AudioCodex.WindowsAISpeechHost/` is a headless C#/.NET 8 host using `Microsoft.Windows.AI.Speech.SpeechRecognitionModel` and `BatchRecognition`.
3. The host is published self-contained to `backend/windows/host/win-x64/`, including app-local Windows App SDK components to reduce global runtime version conflicts.
4. `WindowsAIInvoke.ps1` launches the host inside the registered Audio Codex package identity so Windows sees the `systemAIModels` capability.
5. `transcription.py` uses that host first for the `windows-ai` provider, then preserves R9 fallbacks to `faster-whisper` and legacy `System.Speech`.
6. Settings exposes model readiness and an explicit **Prepare Windows AI model** action. On CPU-only PCs this is the consent point before Windows Update downloads the optional speech recognition model.

## Build the speech host

On Windows with the .NET 8 SDK installed:

```powershell
AudioCodex\backend\windows\Build-WindowsAISpeechHost.ps1
```

Then rebuild the installer normally:

```powershell
python build\build_windows_installer.py
```

The installer build now refuses to silently omit the host. For deliberate Linux/source-only cross builds, set `AUDIO_CODEX_SKIP_WINDOWS_AI_HOST=1`.

## Runtime requirements

- Windows 11 24H2 (build 26100) or later.
- The Audio Codex sparse package identity must be registered (the installer/Repair path does this).
- On Copilot+ PCs the speech model can be preinstalled; on CPU-only PCs Windows may download it on demand through Windows Update.
- Audio stays on-device for Windows AI Speech transcription.

## Diagnostics

System status reports:

- whether the C# host was built;
- package identity registration;
- Windows AI availability/readiness state;
- the effective speech engine.

Host log:

`%LOCALAPPDATA%\AudioCodex\windows-ai-speech.log`

The original PowerShell WinRT bridge remains as a source-tree/emergency fallback when the compiled host is absent.

## Known upstream caveat

The supplied SpeechTranscriber investigation recorded `TryCreateAsync()` returning `0x8007007E` on one CPU-mode machine even after the model reported Ready. The integrated host cannot guarantee that an OS/runtime defect is fixed, but publishing Windows App SDK components app-locally removes one likely source of version mismatch and R9 will still fall back to its other local transcription engines if Windows AI fails.
