# AudioCodex

<img width="2526" height="1270" alt="AudioCodex interface" src="https://github.com/user-attachments/assets/7af7338d-2600-45c3-a691-7d69f57cdd5d" />

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

**Take your work with you.** Export any episode — transcript, timestamps, speakers and all —
to Markdown or JSON.

## What the AI can do

AudioCodex doesn't put a chat box next to a player. The AI is wired into your audio, your
transcripts, your playback position, your notes and collections, and what's on your screen — so
you never have to explain which episode you're listening to, or copy text across to ask about
it. Ask *"what did that mean?"* and it works out which episode, which moment and which passages
you mean.

### It knows where you are

It can see the view you're in, the episode playing, your position in it, the transcript around
you, the live captions, the text you've selected, and whichever collection, note or knowledge
item you're looking at.

So instead of *copy the text → open an AI → paste → explain the background → ask*, you do:
*see it → ask*.

The interface doesn't get to lie, either. What the app claims about your screen is re-resolved
against the local database before the AI sees it, so a stale page title can't steer the answer.

### Rewind — it knows what you actually heard

Most tools read back the last two minutes of the timeline. AudioCodex reads back what you
played.

It keeps a real listening session. If you listened from 00:00 to 08:30 and then dragged the
scrubber to 15:00, the six and a half minutes you skipped are not treated as heard. So when you
ask *"what did they just say?"*, the answer is built only from the parts you were there for.

That is what makes *just now*, *earlier* and *this bit* mean something specific.

### Recap — what you heard, not what the episode contains

An episode summary answers *"what is this episode about?"*

A Listening Recap answers *"what did I just hear?"*

If a show runs two hours and you heard twenty minutes, the recap covers those twenty minutes —
the points, the people, the numbers, and the questions worth following up. It's scoped to your
session, not to the file.

### Anything you select becomes something you can ask about

A transcript passage isn't just text here. It's an object with a source, a timestamp and an
episode behind it. Select a sentence and ask about it, explain it, find related moments, or save
it to a note — without copying anything or describing where it came from.

The same idea covers people, topics, works, collections, notes, bookmarks and episodes.

### It reads your library only when the question needs it

Ask something that depends on your own material and the AI can bring in your notes, bookmarks,
collections and recent listening. But your library isn't shipped to the model wholesale on every
message. The system decides which tools the question needs and reads only what those tools
return.

Turn Personal Context off and those tools leave the AI's reach entirely — not discouraged by an
instruction, but absent from what it is able to call.

### It changes how it works, not just what it says

There is no single enormous prompt trying to cover everything. Depending on what you're doing,
the request is routed differently — a quick question about what you just heard, a deep
comparison across your library, organizing your notes and collections, or carrying out an
action.

Each route gets a different model, a different amount of reasoning, and a different set of
tools. A research question has no business writing to your database, so write tools are not
offered to it at all.

### It uses the app's own tools

Ask a question and it can search transcripts, open episodes, look up knowledge and notes, and
assemble the answer itself. You don't need to know that a search happened before a lookup.

That's the difference between an assistant and a chatbot: it can act, not only reply.

### It acts — but only when you say so

Understanding your request and changing your data are kept firmly apart.

*"Bookmark this." "Save that as a note." "Add this to my collection." "Remind me when this comes
up again."* — the AI prepares the action and shows it to you. Nothing is written until you
confirm it.

And when an action does run, AudioCodex records it locally: what was done, with which arguments,
in which conversation, whether you confirmed it, what came back, and when. The point isn't an
unexplainable assistant — it's keeping the important operations accountable.

### Knowledge Watch — it keeps working after your question is answered

You can tell AudioCodex: *"if anyone talks about this again, let me know"*, or *"remind me next
time this guest discusses inference costs."*

New content is checked as it arrives — when an episode is imported, transcribed or analysed —
and you're told what matched, with the evidence behind it. Watches look forward: creating one
doesn't retroactively turn your entire back catalogue into alerts.

So it stops being only a knowledge base you query, and starts being one that keeps an eye on
your interests.

### It can read what's on your screen

Paste, drop or attach an image and ask about it. Vision here isn't a separate image-chat — the
picture joins the same task as your current episode, transcript and library tools, so *"how does
this compare to what I've heard?"* is a real question.

Images are used for that request and are not written into your conversation history.

### It remembers where its own answers came from

Generating a summary is easy. Knowing whether it's still true months later is the hard part.

Every generated summary and recap records the transcript passages, episode and source audio it
was built on, along with fingerprints of that source. Generated knowledge keeps a visible chain
back to what produced it.

### And it tells you when that chain has broken

Suppose the first transcript read *"revenue increased by 50%"*, and a later correction shows it
was 15%. The old summary cited its source faithfully at the time — and it is wrong now.

AudioCodex can re-check the transcript and the audio behind any generated content. If the source
was edited, re-transcribed, replaced or lost, the summary is marked stale so you can regenerate
it instead of trusting it.

So the question isn't only *"where did this come from?"* — it's *"does it still come from the
same place?"*

One limit worth stating plainly: this checks whether the source is intact. It is not
fact-checking the world. If a guest said something false, and AudioCodex quoted them correctly,
the source verifies clean.

### Conversation that carries over

With conversation memory on, your history is kept in AudioCodex's own local database, so a
follow-up question can refer back to what came before. Different kinds of content get different
lifetimes — a conversation is worth keeping, a passing screenshot is not.

So "remember the context" and "permanently store everything you send me" stay two different
things.

### The knowledge stays local

Transcripts, notes, bookmarks, collections, listening sessions, conversations, watches,
provenance and action records all live in the local data layer, searchable by wording — and by
meaning, once you connect an embedding model.

The model understands and reasons. AudioCodex keeps the knowledge, the sources and the
behaviour. That's why the provider isn't the architecture.

### Not just a chat

The usual shape of an AI audio tool is:

```text
Audio → Transcript → Chat
```

AudioCodex aims at something longer:

```text
Audio → Transcript → Entity → Context → Knowledge → Intelligence → Action → Provenance
```

Audio stops being a file you hand to a model once, and becomes something you can search, cite,
connect, verify, organise and keep track of. The intelligence isn't a separate page — it's a
layer over the player, the transcript and your library.

**Listen. Understand. Remember. Act. Verify.**

- **Listen** — know what you actually heard.
- **Understand** — know what's on your screen, and what the question is really pointing at.
- **Remember** — turn what matters into knowledge that belongs to you.
- **Act** — do the real work, with you keeping the final say.
- **Verify** — connect every answer back to the audio it came from, and notice when that source
  has changed.

The goal isn't to teach you how to use an AI. It's for AudioCodex to understand what you're
listening to, what you're looking at, and what you already know — and to help with the next step
when you actually need it.

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
