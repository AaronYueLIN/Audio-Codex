# AudioCodex

**Turn thousands of hours of audio into a library you can actually use.**

The best ideas arrive in long form — a three-hour interview, a lecture series, a podcast you
keep meaning to finish. Then they disappear, because the answer you need today is buried at
minute 141 of something you heard six months ago.

AudioCodex fixes that. Import your audio, transcribe it on your own machine, and get a library
where every word is searchable and an AI has read all of it.

## Built on three promises

**It's yours.** No account, no cloud sync, no telemetry. Your library is a single SQLite file
on your own disk. Nothing leaves your machine unless you ask the AI something — and then only
the context required to answer that request.

**It's grounded.** AudioCodex's AI doesn't recall your library from memory. It searches it,
reads the passages that matter, and can cite the exact podcast, episode, timestamp and speaker
behind what it tells you.

**It asks first.** The AI can propose real work — bookmark this moment, write that note, file
this episode into a collection, run a transcription. Nothing happens until you press the
button, and every confirmed action is written to a local log you can audit.

## What's inside

**A library that thinks in entities.** Episodes, transcripts, notes, bookmarks and collections
— plus the people, works, places and topics that connect them.

**Transcription that never leaves your machine.** Three local engines, tried in order:
Windows AI Speech, faster-whisper, then Windows System.Speech. Optional speaker diarization
tells you who said what.

**Search that finds the idea, not just the word.** Full-text over every transcript, fused with
optional semantic retrieval for when you remember the concept but not the phrasing. Every hit
carries its podcast, episode, timestamp, chapter and speaker.

**An AI that lives inside your archive.** Bring your own DeepSeek API key. Ask across hundreds
of episodes, jump straight to the moment, and let it organize what it finds.

**Provenance you can trust.** Every generated summary remembers the transcript and audio it
came from. Change either one and AudioCodex marks the summary stale — it would rather warn you
than quietly serve something outdated.

**MCP, if you want it.** Every tool AudioCodex uses is exposed at `/mcp`, ready for your own
clients.

**Take your work with you.** Export any episode — transcript, timestamps, speakers and all —
to Markdown or JSON.

## Get started

Requires Windows 11 x64 and Python 3.11–3.13.

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements-core.txt

# Optional: local transcription (faster-whisper downloads model weights on first use)
.\venv\Scripts\python.exe -m pip install -r requirements-transcription.txt

$env:PYTHONPATH = "$PWD\backend\src"
.\venv\Scripts\python.exe -m uvicorn podcast_codex.server:app --host 127.0.0.1 --port 8765
```

Open <http://127.0.0.1:8765/>. After the first setup, `run-dev.bat` relaunches with the same
environment variables. FFmpeg is optional and used only for audio normalisation.

## Private by default

Your library lives in `%LOCALAPPDATA%\AudioCodex` (or `AUDIO_CODEX_HOME`). There is no account
to create, no server to trust, and nothing reporting home.

Two things stated plainly, because a privacy claim is worth nothing without them: an API key
entered in Settings is stored unencrypted in the local database, and the local API has no
authentication of its own. Keep it bound to `127.0.0.1` and don't expose it to a network.

## Under the hood

```text
backend/src/podcast_codex/   FastAPI backend: data model, retrieval, Intelligence layer
backend/ui/index.html        The entire UI — one HTML file, no build step
backend/windows/             C# Windows AI Speech host and PowerShell bridges
tools/                       Launcher wrapper
installer/                   Go installer
docs/                        Architecture, API contract, build and recovery notes
```

Building the Windows speech host is optional and needs the .NET 8 SDK:

```powershell
powershell -ExecutionPolicy Bypass -File .\backend\windows\Build-WindowsAISpeechHost.ps1
```

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Yue LIN.

---

*Source note: the Python backend, web UI, C# host, PowerShell bridges and manifests were
recovered from the released installer payload; the Go installer source is reconstructed from
the same lineage rather than extracted byte-for-byte. See [docs/RECOVERY.md](docs/RECOVERY.md).*
