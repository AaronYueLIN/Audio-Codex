# R12 — Audio Intelligence

Build: `1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1`

R12 turns the existing Agent into an application intelligence layer built around local entities, current listening context, strict capabilities, explicit user control and verifiable source provenance. It preserves the established Audio Codex font stack, Toyota 2P5 palette and primary UI language.

## Capability contract

1. **Live Context / Rewind** — listening sessions record actual played ranges. Rewind intersects transcript passages with those ranges, so seeked-over audio is never represented as heard.
2. **Listening Recap** — recap generation is session-scoped and sourced. Manual generation is always available; automatic generation is opt-in and tied to the episode/session that scheduled it.
3. **Entity Context Lens** — transcript selections resolve to stable transcript segment IDs plus start/end timestamps. Ask, Explain, Related and Remember operate on those resolved entities rather than arbitrary DOM labels.
4. **Adaptive Intelligence Pane** — the same Intelligence interface docks beside content on wide desktop windows and falls back to a modal on narrow windows. Navigation/source actions keep the pane open when docked.
5. **Intelligence Schema** — first-party entity and intent schemas define Audio Codex capabilities independently of the LLM provider.
6. **Dynamic Profiles** — Listen / Ask / Research / Organize / Act / Vision each receive a strict per-turn tool whitelist, model choice, reasoning policy and web-access policy.
7. **Knowledge Watch** — user-created future conditions can match new/updated local podcast knowledge. Creating a watch never backfills old history into notifications.
8. **Reference Provenance** — transcript hashes and local source-audio SHA-256 fingerprints allow recaps, summaries and sourced AI claims to be marked stale after their source transcript or audio changes.
9. **Confirmable App Actions** — model tool calls only prepare data-changing actions. The mutation runs after an explicit user click, and confirmed/failed actions write local receipts.
10. **Privacy boundary** — Notes, Bookmarks and explicit Memories are tool-gated. Disabling Personal Context removes private tools from the model capability set. Attached image bytes are transient and are never persisted to `ai_messages`.
11. **Sourced research** — local transcript evidence uses `[T…]` citations. Optional current-public-web evidence uses `[W…]` citations and is separated from local evidence.
12. **Streaming** — responses stream over SSE with high-level tool progress. Hidden model reasoning is not forwarded to the browser unless the existing explicit reasoning display setting is enabled.

## Dynamic Intelligence Profiles

- **Listen** — current/just-heard audio context; local only; prioritizes actual listening ranges and transcript evidence.
- **Ask** — current entity and local archive; local only by default.
- **Research** — cross-library synthesis and, only when relevant and enabled, current public web information.
- **Organize** — Notes, Bookmarks, Collections, explicit Memory and Knowledge Watches; mutations remain confirmable.
- **Act** — precise entity resolution plus the smallest confirmable Audio Codex action.
- **Vision** — attached screenshots/images plus current app context; uses the current DeepSeek Flash vision capability and only the tools allowed for the visual task.

Profile tool isolation is enforced in the request/tool layer. It does not rely only on prompt instructions.

## DeepSeek routing — current contract (2026-09-10)

- Fast text / normal conversation: `deepseek-flash`
- Research / deeper synthesis default: `deepseek-flash` with Research-profile reasoning/tool policy
- Vision / real image input: `deepseek-flash`
- Base URL: `https://api.deepseek.com`

DeepSeek's current Flash model supports real image input. Legacy `deepseek-v4-flash-vision-exp` requests are served by the latest Flash model, so R12 no longer defaults to the retired alias. JPEG, PNG, GIF and WebP are accepted. In Chat Completions, image blocks are sent only in the current `user` message; Audio Codex does not place images in system/assistant messages. Vision detail supports `auto`, `low`, `high` and `original`.

Research is source-grounded across the local Audio Codex archive. As of the 2026-09-10 DeepSeek Responses compatibility guide, built-in `web_search` is ignored, so R12 does not expose it as an Agent capability and will not imply that current public facts were verified when they were not.

## App intelligence schema

R12 exposes stable entities for Podcasts, Episodes, Transcript Passages, Knowledge Entities, Collections, Notes, Bookmarks, explicit Memories, Listening Sessions/Recaps, Knowledge Watches/Events and References. Intent schemas cover search, navigation, playback, rewind, source verification, recap, notes/bookmarks/collections, knowledge annotation, transcription/analysis/captions, Knowledge Watches and explicit Memory.

The schema is available through `/api/intelligence/schema` and through the local Agent tool layer.

## Reference Transcript + Audio

Generated knowledge can store:

- exact transcript segment references;
- a deterministic transcript source hash;
- the local source audio file size and modification timestamp;
- a cached SHA-256 of the local source audio;
- model and prompt-version metadata.

Passive verification detects transcript changes and local audio metadata changes. Explicit **Verify** performs a full source-audio SHA-256 check so replacing the underlying audio marks the generated result stale.

## Knowledge Watch behavior

Knowledge Watches are intentionally future-facing. A new watch records its creation/check baseline and does not retroactively notify on older material. New imports trigger new-episode evaluation only for genuinely new episodes; transcript/analysis changes can evaluate mention, topic and contradiction conditions. Matches are stored locally as acknowledgement-capable events.

## Design lineage

R12 borrows architectural ideas, not visual assets, from current system-level personal intelligence patterns: personal context, app actions, on-screen entity awareness, model/tool profiles and provenance-aware execution. Audio Codex keeps its existing visual system and Windows-first implementation.
