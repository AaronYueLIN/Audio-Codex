# Installer source status

`setup.go` is a **reconstructed maintainer source file**, not a byte-for-byte
recovery of the outer R12.1 installer source.

The released R12.1 EXE embedded the application payload but did not embed
its own Go source. This file uses the same known Audio Codex installer
lineage and applies:

- Version: `1.2.1`
- Build: `1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1`

The application payload source itself was recovered directly from the
R12.1 installer.

The historical native shell `AudioCodex-R9.exe` is another compiled
artifact whose original source was not present in the R12.1 installer.
It is intentionally not committed to Git.

See [`../docs/RECOVERY.md`](../docs/RECOVERY.md).
