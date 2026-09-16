# Audio Codex 1.1 API Routes

```text
GET    /
GET    /api/ai/status
GET    /api/artwork/episode/{episode_id}
GET    /api/audio/{episode_id}
GET    /api/bookmarks
POST   /api/bookmarks
DELETE /api/bookmarks/{bookmark_id}
POST   /api/chat
GET    /api/collections
POST   /api/collections
GET    /api/collections/{collection_id}
PATCH  /api/collections/{collection_id}
DELETE /api/collections/{collection_id}
GET    /api/collections/{collection_id}/episodes
PATCH  /api/collections/{collection_id}/episodes
GET    /api/entities
GET    /api/entities/{entity_id}
PATCH  /api/entities/{entity_id}
GET    /api/episodes
GET    /api/episodes/{episode_id}
DELETE /api/episodes/{episode_id}
POST   /api/episodes/{episode_id}/analyze
GET    /api/episodes/{episode_id}/captions
POST   /api/episodes/{episode_id}/captions/generate
PATCH  /api/episodes/{episode_id}/playhead
POST   /api/episodes/{episode_id}/transcribe
GET    /api/episodes/{episode_id}/transcript
GET    /api/episodes/{episode_id}/transcript/text
GET    /api/export/episode/{episode_id}
GET    /api/graph
GET    /api/health
POST   /api/import
GET    /api/intelligence/conversations/{conversation_id}
DELETE /api/intelligence/conversations/{conversation_id}
GET    /api/intelligence/memories
DELETE /api/intelligence/memories/{memory_id}
GET    /api/jobs
DELETE /api/jobs/history
POST   /api/jobs/{job_id}/cancel
DELETE /api/jobs/{job_id}
GET    /api/jobs/{job_id}
GET    /api/notes
POST   /api/notes
GET    /api/overview
POST   /api/pick-files
POST   /api/pick-folder
GET    /api/podcasts
GET    /api/reading-list
POST   /api/reading-list
DELETE /api/reading-list/{item_id}
GET    /api/search
PATCH  /api/segments/{segment_id}
GET    /api/settings
PATCH  /api/settings
GET    /api/system
POST   /api/system/windows-ai-speech/ensure
GET    /api/timeline
POST   /mcp
```

## POST /api/chat

The R11 request accepts text, local UI context and optional visual input:

```json
{
  "message": "What is this chart showing, and where is it discussed in my library?",
  "conversation_id": null,
  "scope": "episode",
  "episode_id": 42,
  "collection_id": null,
  "podcast_id": null,
  "passage_ms": 183000,
  "images": [
    {
      "data_url": "data:image/png;base64,...",
      "name": "screenshot.png",
      "detail": "auto"
    }
  ],
  "history": [],
  "screen_context": {
    "view": "episodes",
    "selected_episode_id": 42,
    "selected_entity_id": null,
    "selected_collection_id": null,
    "playhead_ms": 183000,
    "player_state": "paused",
    "visible_title": "Episode title",
    "selected_text": "",
    "active_caption": "",
    "search_query": ""
  }
}
```

`images` accepts JPEG/PNG/GIF/WebP data URLs. Audio Codex validates content signatures and enforces its own conservative attachment limits before forwarding visual content to the provider.

The response may contain `sources` (local transcript citations), `actions` (pending confirmation actions), `conversation_id`, and `intelligence` metadata such as route/model/vision usage.
