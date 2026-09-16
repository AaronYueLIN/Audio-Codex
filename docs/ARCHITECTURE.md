# Architecture

```text
User
  |
  v
Vanilla HTML/CSS/JS UI
  |
  v
FastAPI / Uvicorn
  |
  +--> AudioCodex Intelligence
  |      +--> screen/listening context
  |      +--> Dynamic Profiles
  |      +--> tool routing
  |      +--> confirmable actions
  |      +--> watches / provenance
  |
  +--> Local Knowledge
  |      +--> SQLite / FTS5
  |      +--> transcripts / entities
  |      +--> notes / bookmarks / collections
  |
  +--> AI Provider
  |      +--> text / vision / function tools / streaming
  |
  +--> Speech / Media
         +--> Windows AI Speech source host
         +--> faster-whisper
         +--> Windows System Speech
         +--> FFmpeg when available
```

Key rules:

1. Local entities are authoritative; UI labels are hints.
2. The model receives only tools allowed for the current profile.
3. Data-changing actions require explicit confirmation.
4. Generated knowledge can retain source provenance and become stale.
5. Provider code sits behind AudioCodex's own entity/action schema.
