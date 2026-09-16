from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path

from .db import Database
from .config import PATHS
from .media import collect_audio_paths, probe_audio, extract_artwork


def _humanize_in_our_time_piece(value: str) -> str:
    """Humanize CamelCase / letter-number boundaries while preserving hyphens as two spaces."""
    chunks = re.split(r"-+", str(value or ""))
    rendered: list[str] = []
    for chunk in chunks:
        text = chunk.replace("_", " ")
        text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
        text = re.sub(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            rendered.append(text)
    return "  ".join(rendered)


def _in_our_time_title_from_stem(stem: str) -> str | None:
    """Format filenames such as InOurTime-20060427-TheGreatExhibitionOf1851."""
    match = re.fullmatch(
        r"In[\s_-]*Our[\s_-]*Time[\s_-]*(?P<date>\d{8})(?:[\s_-]*(?P<episode>.+))?",
        str(stem or "").strip(),
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    date_code = match.group("date")
    episode_raw = (match.group("episode") or "").lstrip("-_ ")
    episode_name = _humanize_in_our_time_piece(episode_raw)
    heading = f"In Our Time  {date_code}"
    return f"{heading}\n{episode_name}" if episode_name else heading


def normalize_existing_in_our_time_titles(db: Database) -> int:
    """Bring previously imported matching files onto the same filename-title rule."""
    updated = 0
    for row in db.all("SELECT id, title, audio_path FROM episodes"):
        audio_path = str(row.get("audio_path") or "")
        if not audio_path:
            continue
        formatted = _in_our_time_title_from_stem(Path(audio_path).stem)
        if formatted and str(row.get("title") or "") != formatted:
            db.execute("UPDATE episodes SET title=? WHERE id=?", (formatted, row["id"]))
            updated += 1
    return updated


def _podcast_name(path: Path, requested: str | None, metadata: dict) -> str:
    if requested:
        return requested.strip()
    if metadata.get("album"):
        return str(metadata["album"]).strip()
    return path.parent.name or "Imported"


def import_paths(db: Database, paths: list[str], podcast_title: str | None = None) -> dict:
    files = collect_audio_paths(paths)
    imported = 0
    skipped = 0
    episode_ids: list[int] = []
    new_episode_ids: list[int] = []

    for path in files:
        existing = db.one("SELECT id FROM episodes WHERE audio_path=?", (str(path),))
        if existing:
            formatted_existing = _in_our_time_title_from_stem(path.stem)
            if formatted_existing:
                db.execute("UPDATE episodes SET title=? WHERE id=?", (formatted_existing, existing["id"]))
            skipped += 1
            episode_ids.append(existing["id"])
            continue

        meta = probe_audio(path)
        ptitle = _podcast_name(path, podcast_title, meta)
        artwork_key = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:20]
        artwork = extract_artwork(path, PATHS.cache / "artwork" / artwork_key)
        artwork_str = str(artwork) if artwork else None
        podcast = db.one("SELECT id FROM podcasts WHERE title=?", (ptitle,))
        if podcast:
            podcast_id = podcast["id"]
            if artwork_str:
                db.execute(
                    "UPDATE podcasts SET artwork_path=COALESCE(artwork_path, ?) WHERE id=?",
                    (artwork_str, podcast_id),
                )
        else:
            podcast_id = db.execute(
                "INSERT INTO podcasts(title, description, artwork_path) VALUES(?, '', ?)",
                (ptitle, artwork_str)
            )

        episode_id = db.execute(
            """INSERT INTO episodes(
                podcast_id, title, audio_path, artwork_path, duration_ms, published_at, metadata_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (
                podcast_id,
                _in_our_time_title_from_stem(path.stem) or meta.get("title") or path.stem,
                str(path),
                artwork_str,
                int(meta.get("duration_ms") or 0),
                meta.get("date"),
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        imported += 1
        episode_ids.append(episode_id)
        new_episode_ids.append(episode_id)

    if any("in our time" in str(Path(p)).lower() for p in paths) or any(
        "in our time" in (db.one(
            "SELECT p.title AS title FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.id=?",
            (eid,)
        ) or {}).get("title", "").lower()
        for eid in episode_ids
    ):
        ensure_in_our_time_collections(db)

    return {"imported": imported, "skipped": skipped, "episode_ids": episode_ids, "new_episode_ids": new_episode_ids}


def ensure_in_our_time_collections(db: Database) -> None:
    for name in ("Culture", "History", "Philosophy", "Religion", "Science"):
        if not db.one("SELECT id FROM collections WHERE name=?", (name,)):
            db.execute(
                """INSERT INTO collections(name, description, is_smart, query_json)
                   VALUES(?, ?, 1, ?)""",
                (
                    name,
                    "In Our Time discipline view",
                    json.dumps({"discipline": name}, ensure_ascii=False),
                ),
            )
