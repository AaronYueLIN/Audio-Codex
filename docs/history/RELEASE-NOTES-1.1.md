# Audio Codex release notes

## 1.2 R12 — Audio Intelligence

Build: `1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1`

R12 is an intelligence-architecture release rather than a visual redesign.

- Added actual-range Live Rewind and session-scoped Listening Recaps.
- Added opt-in Auto Listening Recap with episode/session-safe scheduling.
- Added transcript Entity Context Lens: Ask / Explain / Related / Remember.
- Added adaptive docked Intelligence Pane on wide desktop windows with compact modal fallback.
- Added stable Audio Codex entity + intent schemas independent of the LLM provider.
- Added strict Dynamic Profiles: Listen, Ask, Research, Organize, Act and Vision.
- Added user-created, future-only Knowledge Watches and acknowledgement-capable events.
- Added Reference Transcript + Audio provenance with transcript hashing and local audio SHA-256 verification.
- Added sourced `[T…]` local transcript citations and optional `[W…]` current-web citations.
- Preserved SSE streaming and high-level tool progress without exposing hidden reasoning by default.
- Preserved confirm-before-mutate App Actions and persistent confirmed/failed local action receipts.
- Minimized Personal Context: private Notes / Bookmarks / explicit Memories are fetched only by relevant tools, and those tools disappear when Personal Context is disabled.
- Images remain transient and are not persisted to local conversation rows.
- Current DeepSeek defaults (2026-09-10): `deepseek-v4-flash` text, `deepseek-v4-pro` research, `deepseek-v4-flash-vision-exp` vision.
- Preserved the established font stack, Toyota 2P5 palette, R10 UI detail polish and Windows AI Speech integration.

## Historical R11 — DeepSeek Intelligence 2

R11 introduced app-aware screen context, local Function Tools, confirmable App Actions, local conversation continuity, visual attachments, optional web retrieval, streaming answers and action receipts. R12 supersedes R11's early model aliases and uses the current explicit DeepSeek V4 model IDs listed above.
