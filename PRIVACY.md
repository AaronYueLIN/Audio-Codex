# Privacy

AudioCodex is designed around a local-first library.

## Stored locally

Podcast metadata, transcript segments, notes, bookmarks, collections,
entities, AI conversations, action receipts, listening sessions,
provenance records, watches and settings can be stored in the local SQLite
database.

On Windows the default runtime home is under `%LOCALAPPDATA%\AudioCodex`
unless `AUDIO_CODEX_HOME` is set.

## Credentials

In R12.1, an AI API key entered in Settings is stored in the local SQLite
settings data. This repository does not claim that value is encrypted at
rest. Treat the local database as sensitive.

No user database, API key, local log, model cache or personal path is
included in this public-source package.

## External AI providers

Content is sent to the configured AI provider only when an AI capability
that requires the provider is invoked.

Image attachments are transient in R12.1 and are not persisted to
`ai_messages`.

## Personal Context

Notes, bookmarks and explicit memories are tool-gated. Disabling Personal
Context removes those private tools from the model capability set for the
relevant requests.

## Network exposure

Normal local operation should bind the API to `127.0.0.1`. Do not expose
it to a LAN or the public Internet without adding authentication and
network controls appropriate to your environment.
