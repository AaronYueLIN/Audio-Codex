# Knowledge annotations update

Build: `1.0.0-R9-MATUREAI-KNOWLEDGE1`

- Removed the accidental always-visible Jobs symbol in the expanded left sidebar.
- Analyze entity descriptions remain persisted in `entities.description`.
- Knowledge now previews the saved AI annotation.
- Clicking an entity shows the effective annotation (`user_description` if present, otherwise AI `description`), connections, and transcript mentions.
- Existing analyzed databases do not need re-analysis to display annotations that were already stored.
- Previous DeepSeek Analysis R1-R4 fixes and Collections hover-only Delete behavior are retained.
