# Security Policy

## Supported snapshot

This public repository snapshot represents AudioCodex 1.2.1 / R12.1
Hotfix 1.

## Reporting a vulnerability

Prefer GitHub's private security-advisory / "Report a vulnerability"
workflow when available. Do not put API keys, private transcript content,
user databases or other sensitive data in a public issue.

## Important boundaries

- Keep FastAPI bound to `127.0.0.1` unless you deliberately add
  authentication and network isolation.
- The local SQLite database can contain private content and AI credentials.
- Agent mutations are expected to use the explicit confirmation flow.
- Do not log Authorization headers, API keys or attached image bytes.
- Treat imported media and metadata as untrusted input.

## Repository hygiene

Run:

```powershell
py -3.12 scripts/repo_check.py
```

before publishing.
