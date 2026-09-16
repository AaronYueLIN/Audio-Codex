from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    home: Path
    db: Path
    cache: Path
    models: Path
    logs: Path

    @classmethod
    def resolve(cls) -> "AppPaths":
        configured = os.getenv("AUDIO_CODEX_HOME")
        if configured:
            home = Path(configured).expanduser().resolve()
        elif os.name == "nt":
            base = Path(os.getenv("LOCALAPPDATA", Path.home()))
            home = base / "AudioCodex"
        else:
            home = Path.home() / ".audio-codex"
        paths = cls(
            home=home,
            db=home / "library.db",
            cache=home / "cache",
            models=home / "models",
            logs=home / "logs",
        )
        for p in (paths.home, paths.cache, paths.models, paths.logs):
            p.mkdir(parents=True, exist_ok=True)
        return paths


PATHS = AppPaths.resolve()
HOST = os.getenv("AUDIO_CODEX_HOST", "127.0.0.1")
PORT = int(os.getenv("AUDIO_CODEX_PORT", "8765"))
