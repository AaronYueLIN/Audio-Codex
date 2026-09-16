from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from .db import Database
from .search import hybrid_search

R12_SCHEMA_VERSION = "audio-intelligence-2"
MAX_REWIND_MS = 15 * 60 * 1000
DEFAULT_REWIND_MS = 2 * 60 * 1000

ENTITY_SCHEMAS: dict[str, dict[str, Any]] = {
    "podcast": {"label": "Podcast", "id_field": "id", "actions": ["open", "search", "watch"]},
    "episode": {"label": "Episode", "id_field": "id", "actions": ["open", "play", "bookmark", "note", "transcribe", "analyse", "captions", "recap", "watch"]},
    "transcript_segment": {"label": "Transcript passage", "id_field": "segment_id", "actions": ["open", "play", "ask", "explain", "find_related", "note", "bookmark", "watch"]},
    "knowledge_entity": {"label": "Knowledge entity", "id_field": "id", "actions": ["open", "ask", "find_related", "annotate", "watch"]},
    "collection": {"label": "Collection", "id_field": "id", "actions": ["open", "search", "add_episode", "rename", "describe"]},
    "note": {"label": "Note", "id_field": "id", "actions": ["open_source", "ask", "watch"]},
    "bookmark": {"label": "Bookmark", "id_field": "id", "actions": ["open", "play", "ask"]},
    "memory": {"label": "Explicit local memory", "id_field": "id", "actions": ["forget"]},
    "listening_session": {"label": "Listening session", "id_field": "id", "actions": ["rewind", "recap"]},
    "listening_recap": {"label": "Listening Recap", "id_field": "id", "actions": ["open_source", "verify"]},
    "knowledge_watch": {"label": "Knowledge Watch", "id_field": "id", "actions": ["open", "pause", "resume"]},
    "knowledge_watch_event": {"label": "Knowledge Watch match", "id_field": "id", "actions": ["open_source", "acknowledge"]},
    "reference": {"label": "Reference provenance", "id_field": "id", "actions": ["verify", "open_source"]},
}

INTENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "search_content": {"side_effect": False, "inputs": ["query", "scope"], "description": "Find transcript or knowledge content."},
    "search_library": {"side_effect": False, "inputs": ["query"], "description": "Run a user-visible Audio Codex library search."},
    "open_view": {"side_effect": False, "inputs": ["view"], "description": "Navigate to a first-party Audio Codex view."},
    "open_episode": {"side_effect": False, "inputs": ["episode_id", "position_ms"], "description": "Open an episode, optionally at a timestamp."},
    "open_entity": {"side_effect": False, "inputs": ["entity_id"], "description": "Open a knowledge entity."},
    "open_collection": {"side_effect": False, "inputs": ["collection_id"], "description": "Open a collection."},
    "play_audio": {"side_effect": False, "inputs": ["episode_id", "position_ms"], "description": "Play an episode at a timestamp."},
    "rewind_listening": {"side_effect": False, "inputs": ["episode_id", "window_ms", "session_id"], "description": "Read only what the user actually heard in a recent listening window."},
    "verify_reference": {"side_effect": False, "inputs": ["object_type", "object_id", "claim_key"], "description": "Re-hash source transcript passages and report whether generated knowledge is still current."},
    "create_listening_recap": {"side_effect": True, "confirmation": True, "inputs": ["episode_id", "session_id"], "description": "Create a sourced recap for a listening session."},
    "create_note": {"side_effect": True, "confirmation": True, "inputs": ["episode_id", "entity_id", "segment_id", "body"], "description": "Save a local note."},
    "create_bookmark": {"side_effect": True, "confirmation": True, "inputs": ["episode_id", "position_ms", "label"], "description": "Save a playback bookmark."},
    "create_collection": {"side_effect": True, "confirmation": True, "inputs": ["name", "description"], "description": "Create a local collection."},
    "add_to_collection": {"side_effect": True, "confirmation": True, "inputs": ["collection_id", "episode_id"], "description": "Add an episode to a collection."},
    "update_collection": {"side_effect": True, "confirmation": True, "inputs": ["collection_id", "name", "description"], "description": "Update collection metadata."},
    "update_entity_annotation": {"side_effect": True, "confirmation": True, "inputs": ["entity_id", "description"], "description": "Update a knowledge annotation."},
    "transcribe_episode": {"side_effect": True, "confirmation": True, "inputs": ["episode_id"], "description": "Start transcript generation."},
    "analyze_episode": {"side_effect": True, "confirmation": True, "inputs": ["episode_id"], "description": "Start episode knowledge analysis."},
    "generate_captions": {"side_effect": True, "confirmation": True, "inputs": ["episode_id"], "description": "Generate timestamped subtitles."},
    "create_knowledge_watch": {"side_effect": True, "confirmation": True, "inputs": ["name", "query", "mode", "scope", "episode_id", "collection_id", "entity_id", "reference_text"], "description": "Watch future local knowledge for a user-defined condition."},
    "set_knowledge_watch": {"side_effect": True, "confirmation": True, "inputs": ["watch_id", "active"], "description": "Pause or resume a Knowledge Watch."},
    "acknowledge_watch_event": {"side_effect": True, "confirmation": True, "inputs": ["event_id"], "description": "Mark a surfaced Knowledge Watch match as read."},
    "remember_memory": {"side_effect": True, "confirmation": True, "inputs": ["content", "kind"], "description": "Save an explicit local AI preference only after confirmation."},
    "forget_memory": {"side_effect": True, "confirmation": True, "inputs": ["memory_id"], "description": "Delete an explicit local AI memory only after confirmation."},
}


