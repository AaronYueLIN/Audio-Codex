# Public-release sanitization

Excluded from the GitHub source package:

- user databases and SQLite WAL/SHM files;
- logs, caches, downloaded models and virtual environments;
- environment files and credential/key containers;
- compiled application/installer EXEs;
- internal validation JSON and historical patch artifacts;
- temporary build-environment files.

Automated checks cover common token/private-key formats (including
provider API keys and HuggingFace tokens), e-mail addresses and
machine-specific user-home paths. The documentation describing those
checks is excluded from matching against its own example patterns.

No matching secret/identity material was found in the prepared repository.

Runtime note: R12.1 can store an AI API key in the user's local SQLite
settings. That runtime database is not part of this package and is blocked
by `.gitignore`.
