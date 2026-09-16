# AudioCodex


<img width="2526" height="1270" alt="image" src="https://github.com/user-attachments/assets/7af7338d-2600-45c3-a691-7d69f57cdd5d" />






























**AudioCodex** is a local-first Windows audio knowledge system that turns long-form audio into a searchable, source-aware personal knowledge library and adds an application-level Intelligence layer on top.

This repository snapshot corresponds to **AudioCodex 1.2.1 / R12.1 Hotfix 1**.

`1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1`

> **Source provenance:** the Python backend, web UI, Windows AI Speech source, PowerShell bridges, manifests and launcher-wrapper source were recovered directly from the released R12.1 installer payload. The outer Go installer source is reconstructed from the same installer lineage because it was not embedded in the EXE. See [Recovery & provenance](docs/RECOVERY.md).

## Highlights

- **Local-first audio library** with episodes, transcripts, notes, bookmarks, collections, entities and knowledge records.
- **Transcript search and retrieval** backed by SQLite/FTS5, with optional embedding-based retrieval.
- **Windows-first speech pipeline** with Windows AI Speech integration source, `faster-whisper`, and Windows System Speech fallback paths.
- **AudioCodex Intelligence** with Live Context/Rewind, session-scoped Listening Recaps, Entity Context Lens, Dynamic Intelligence Profiles, Knowledge Watch, source provenance, vision input and SSE streaming.
- **Confirmable app actions**: data-changing Agent actions are proposed first and execute only after explicit user confirmation.
- **Reference Transcript + Audio** provenance so generated knowledge can be marked stale when its underlying transcript or local audio changes.
- **Adaptive desktop UI** that keeps the existing AudioCodex visual system while allowing Intelligence to dock beside content on wide windows.

## Intelligence model

```text
Audio -> Transcript -> Entity -> Knowledge -> Provenance -> Action
```

The model provider is only one part of the system. AudioCodex keeps its own entity/action schema, local context, source references and permission boundary so product behavior is not architecturally tied to one LLM.

## Quick start from source

Requirements:

- Windows 11 x64 recommended
- Python 3.11–3.13 x64
- PowerShell
- Optional: FFmpeg for audio normalization
- Optional: .NET 8 SDK to build the Windows AI Speech host
- Optional: Go to rebuild launcher/installer layers

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install --upgrade pip
.\venv\Scripts\python.exe -m pip install -r requirements-core.txt

# Optional local transcription
.\venv\Scripts\python.exe -m pip install -r requirements-transcription.txt

$env:PYTHONPATH = "$PWD\backend\src"
$env:AUDIO_CODEX_BUILD_ID = "1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1"
.\venv\Scripts\python.exe -m uvicorn podcast_codex.server:app --host 127.0.0.1 --port 8765
```

Open `http://127.0.0.1:8765/`.

Windows AI Speech host:

```powershell
powershell -ExecutionPolicy Bypass -File .\backend\windows\Build-WindowsAISpeechHost.ps1
```

## Privacy and security

AudioCodex stores its library locally by default. In R12.1, user settings — including an AI API key if entered — are stored in the local SQLite settings database and are **not claimed to be encrypted at rest**. Never commit `library.db`, logs, caches or local runtime folders.

AI/image content is sent to the configured provider only when the relevant AI capability is used. R12 image attachments are transient and are not persisted to `ai_messages`. Personal-context tools can be disabled, and data-changing Agent actions require confirmation.

Read [PRIVACY.md](PRIVACY.md) and [SECURITY.md](SECURITY.md) before exposing the local API beyond loopback.

## Repository map

```text
backend/src/podcast_codex/    Python backend, RAG, Intelligence and data model
backend/ui/                   Vanilla HTML/CSS/JavaScript UI
backend/windows/              Windows AI Speech C# source and PowerShell bridges
tools/                        Launcher-wrapper source
installer/                    Reconstructed Go installer source
docs/                         Architecture, R12 contract, build/recovery notes
.github/                      CI and contribution templates
```


## License

MIT. See [LICENSE](LICENSE).
