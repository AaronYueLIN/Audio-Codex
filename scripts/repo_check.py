from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", "build", "dist", "out"}
SKIP_FILES = {"repo_check.py", "SANITIZATION.md"}

PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    # AudioCodex stores both of these in the local settings table, so a settings dump,
    # a stray database copy or a debug log is the realistic way they would reach the repo.
    "provider API key": re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}"),
    "HuggingFace token": re.compile(rb"\bhf_[A-Za-z0-9]{30,}"),
    "Windows user path": re.compile(rb"(?i)[A-Z]:\\Users\\(?!\.\.\.)[^\\\r\n`]+"),
    "Unix home path": re.compile(rb"/(?:home|Users)/(?!\.\.\.)[^/\s`]+"),
    "email address": re.compile(rb"(?i)[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"),
}

problems: list[str] = []
for p in ROOT.rglob("*"):
    if any(part in SKIP_DIRS for part in p.parts) or p.is_dir():
        continue
    if p.name in {"library.db", "library.db-wal", "library.db-shm", ".env"}:
        problems.append(f"forbidden runtime/secret file: {p.relative_to(ROOT)}")
        continue
    if p.suffix.lower() == ".exe":
        problems.append(f"compiled EXE in source repository: {p.relative_to(ROOT)}")
        continue
    if p.name in SKIP_FILES:
        continue
    try:
        data = p.read_bytes()
    except OSError:
        continue
    for label, rx in PATTERNS.items():
        if rx.search(data):
            problems.append(f"{label}: {p.relative_to(ROOT)}")

if problems:
    print("Repository hygiene check FAILED:")
    for item in problems:
        print(" -", item)
    sys.exit(1)

print("Repository hygiene check passed.")
