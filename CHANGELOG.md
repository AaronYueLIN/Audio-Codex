# Changelog

## 1.2.1 — R12.1 Hotfix 1

Build: `1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1`

### Fixed

- Fixed `TypeError: unhashable type: 'list'` in conversation-context metadata
  filtering when R12 on-screen context contains structured values such as
  `visible_items`.
- Preserved non-empty list/dict context while continuing to exclude selected
  free-form text and live-caption content from persisted conversation metadata.

### R12 Intelligence

- Live Context / Rewind based on actual played ranges.
- Listening Recap scoped to listening sessions.
- Entity Context Lens.
- Adaptive Intelligence Pane.
- Dynamic Intelligence Profiles with per-turn tool whitelists.
- Knowledge Watch.
- Reference Transcript + Audio provenance and stale detection.
- Confirmable App Actions and local Action Receipts.
- Vision input and SSE streaming.
- Personal Context tool gating.

Historical recovered notes are under `docs/history/`.