INTENT_HANDLERS: dict[str, list[str]] = {
    "search_content": ["audiocodex.transcript.search", "audiocodex.entity.search"],
    "search_library": ["audiocodex.ui.search_library"],
    "open_view": ["audiocodex.ui.open_view"],
    "open_episode": ["audiocodex.ui.open_episode"],
    "open_entity": ["audiocodex.ui.open_entity"],
    "open_collection": ["audiocodex.ui.open_collection"],
    "play_audio": ["audiocodex.ui.play_audio"],
    "rewind_listening": ["audiocodex.listening.rewind"],
    "verify_reference": ["audiocodex.provenance.get"],
    "create_listening_recap": ["audiocodex.ui.create_listening_recap"],
    "create_note": ["audiocodex.ui.create_note"],
    "create_bookmark": ["audiocodex.ui.create_bookmark"],
    "create_collection": ["audiocodex.ui.create_collection"],
    "add_to_collection": ["audiocodex.ui.add_to_collection"],
    "update_collection": ["audiocodex.ui.update_collection"],
    "update_entity_annotation": ["audiocodex.ui.update_entity_annotation"],
    "transcribe_episode": ["audiocodex.ui.transcribe_episode"],
    "analyze_episode": ["audiocodex.ui.analyze_episode"],
    "generate_captions": ["audiocodex.ui.generate_captions"],
    "create_knowledge_watch": ["audiocodex.ui.create_knowledge_watch"],
    "set_knowledge_watch": ["audiocodex.ui.set_knowledge_watch"],
    "acknowledge_watch_event": ["audiocodex.ui.acknowledge_watch_event"],
    "remember_memory": ["audiocodex.ui.remember_memory"],
    "forget_memory": ["audiocodex.ui.forget_memory"],
}


