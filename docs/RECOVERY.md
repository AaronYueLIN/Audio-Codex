# Recovery and source provenance

This repository was prepared from the released AudioCodex R12.1 Hotfix 1
Windows installer.

Installer SHA-256:

`3d5dfef6c7d9f3e4fbd26cbfd85ce17520777f5282c72e1e32e33f24c5a2e411`

Recovered build identity:

`1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1`

## Recovered directly from the embedded payload

- Python backend under `backend/src/podcast_codex/`
- UI under `backend/ui/`
- Windows AI Speech C# project and PowerShell bridge source
- `AppxManifest.xml`
- requirements and development launch scripts
- `tools/launcher-wrapper.go`
- R12 documentation and image assets

## Reconstructed

- `installer/setup.go`

The outer installer was compiled Go code and did not embed its own `setup.go`.
The public file is reconstructed from the same known Audio Codex Go installer
lineage with R12.1 release constants applied.

## Not recovered as original source

- Original source for the compiled `AudioCodex-R9.exe` native shell.

The binary existed in the payload but is intentionally not committed as source.

## GitHub packaging changes

- Removed compiled `.exe` files from the source repository.
- Removed internal validation JSON and historical patch artifacts.
- Organized public docs under `docs/`.
- Added GitHub metadata, privacy/security/contribution docs and hygiene checks.
- Included no user DB, API key, personal path, log or cache.
