# Analysis Fix + Collection UI rollback

Build: `1.0.0-R9-MATUREAI-ANALYSISFIX1`

AI changes follow the supplied Codex error report:

- R1: structured Analyze keeps `thinking=False`.
- R2: MCP dotted names are converted to wire-safe function names and mapped back internally.
- R3: `json_mode=True` now reaches the request payload as
  `response_format={"type":"json_object"}`.
- R4: Analyze stage requests use a logical 1,000,000 token budget; request construction
  clamps DeepSeek to 393,216 output tokens and OpenAI-compatible providers to 32,768.

Collections UI:
- restored to the compact/original R9 card layout;
- no Edit / Manage Episodes controls in the Collections UI;
- Delete is hidden until the mouse hovers the collection card;
- deleting a collection does not delete its Library episodes.

Other Jobs/MCP/server behavior from the previous build is retained.