PROFILE_SCHEMAS: dict[str, dict[str, Any]] = {
    "listen": {
        "purpose": "Understand what the user is hearing now or just heard.",
        "freshness": "local",
        "mutation_policy": "confirm",
        "tool_scope": ["listening", "transcript", "playback", "notes", "bookmarks"],
    },
    "ask": {
        "purpose": "Answer from the current Audio Codex entity and local archive.",
        "freshness": "local",
        "mutation_policy": "confirm",
        "tool_scope": ["episode", "transcript", "knowledge", "collections", "provenance"],
    },
    "research": {
        "purpose": "Synthesize deeply across the local archive with source provenance.",
        "freshness": "local",
        "mutation_policy": "confirm",
        "tool_scope": ["episode", "transcript", "knowledge", "collections", "provenance"],
    },
    "organize": {
        "purpose": "Organize notes, collections, memories and user-created Knowledge Watches.",
        "freshness": "local",
        "mutation_policy": "confirm",
        "tool_scope": ["notes", "bookmarks", "collections", "memory", "watches", "provenance"],
    },
    "act": {
        "purpose": "Resolve a concrete object and prepare the smallest confirmable app action.",
        "freshness": "local",
        "mutation_policy": "confirm",
        "tool_scope": ["entity-resolution", "app-actions"],
    },
    "vision": {
        "purpose": "Understand an attached image or screenshot together with current app context.",
        "freshness": "local",
        "mutation_policy": "confirm",
        "tool_scope": ["vision", "entity-resolution", "transcript", "knowledge", "app-actions"],
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def schema_manifest() -> dict:
    return {
        "version": R12_SCHEMA_VERSION,
        "entities": ENTITY_SCHEMAS,
        "intents": {name: {**spec, "handlers": INTENT_HANDLERS.get(name, [])} for name, spec in INTENT_SCHEMAS.items()},
        "profiles": PROFILE_SCHEMAS,
        "principles": {
            "entity_grounded": True,
            "onscreen_objects_are_ids_not_dom_truth": True,
            "mutations_require_confirmation": True,
            "personal_context_is_tool_gated": True,
            "generated_knowledge_keeps_provenance": True,
            "current_web_claims_require_verified_retrieval": True,
        },
    }


def _clean_text(value: Any, limit: int = 12000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _fmt_ms(ms: int) -> str:
    sec = max(0, int(ms or 0)) // 1000
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _segment_rows(db: Database, episode_id: int, ranges: list[dict] | None = None, *, limit: int = 800) -> list[dict]:
    episode_id = int(episode_id)
    if not ranges:
        return db.all(
            """SELECT id AS segment_id, segment_index, start_ms, end_ms,
                      COALESCE(user_speaker_label, speaker_label, '') AS speaker_label,
                      COALESCE(user_text, text) AS text
               FROM transcript_segments WHERE episode_id=? ORDER BY segment_index LIMIT ?""",
            (episode_id, int(limit)),
        )
    seen: set[int] = set()
    out: list[dict] = []
    for r in ranges[:64]:
        start_ms = max(0, int(r.get("start_ms") or 0))
        end_ms = max(start_ms, int(r.get("end_ms") or start_ms))
        if end_ms > start_ms:
            sql = """SELECT id AS segment_id, segment_index, start_ms, end_ms,
                            COALESCE(user_speaker_label, speaker_label, '') AS speaker_label,
                            COALESCE(user_text, text) AS text
                     FROM transcript_segments
                     WHERE episode_id=? AND end_ms>? AND start_ms<?
                     ORDER BY segment_index LIMIT ?"""
            params = (episode_id, start_ms, end_ms, int(limit))
        else:
            sql = """SELECT id AS segment_id, segment_index, start_ms, end_ms,
                            COALESCE(user_speaker_label, speaker_label, '') AS speaker_label,
                            COALESCE(user_text, text) AS text
                     FROM transcript_segments
                     WHERE episode_id=? AND start_ms<=? AND end_ms>=?
                     ORDER BY segment_index LIMIT ?"""
            params = (episode_id, start_ms, start_ms, int(limit))
        for row in db.all(sql, params):
            sid = int(row["segment_id"])
            if sid in seen:
                continue
            seen.add(sid)
            out.append(row)
            if len(out) >= limit:
                return sorted(out, key=lambda x: int(x.get("segment_index") or 0))
    return sorted(out, key=lambda x: int(x.get("segment_index") or 0))


def transcript_source_hash(rows: list[dict]) -> str:
    h = hashlib.sha256()
    for row in rows:
        value = "\x1f".join([
            str(row.get("segment_id") or row.get("id") or ""),
            str(row.get("start_ms") or 0),
            str(row.get("end_ms") or 0),
            str(row.get("speaker_label") or ""),
            str(row.get("text") or ""),
        ])
        h.update(value.encode("utf-8", "replace"))
        h.update(b"\n")
    return h.hexdigest()


def _parse_ranges(raw: Any) -> list[dict]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw[:64]:
        if not isinstance(item, dict):
            continue
        try:
            start = max(0, int(item.get("start_ms") or 0))
            end = max(start, int(item.get("end_ms") or start))
        except Exception:
            continue
        out.append({"start_ms": start, "end_ms": end})
    return out


def _extend_ranges(
    ranges: list[dict], position_ms: int, previous_position_ms: int | None = None, *, force_new: bool = False
) -> list[dict]:
    """Extend one actually-heard range, or start a new range after an explicit seek.

    A seek is a semantic discontinuity even when it is only a few seconds. Treating a
    short seek as ordinary playback would incorrectly mark skipped transcript as heard.
    """
    pos = max(0, int(position_ms))
    prev = None if previous_position_ms is None else max(0, int(previous_position_ms))
    if not ranges:
        return [{"start_ms": pos, "end_ms": pos}]
    if force_new:
        last = ranges[-1]
        if int(last.get("start_ms") or 0) == pos and int(last.get("end_ms") or 0) == pos:
            return ranges[-64:]
        ranges.append({"start_ms": pos, "end_ms": pos})
        return ranges[-64:]
    last = dict(ranges[-1])
    anchor = int(last.get("end_ms") or last.get("start_ms") or pos)
    # Progress touches are normally ~5 seconds apart. Keep a small tolerance for
    # scheduler stalls, but never bridge a large discontinuity as if it were heard.
    if prev is not None and abs(pos - prev) <= 15_000:
        anchor = prev
    if abs(pos - anchor) <= 15_000:
        last["start_ms"] = min(int(last.get("start_ms") or pos), pos)
        last["end_ms"] = max(int(last.get("end_ms") or pos), pos)
        ranges[-1] = last
    else:
        ranges.append({"start_ms": pos, "end_ms": pos})
    return ranges[-64:]


def touch_listening_session(
    db: Database,
    episode_id: int,
    position_ms: int,
    *,
    event: str = "progress",
    session_id: str | None = None,
    previous_position_ms: int | None = None,
) -> dict:
    episode_id = int(episode_id)
    if not db.one("SELECT id FROM episodes WHERE id=?", (episode_id,)):
        raise RuntimeError("Episode not found")
    event = str(event or "progress").strip().lower()
    if event not in {"play", "progress", "seek", "pause", "ended"}:
        event = "progress"
    sid = str(session_id or "").strip()
    row = db.one("SELECT * FROM listening_sessions WHERE id=?", (sid,)) if sid else None
    if row and int(row.get("episode_id") or 0) != episode_id:
        row = None
    if not row:
        sid = uuid.uuid4().hex
        ranges = _extend_ranges([], position_ms, previous_position_ms, force_new=(event == "seek"))
        db.execute(
            """INSERT INTO listening_sessions(id, episode_id, status, ranges_json, last_position_ms, started_at, updated_at)
               VALUES(?, ?, ?, ?, ?, ?, ?)""",
            (sid, episode_id, {"pause": "paused", "ended": "ended"}.get(event, "active"),
             json.dumps(ranges), max(0, int(position_ms)), utc_now(), utc_now()),
        )
    else:
        ranges = _extend_ranges(_parse_ranges(row.get("ranges_json")), position_ms, previous_position_ms, force_new=(event == "seek"))
        status = {"pause": "paused", "ended": "ended"}.get(event, "active")
        ended_at = utc_now() if event == "ended" else row.get("ended_at")
        db.execute(
            """UPDATE listening_sessions SET status=?, ranges_json=?, last_position_ms=?, updated_at=?, ended_at=? WHERE id=?""",
            (status, json.dumps(ranges), max(0, int(position_ms)), utc_now(), ended_at, sid),
        )
    result = db.one("SELECT * FROM listening_sessions WHERE id=?", (sid,)) or {}
    result["ranges"] = _parse_ranges(result.pop("ranges_json", "[]"))
    return result


def current_listening_session(db: Database, episode_id: int | None = None) -> dict | None:
    if episode_id:
        row = db.one(
            "SELECT * FROM listening_sessions WHERE episode_id=? ORDER BY updated_at DESC LIMIT 1", (int(episode_id),)
        )
    else:
        row = db.one("SELECT * FROM listening_sessions ORDER BY updated_at DESC LIMIT 1")
    if not row:
        return None
    row["ranges"] = _parse_ranges(row.pop("ranges_json", "[]"))
    return row


def listening_rewind(
    db: Database, episode_id: int, playhead_ms: int, window_ms: int = DEFAULT_REWIND_MS, *, session_id: str | None = None
) -> dict:
    window = max(15_000, min(MAX_REWIND_MS, int(window_ms or DEFAULT_REWIND_MS)))
    playhead = max(0, int(playhead_ms or 0))
    episode = db.one(
        """SELECT e.id, e.title, e.podcast_id, p.title AS podcast_title
           FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.id=?""", (int(episode_id),)
    )
    if not episode:
        raise RuntimeError("Episode not found")
    start = max(0, playhead - window)
    session = None
    if session_id:
        session = db.one("SELECT * FROM listening_sessions WHERE id=? AND episode_id=?", (str(session_id), int(episode_id)))
    if not session:
        session = db.one("SELECT * FROM listening_sessions WHERE episode_id=? ORDER BY updated_at DESC LIMIT 1", (int(episode_id),))

    heard_ranges: list[dict] = []
    if session:
        for r in _parse_ranges(session.get("ranges_json")):
            left, right = max(start, int(r["start_ms"])), min(playhead + 2500, int(r["end_ms"]))
            if right >= left:
                heard_ranges.append({"start_ms": left, "end_ms": right})
    # If no listening session exists (for example an older install), preserve the useful legacy window.
    # Once a session exists, skipped gaps are never filled in implicitly.
    source_ranges = heard_ranges if session else [{"start_ms": start, "end_ms": playhead + 2500}]
    rows = _segment_rows(db, int(episode_id), source_ranges, limit=240) if source_ranges else []
    return {
        "episode": episode,
        "session_id": (session or {}).get("id"),
        "actual_listening": bool(session),
        "heard_ranges": heard_ranges,
        "playhead_ms": playhead,
        "window_ms": window,
        "start_ms": start,
        "end_ms": playhead,
        "segments": rows,
        "text": "\n".join(
            f"[{_fmt_ms(r['start_ms'])}-{_fmt_ms(r['end_ms'])}]"
            + (f" {r.get('speaker_label')}:" if r.get("speaker_label") else "")
            + f" {r.get('text') or ''}"
            for r in rows
        ),
        "source_hash": transcript_source_hash(rows),
    }


def _episode_audio_reference(db: Database, episode_id: int | None, *, force_hash: bool = False) -> dict | None:
    if not episode_id:
        return None
    row = db.one("SELECT audio_path FROM episodes WHERE id=?", (int(episode_id),))
    if not row or not str(row.get("audio_path") or "").strip():
        return None
    path = Path(str(row["audio_path"]))
    try:
        stat = path.stat()
        size, mtime_ns = int(stat.st_size), int(stat.st_mtime_ns)
    except OSError:
        return {"source_type": "audio", "available": False, "file_size": 0, "mtime_ns": 0, "sha256": ""}
    cached = db.one("SELECT * FROM audio_fingerprints WHERE episode_id=?", (int(episode_id),))
    digest = ""
    if cached and not force_hash and int(cached.get("file_size") or 0) == size and int(cached.get("mtime_ns") or 0) == mtime_ns:
        digest = str(cached.get("sha256") or "")
    if not digest:
        h = hashlib.sha256()
        try:
            with path.open("rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    h.update(chunk)
            digest = h.hexdigest()
        except OSError:
            return {"source_type": "audio", "available": False, "file_size": size, "mtime_ns": mtime_ns, "sha256": ""}
        db.execute(
            """INSERT INTO audio_fingerprints(episode_id, file_size, mtime_ns, sha256, updated_at)
               VALUES(?, ?, ?, ?, ?)
               ON CONFLICT(episode_id) DO UPDATE SET file_size=excluded.file_size,
                 mtime_ns=excluded.mtime_ns, sha256=excluded.sha256, updated_at=excluded.updated_at""",
            (int(episode_id), size, mtime_ns, digest, utc_now()),
        )
    return {"source_type": "audio", "available": True, "file_size": size, "mtime_ns": mtime_ns, "sha256": digest}


def record_provenance(
    db: Database,
    *,
    object_type: str,
    object_id: str | int,
    claim_key: str,
    episode_id: int | None,
    source_rows: list[dict],
    model: str = "",
    prompt_version: str = R12_SCHEMA_VERSION,
) -> dict:
    refs = [
        {
            "source_type": "transcript",
            "segment_id": int(r.get("segment_id") or r.get("id") or 0),
            "start_ms": int(r.get("start_ms") or 0),
            "end_ms": int(r.get("end_ms") or 0),
        }
        for r in source_rows if int(r.get("segment_id") or r.get("id") or 0) > 0
    ]
    audio_ref = _episode_audio_reference(db, episode_id)
    if audio_ref:
        refs.append(audio_ref)
    source_hash = transcript_source_hash(source_rows)
    source_kind = "transcript+audio" if audio_ref else "transcript"
    db.execute(
        """INSERT INTO intelligence_provenance(
               object_type, object_id, claim_key, episode_id, source_kind, source_refs_json,
               source_hash, model, prompt_version, created_at
           ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(object_type, object_id, claim_key) DO UPDATE SET
               episode_id=excluded.episode_id, source_kind=excluded.source_kind,
               source_refs_json=excluded.source_refs_json, source_hash=excluded.source_hash,
               model=excluded.model, prompt_version=excluded.prompt_version, created_at=excluded.created_at""",
        (str(object_type), str(object_id), str(claim_key), episode_id, source_kind,
         json.dumps(refs, ensure_ascii=False), source_hash, str(model or ""), str(prompt_version or ""), utc_now()),
    )
    return provenance_get(db, object_type, object_id, claim_key=claim_key)[0]


def provenance_get(
    db: Database, object_type: str, object_id: str | int, *, claim_key: str | None = None, full_audio_verify: bool = False
) -> list[dict]:
    args: list[Any] = [str(object_type), str(object_id)]
    where = "object_type=? AND object_id=?"
    if claim_key is not None:
        where += " AND claim_key=?"
        args.append(str(claim_key))
    rows = db.all(f"SELECT * FROM intelligence_provenance WHERE {where} ORDER BY id DESC", args)
    for row in rows:
        refs = _parse_refs(row.get("source_refs_json"))
        row["source_refs"] = refs
        row.pop("source_refs_json", None)
        ids = [int(x.get("segment_id") or 0) for x in refs if int(x.get("segment_id") or 0) > 0]
        current_rows: list[dict] = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            current_rows = db.all(
                f"""SELECT id AS segment_id, start_ms, end_ms,
                           COALESCE(user_speaker_label, speaker_label, '') AS speaker_label,
                           COALESCE(user_text, text) AS text
                    FROM transcript_segments WHERE id IN ({placeholders}) ORDER BY segment_index""",
                ids,
            )
        current_hash = transcript_source_hash(current_rows) if current_rows else ""
        transcript_stale = bool(ids and current_hash != str(row.get("source_hash") or ""))
        transcript_available = len(current_rows) == len(ids) if ids else True

        recorded_audio = next((x for x in refs if x.get("source_type") == "audio"), None)
        audio_available = True
        audio_stale = False
        current_audio = None
        if recorded_audio is not None:
            current_audio = _episode_audio_reference(db, row.get("episode_id"), force_hash=bool(full_audio_verify))
            audio_available = bool(current_audio and current_audio.get("available"))
            if not audio_available:
                audio_stale = True
            elif full_audio_verify:
                audio_stale = str(current_audio.get("sha256") or "") != str(recorded_audio.get("sha256") or "")
            else:
                # Passive checks are instant: a size/mtime change is enough to require explicit re-verification.
                audio_stale = (
                    int(current_audio.get("file_size") or 0) != int(recorded_audio.get("file_size") or 0)
                    or int(current_audio.get("mtime_ns") or 0) != int(recorded_audio.get("mtime_ns") or 0)
                )
        row["current_source_hash"] = current_hash
        row["transcript_stale"] = transcript_stale
        row["audio_stale"] = audio_stale
        row["audio_available"] = audio_available
        row["audio_verified"] = bool(recorded_audio is not None and full_audio_verify and not audio_stale and audio_available)
        row["stale"] = bool(transcript_stale or audio_stale)
        row["sources_available"] = bool(transcript_available and audio_available)
    return rows


def _parse_refs(raw: Any) -> list[dict]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        return []
    return [dict(x) for x in raw if isinstance(x, dict)][:1000]


def record_episode_summary_provenance(db: Database, episode_id: int, *, model: str = "") -> dict | None:
    ep = db.one("SELECT id, summary FROM episodes WHERE id=?", (int(episode_id),))
    if not ep or not str(ep.get("summary") or "").strip():
        return None
    rows = _segment_rows(db, int(episode_id), limit=2000)
    if not rows:
        return None
    return record_provenance(
        db, object_type="episode_summary", object_id=int(episode_id), claim_key="summary",
        episode_id=int(episode_id), source_rows=rows, model=model, prompt_version="episode-analysis-r12",
    )


def _safe_json(text: str) -> dict:
    value = str(text or "").strip()
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        start, end = value.find("{"), value.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(value[start:end + 1])
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                pass
    return {}


def generate_listening_recap(db: Database, episode_id: int, *, session_id: str | None = None) -> dict:
    episode_id = int(episode_id)
    ep = db.one(
        """SELECT e.id, e.title, e.podcast_id, p.title AS podcast_title, e.playhead_ms
           FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.id=?""", (episode_id,)
    )
    if not ep:
        raise RuntimeError("Episode not found")
    session = None
    if session_id:
        session = db.one("SELECT * FROM listening_sessions WHERE id=? AND episode_id=?", (str(session_id), episode_id))
    if not session:
        session = db.one("SELECT * FROM listening_sessions WHERE episode_id=? ORDER BY updated_at DESC LIMIT 1", (episode_id,))
    ranges = _parse_ranges((session or {}).get("ranges_json"))
    if not ranges:
        center = max(0, int(ep.get("playhead_ms") or 0))
        ranges = [{"start_ms": max(0, center - 5 * 60 * 1000), "end_ms": center}]
    rows = _segment_rows(db, episode_id, ranges, limit=320)
    if not rows:
        raise RuntimeError("No transcript is available for the listening range")
    source_hash = transcript_source_hash(rows)

    if session:
        cached = db.one(
            "SELECT * FROM listening_recaps WHERE session_id=? AND source_hash=? ORDER BY id DESC LIMIT 1",
            (session["id"], source_hash),
        )
        if cached:
            return _recap_row(db, cached, rows)

    sources: list[dict] = []
    prompt_rows: list[str] = []
    for i, row in enumerate(rows, start=1):
        key = f"T{i}"
        prompt_rows.append(
            f"[{key}] [{_fmt_ms(row['start_ms'])}-{_fmt_ms(row['end_ms'])}]"
            + (f" {row.get('speaker_label')}:" if row.get("speaker_label") else "")
            + f" {row.get('text') or ''}"
        )
        sources.append({
            "type": "transcript", "key": key, "citation": key,
            "segment_id": row.get("segment_id"), "episode_id": episode_id,
            "episode_title": ep.get("title") or "", "podcast_id": ep.get("podcast_id"),
            "podcast_title": ep.get("podcast_title") or "", "start_ms": row.get("start_ms"),
            "end_ms": row.get("end_ms"), "speaker_label": row.get("speaker_label") or "",
            "text": row.get("text") or "",
        })

    model_used = "local-fallback"
    payload: dict[str, Any]
    try:
        from .ai import AISettings, ChatProvider
        settings = AISettings.from_db(db)
        provider = ChatProvider(settings, db)
        status = provider.status()
        if not status.get("available"):
            raise RuntimeError(status.get("detail") or "AI unavailable")
        system = (
            "You create concise Listening Recaps for Audio Codex. Return one JSON object only with keys "
            "title (string), summary (string), key_points (array of 3-6 strings), people_topics (array of strings), "
            "follow_ups (array of 0-4 useful questions). Ground every factual item in the supplied transcript. "
            "When helpful, include transcript citation markers like [T1] inside summary/key_points. Do not invent facts."
        )
        user = (
            f"Podcast: {ep.get('podcast_title')}\nEpisode: {ep.get('title')}\n"
            f"Listening ranges: {json.dumps(ranges)}\n\nTRANSCRIPT:\n" + "\n".join(prompt_rows)
        )
        model_override = settings.pro_model or settings.chat_model
        generated = provider.chat(
            system, user, model_override=model_override, thinking=False, use_tools=False,
            allow_client_actions=False, include_personal_context=False,
            json_mode=True, max_output_tokens=min(2200, settings.chat_max_tokens), temperature=0.1,
        )
        payload = _safe_json(generated)
        if not payload:
            raise RuntimeError("Recap JSON was empty")
        model_used = str((provider.last_run or {}).get("model") or model_override or "")
    except Exception:
        # Useful offline fallback: preserve the exact listening span and several representative passages,
        # without pretending to generate semantic conclusions that require a model.
        first, last = rows[0], rows[-1]
        samples = [rows[0], rows[len(rows)//2], rows[-1]] if len(rows) >= 3 else rows
        payload = {
            "title": f"Listening recap · {ep.get('title')}",
            "summary": f"You listened from {_fmt_ms(first['start_ms'])} to {_fmt_ms(last['end_ms'])}. Connect DeepSeek to generate a semantic recap.",
            "key_points": [f"[{sources[rows.index(r)]['citation']}] {str(r.get('text') or '')[:240]}" for r in samples],
            "people_topics": [],
            "follow_ups": [],
            "fallback": True,
        }

    title = _clean_text(payload.get("title") or f"Listening recap · {ep.get('title')}", 240)
    summary = str(payload.get("summary") or "").strip()[:12000]
    key_points = [_clean_text(x, 1000) for x in (payload.get("key_points") or []) if _clean_text(x, 1000)][:8]
    people_topics = [_clean_text(x, 240) for x in (payload.get("people_topics") or []) if _clean_text(x, 240)][:20]
    follow_ups = [_clean_text(x, 600) for x in (payload.get("follow_ups") or []) if _clean_text(x, 600)][:6]
    recap_payload = {
        "title": title, "summary": summary, "key_points": key_points,
        "people_topics": people_topics, "follow_ups": follow_ups,
        "fallback": bool(payload.get("fallback")),
    }
    rid = db.execute(
        """INSERT INTO listening_recaps(session_id, episode_id, payload_json, sources_json, source_hash, model, created_at)
           VALUES(?, ?, ?, ?, ?, ?, ?)""",
        ((session or {}).get("id"), episode_id, json.dumps(recap_payload, ensure_ascii=False),
         json.dumps(sources, ensure_ascii=False), source_hash, model_used, utc_now()),
    )
    record_provenance(
        db, object_type="listening_recap", object_id=rid, claim_key="recap", episode_id=episode_id,
        source_rows=rows, model=model_used, prompt_version="listening-recap-r12",
    )
    row = db.one("SELECT * FROM listening_recaps WHERE id=?", (rid,)) or {}
    return _recap_row(db, row, rows)


def _recap_row(db: Database, row: dict, current_rows: list[dict] | None = None) -> dict:
    out = dict(row)
    try:
        out["recap"] = json.loads(out.pop("payload_json") or "{}")
    except Exception:
        out["recap"] = {}
    try:
        out["sources"] = json.loads(out.pop("sources_json") or "[]")
    except Exception:
        out["sources"] = []
    rows = current_rows
    if rows is None:
        ids = [int(s.get("segment_id") or 0) for s in out["sources"] if int(s.get("segment_id") or 0) > 0]
        if ids:
            placeholders = ",".join("?" for _ in ids)
            rows = db.all(
                f"""SELECT id AS segment_id, start_ms, end_ms,
                           COALESCE(user_speaker_label, speaker_label, '') AS speaker_label,
                           COALESCE(user_text, text) AS text
                    FROM transcript_segments WHERE id IN ({placeholders}) ORDER BY segment_index""", ids,
            )
        else:
            rows = []
    out["stale"] = bool(rows and transcript_source_hash(rows) != str(out.get("source_hash") or ""))
    return out



def list_listening_recaps(db: Database, *, episode_id: int | None = None, limit: int = 50) -> dict:
    limit = max(1, min(200, int(limit or 50)))
    if episode_id is not None:
        rows = db.all(
            "SELECT * FROM listening_recaps WHERE episode_id=? ORDER BY id DESC LIMIT ?",
            (int(episode_id), limit),
        )
    else:
        rows = db.all("SELECT * FROM listening_recaps ORDER BY id DESC LIMIT ?", (limit,))
    recaps = [_recap_row(db, row) for row in rows]
    return {"recaps": recaps, "count": len(recaps)}

def create_watch(
    db: Database,
    *,
    name: str,
    query: str,
    mode: str = "mention",
    scope: str = "library",
    episode_id: int | None = None,
    collection_id: int | None = None,
    entity_id: int | None = None,
    reference_text: str = "",
) -> dict:
    mode = str(mode or "mention").strip().lower()
    if mode not in {"mention", "topic", "contradiction", "new_episode"}:
        raise RuntimeError("Unsupported watch mode")
    scope = str(scope or "library").strip().lower()
    if scope not in {"library", "episode", "collection", "entity"}:
        raise RuntimeError("Unsupported watch scope")
    query = _clean_text(query, 1000)
    reference_text = _clean_text(reference_text, 8000)
    if mode != "new_episode" and not query:
        raise RuntimeError("A watch query is required")
    if mode == "contradiction" and not reference_text:
        raise RuntimeError("A contradiction watch needs reference text")
    name = _clean_text(name, 180) or (f"Watch: {query[:80]}" if query else "New episode watch")
    if episode_id is not None and not db.one("SELECT id FROM episodes WHERE id=?", (int(episode_id),)):
        raise RuntimeError("Episode not found")
    if collection_id is not None and not db.one("SELECT id FROM collections WHERE id=?", (int(collection_id),)):
        raise RuntimeError("Collection not found")
    if entity_id is not None and not db.one("SELECT id FROM entities WHERE id=?", (int(entity_id),)):
        raise RuntimeError("Entity not found")
    wid = db.execute(
        """INSERT INTO knowledge_watches(name, query, mode, scope, episode_id, collection_id, entity_id,
                                         reference_text, active, created_at, updated_at)
           VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        (name, query, mode, scope, episode_id, collection_id, entity_id, reference_text, utc_now(), utc_now()),
    )
    return db.one("SELECT * FROM knowledge_watches WHERE id=?", (wid,)) or {"id": wid}


def set_watch_active(db: Database, watch_id: int, active: bool) -> dict:
    row = db.one("SELECT * FROM knowledge_watches WHERE id=?", (int(watch_id),))
    if not row:
        raise RuntimeError("Knowledge Watch not found")
    db.execute("UPDATE knowledge_watches SET active=?, updated_at=? WHERE id=?", (1 if active else 0, utc_now(), int(watch_id)))
    return db.one("SELECT * FROM knowledge_watches WHERE id=?", (int(watch_id),)) or {}


def list_watches(db: Database, *, include_events: bool = False, limit: int = 100) -> dict:
    watches = db.all(
        """SELECT w.*,
                  (SELECT COUNT(*) FROM knowledge_watch_events e WHERE e.watch_id=w.id AND e.acknowledged=0) AS unread_count,
                  (SELECT MAX(created_at) FROM knowledge_watch_events e WHERE e.watch_id=w.id) AS last_event_at
           FROM knowledge_watches w ORDER BY active DESC, updated_at DESC LIMIT ?""",
        (max(1, min(500, int(limit))),),
    )
    result: dict[str, Any] = {"watches": watches}
    if include_events:
        result["events"] = list_watch_events(db, unacknowledged=False, limit=min(100, max(20, limit))).get("events", [])
    return result


def list_watch_events(db: Database, *, unacknowledged: bool = False, limit: int = 100) -> dict:
    where = "WHERE e.acknowledged=0" if unacknowledged else ""
    rows = db.all(
        f"""SELECT e.*, w.name AS watch_name, w.query AS watch_query, w.mode AS watch_mode,
                   ep.title AS episode_title, p.title AS podcast_title
            FROM knowledge_watch_events e
            JOIN knowledge_watches w ON w.id=e.watch_id
            LEFT JOIN episodes ep ON ep.id=e.episode_id
            LEFT JOIN podcasts p ON p.id=ep.podcast_id
            {where}
            ORDER BY e.created_at DESC LIMIT ?""",
        (max(1, min(500, int(limit))),),
    )
    for row in rows:
        try:
            row["evidence"] = json.loads(row.pop("evidence_json") or "{}")
        except Exception:
            row["evidence"] = {}
    return {"events": rows}


def acknowledge_watch_event(db: Database, event_id: int) -> dict:
    if not db.one("SELECT id FROM knowledge_watch_events WHERE id=?", (int(event_id),)):
        raise RuntimeError("Knowledge Watch event not found")
    db.execute("UPDATE knowledge_watch_events SET acknowledged=1 WHERE id=?", (int(event_id),))
    return {"ok": True, "id": int(event_id)}


def _watch_applies(db: Database, watch: dict, episode_id: int) -> bool:
    scope = str(watch.get("scope") or "library")
    if scope == "library":
        return True
    if scope == "episode":
        return int(watch.get("episode_id") or 0) == int(episode_id)
    if scope == "collection":
        return bool(db.one(
            "SELECT 1 AS ok FROM collection_episodes WHERE collection_id=? AND episode_id=?",
            (int(watch.get("collection_id") or 0), int(episode_id)),
        ))
    if scope == "entity":
        entity_id = int(watch.get("entity_id") or 0)
        return bool(entity_id and db.one(
            "SELECT 1 AS ok FROM episode_entities WHERE entity_id=? AND episode_id=? LIMIT 1",
            (entity_id, int(episode_id)),
        ))
    return False


def _contradiction_assessment(db: Database, watch: dict, episode: dict, hits: list[dict]) -> dict:
    evidence = "\n".join(
        f"[{_fmt_ms(h.get('start_ms') or 0)}] {h.get('text') or ''}" for h in hits[:12]
    )[:16000]
    try:
        from .ai import AISettings, ChatProvider
        settings = AISettings.from_db(db)
        provider = ChatProvider(settings, db)
        if not provider.status().get("available"):
            return {"contradiction": False, "confidence": 0, "summary": "", "ai_unavailable": True}
        system = (
            "Compare one reference claim with podcast evidence. Return JSON only: "
            "{contradiction:boolean, confidence:number 0..1, summary:string}. "
            "Mark contradiction true only for a genuine factual/logical conflict, not mere absence or nuance."
        )
        user = f"REFERENCE:\n{watch.get('reference_text') or ''}\n\nEPISODE: {episode.get('title')}\nEVIDENCE:\n{evidence}"
        text = provider.chat(
            system, user, model_override=settings.pro_model or settings.chat_model,
            thinking=True, use_tools=False, allow_client_actions=False, include_personal_context=False,
            json_mode=True, max_output_tokens=900, temperature=0.0,
        )
        return _safe_json(text)
    except Exception:
        return {"contradiction": False, "confidence": 0, "summary": ""}


def evaluate_watches_for_episode(db: Database, episode_id: int) -> dict:
    episode_id = int(episode_id)
    episode = db.one(
        """SELECT e.id, e.title, e.podcast_id, p.title AS podcast_title, e.transcript_status
           FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.id=?""", (episode_id,)
    )
    if not episode:
        return {"evaluated": 0, "events": 0}
    watches = db.all("SELECT * FROM knowledge_watches WHERE active=1 ORDER BY id")
    created = 0
    evaluated = 0
    for watch in watches:
        if not _watch_applies(db, watch, episode_id):
            continue
        evaluated += 1
        mode = str(watch.get("mode") or "mention")
        query = str(watch.get("query") or "").strip()
        if mode == "new_episode":
            hits: list[dict] = []
        else:
            hits = hybrid_search(db, query, episode_ids=[episode_id], limit=30) if query else []
            if not hits:
                db.execute("UPDATE knowledge_watches SET last_checked_at=?, updated_at=? WHERE id=?", (utc_now(), utc_now(), watch["id"]))
                continue
        if db.one(
            "SELECT id FROM knowledge_watch_events WHERE watch_id=? AND episode_id=? AND event_type=? LIMIT 1",
            (watch["id"], episode_id, mode),
        ):
            db.execute("UPDATE knowledge_watches SET last_checked_at=? WHERE id=?", (utc_now(), watch["id"]))
            continue

        should_create = True
        summary = ""
        extra: dict[str, Any] = {}
        if mode == "contradiction":
            assessment = _contradiction_assessment(db, watch, episode, hits)
            confidence = float(assessment.get("confidence") or 0)
            should_create = bool(assessment.get("contradiction")) and confidence >= 0.55
            summary = _clean_text(assessment.get("summary"), 1000)
            extra["assessment"] = assessment
        elif mode == "new_episode":
            summary = f"New matching episode is available: {episode.get('title')}"
        else:
            first = hits[0]
            summary = _clean_text(first.get("text"), 900)

        if should_create:
            evidence = {
                "query": query,
                "hits": [
                    {"segment_id": h.get("segment_id"), "start_ms": h.get("start_ms"), "end_ms": h.get("end_ms"), "text": h.get("text")}
                    for h in hits[:12]
                ],
                **extra,
            }
            segment_id = int(hits[0].get("segment_id") or 0) if hits else None
            db.execute(
                """INSERT INTO knowledge_watch_events(watch_id, episode_id, segment_id, event_type, summary, evidence_json, acknowledged, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, 0, ?)""",
                (watch["id"], episode_id, segment_id, mode, summary, json.dumps(evidence, ensure_ascii=False), utc_now()),
            )
            created += 1
            db.execute(
                "UPDATE knowledge_watches SET last_checked_at=?, last_match_at=?, updated_at=? WHERE id=?",
                (utc_now(), utc_now(), utc_now(), watch["id"]),
            )
        else:
            db.execute("UPDATE knowledge_watches SET last_checked_at=?, updated_at=? WHERE id=?", (utc_now(), utc_now(), watch["id"]))
    return {"evaluated": evaluated, "events": created}
