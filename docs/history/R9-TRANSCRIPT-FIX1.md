# R9 final integration

- R9 design/features retained.
- Transcript: local faster-whisper by default.
- Windows AI Speech helper + sparse package identity restored from the working installer architecture.
- AI: API-key-only; DeepSeek default (`deepseek-v4-flash`, `https://api.deepseek.com`).
- No Auto local/Ollama probing.
- DeepSeek follows the mature reference OpenAI-compatible SSE/tool-call protocol. Structured Analyze always disables thinking; Chat thinking remains configurable.
- Environment-aware installer reuses system Python packages via `--system-site-packages` and installs only missing/incompatible packages.

Build `1.0.0-R9-MATUREAI-COLLECTIONS-JOBS2`.
