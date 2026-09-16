from __future__ import annotations

import json
import math
import re
from typing import Any


from .db import Database
from .search import hybrid_search, scope_episode_ids

MCP_PROTOCOL_VERSION = "2025-06-18"
DEFAULT_CONTEXT_TOKENS = 1_000_000
MAX_CONTEXT_TOKENS = 1_000_000
_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def clamp_context_tokens(value: Any) -> int:
    try:
        n = int(value)
    except Exception:
        n = DEFAULT_CONTEXT_TOKENS
    return max(32_000, min(MAX_CONTEXT_TOKENS, n))


def estimate_tokens(text: str) -> int:
    text = str(text or "")
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = max(0, len(text) - cjk)
    return cjk + math.ceil(other / 3.6)


def _fmt_ms(ms: int) -> str:
    sec = max(0, int(ms or 0)) // 1000
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def transcript_read(
    db: Database,
    episode_id: int,
    *,
    cursor: int = 0,
    max_tokens: int = DEFAULT_CONTEXT_TOKENS,
    start_ms: int | None = None,
    end_ms: int | None = None,
    include_timestamps: bool = True,
) -> dict:
    budget = clamp_context_tokens(max_tokens)
    episode = db.one(
        """SELECT e.id, e.title, e.duration_ms, e.summary, e.discipline, e.podcast_id,
                  p.title AS podcast_title
           FROM episodes e JOIN podcasts p ON p.id=e.podcast_id
           WHERE e.id=?""",
        (episode_id,),
    )
    if not episode:
        raise RuntimeError("Episode not found")

    where = ["s.episode_id=?", "s.segment_index>=?"]
    args: list[Any] = [episode_id, max(0, int(cursor or 0))]
    if start_ms is not None:
        where.append("s.end_ms>=?")
        args.append(max(0, int(start_ms)))
    if end_ms is not None:
        where.append("s.start_ms<=?")
        args.append(max(0, int(end_ms)))

    rows = db.all(
        f"""SELECT s.id AS segment_id, s.segment_index, s.start_ms, s.end_ms,
                   COALESCE(s.user_text, s.text) AS text,
                   COALESCE(s.user_speaker_label, s.speaker_label, '') AS speaker_label
            FROM transcript_segments s
            WHERE {' AND '.join(where)}
            ORDER BY s.segment_index""",
        tuple(args),
    )

    rendered: list[str] = []
    segments: list[dict] = []
    used = 0
    next_cursor: int | None = None
    for row in rows:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        speaker = str(row.get("speaker_label") or "").strip()
        if include_timestamps:
            prefix = f"[{_fmt_ms(row['start_ms'])}-{_fmt_ms(row['end_ms'])}]"
            if speaker:
                prefix += f" {speaker}:"
            line = f"{prefix} {text}"
        else:
            line = text
        cost = estimate_tokens(line) + 2
        if rendered and used + cost > budget:
            next_cursor = int(row["segment_index"])
            break
        if not rendered and cost > budget:
            approx_chars = max(1000, int(budget * 3.2))
            line = line[:approx_chars]
            cost = estimate_tokens(line)
            next_cursor = int(row["segment_index"]) + 1
        rendered.append(line)
        used += cost
        segments.append({
            "segment_id": row["segment_id"],
            "segment_index": row["segment_index"],
            "start_ms": row["start_ms"],
            "end_ms": row["end_ms"],
            "speaker_label": speaker,
            "text": text,
        })

    return {
        "episode": episode,
        "text": "\n".join(rendered),
        "segments": segments,
        "token_estimate": used,
        "context_budget_tokens": budget,
        "next_cursor": next_cursor,
        "truncated": next_cursor is not None,
    }


def transcript_search(
    db: Database,
    query: str,
    *,
    scope: str = "library",
    episode_id: int | None = None,
    collection_id: int | None = None,
    podcast_id: int | None = None,
    limit: int = 80,
    max_tokens: int = DEFAULT_CONTEXT_TOKENS,
) -> dict:
    ids = scope_episode_ids(
        db, scope,
        episode_id=episode_id,
        collection_id=collection_id,
        podcast_id=podcast_id,
    )
    hits = hybrid_search(
        db,
        str(query or "").strip(),
        episode_ids=ids,
        limit=max(1, min(200, int(limit or 80))),
    )
    budget = clamp_context_tokens(max_tokens)
    used = 0
    out: list[dict] = []
    lines: list[str] = []
    for hit in hits:
        speaker = str(hit.get("speaker_label") or "").strip()
        line = (
            f"[{hit['podcast_title']} / {hit['episode_title']} "
            f"@ {_fmt_ms(hit['start_ms'])}-{_fmt_ms(hit['end_ms'])}]"
            + (f" {speaker}:" if speaker else "")
            + f" {hit['text']}"
        )
        cost = estimate_tokens(line) + 2
        if lines and used + cost > budget:
            break
        used += cost
        lines.append(line)
        out.append(hit)
    return {
        "query": query,
        "scope": scope,
        "hits": out,
        "text": "\n".join(lines),
        "token_estimate": used,
        "context_budget_tokens": budget,
        "truncated": len(out) < len(hits),
    }


def episode_get(db: Database, episode_id: int) -> dict:
    ep = db.one(
        """SELECT e.*, p.title AS podcast_title
           FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.id=?""",
        (episode_id,),
    )
    if not ep:
        raise RuntimeError("Episode not found")
    ep["chapters"] = db.all(
        "SELECT id, start_ms, end_ms, title, summary FROM chapters WHERE episode_id=? ORDER BY start_ms",
        (episode_id,),
    )
    ep["entities"] = db.all(
        """SELECT DISTINCT en.id, en.type, en.canonical_name, en.description,
                  COALESCE(en.user_description, '') AS user_description
           FROM episode_entities ee JOIN entities en ON en.id=ee.entity_id
           WHERE ee.episode_id=? ORDER BY en.type, en.canonical_name""",
        (episode_id,),
    )
    return ep


def library_overview(db: Database, limit: int = 12) -> dict:
    return {
        "counts": db.one(
            """SELECT COUNT(*) AS episodes,
                      COUNT(DISTINCT podcast_id) AS podcasts,
                      SUM(CASE WHEN transcript_status='ready' THEN 1 ELSE 0 END) AS transcribed,
                      SUM(CASE WHEN completed=0 THEN 1 ELSE 0 END) AS unfinished
               FROM episodes"""
        ) or {},
        "recent_episodes": db.all(
            """SELECT e.id, e.title, e.duration_ms, e.playhead_ms, e.completed,
                      e.last_played_at, e.imported_at, e.summary, e.discipline,
                      p.id AS podcast_id, p.title AS podcast_title
               FROM episodes e JOIN podcasts p ON p.id=e.podcast_id
               ORDER BY COALESCE(e.last_played_at, e.imported_at) DESC
               LIMIT ?""",
            (max(1, min(50, int(limit or 12))),),
        ),
    }


def notes_list(db: Database, episode_id: int | None = None, entity_id: int | None = None,
               query: str = "", limit: int = 40) -> dict:
    where = ["1=1"]
    args: list[Any] = []
    if episode_id is not None:
        where.append("n.episode_id=?")
        args.append(int(episode_id))
    if entity_id is not None:
        where.append("n.entity_id=?")
        args.append(int(entity_id))
    if str(query or "").strip():
        where.append("n.body LIKE ?")
        args.append(f"%{str(query).strip()}%")
    args.append(max(1, min(200, int(limit or 40))))
    rows = db.all(
        f"""SELECT n.id, n.episode_id, n.segment_id, n.entity_id, n.body,
                   n.created_at, n.updated_at, e.title AS episode_title,
                   en.canonical_name AS entity_name
            FROM notes n
            LEFT JOIN episodes e ON e.id=n.episode_id
            LEFT JOIN entities en ON en.id=n.entity_id
            WHERE {' AND '.join(where)}
            ORDER BY n.updated_at DESC LIMIT ?""",
        args,
    )
    return {"notes": rows}


def bookmarks_list(db: Database, episode_id: int | None = None, limit: int = 40) -> dict:
    where = "WHERE b.episode_id=?" if episode_id is not None else ""
    args: list[Any] = [int(episode_id)] if episode_id is not None else []
    args.append(max(1, min(200, int(limit or 40))))
    rows = db.all(
        f"""SELECT b.id, b.episode_id, b.position_ms, b.label, b.created_at,
                   e.title AS episode_title, p.title AS podcast_title
            FROM bookmarks b
            JOIN episodes e ON e.id=b.episode_id
            JOIN podcasts p ON p.id=e.podcast_id
            {where}
            ORDER BY b.created_at DESC LIMIT ?""",
        args,
    )
    return {"bookmarks": rows}


def collections_list(db: Database, query: str = "", limit: int = 50) -> dict:
    where = "WHERE c.name LIKE ? OR c.description LIKE ?" if str(query or "").strip() else ""
    args: list[Any] = []
    if where:
        q = f"%{str(query).strip()}%"
        args.extend([q, q])
    args.append(max(1, min(200, int(limit or 50))))
    rows = db.all(
        f"""SELECT c.id, c.name, c.description, c.is_smart, c.created_at,
                   COUNT(ce.episode_id) AS episode_count
            FROM collections c
            LEFT JOIN collection_episodes ce ON ce.collection_id=c.id
            {where}
            GROUP BY c.id
            ORDER BY c.created_at DESC LIMIT ?""",
        args,
    )
    return {"collections": rows}


def entity_search(db: Database, query: str, entity_type: str | None = None, limit: int = 40) -> dict:
    q = str(query or "").strip()
    if not q:
        raise RuntimeError("Entity search query is required")
    where = ["(en.canonical_name LIKE ? OR en.description LIKE ? OR en.user_description LIKE ?)"]
    needle = f"%{q}%"
    args: list[Any] = [needle, needle, needle]
    if entity_type:
        where.append("en.type=?")
        args.append(str(entity_type).upper())
    args.append(max(1, min(200, int(limit or 40))))
    rows = db.all(
        f"""SELECT en.id, en.type, en.canonical_name, en.description,
                   COALESCE(en.user_description, '') AS user_description,
                   COUNT(DISTINCT ee.episode_id) AS episode_count,
                   COUNT(ee.segment_id) AS mention_count
            FROM entities en LEFT JOIN episode_entities ee ON ee.entity_id=en.id
            WHERE {' AND '.join(where)}
            GROUP BY en.id
            ORDER BY episode_count DESC, mention_count DESC, en.canonical_name
            LIMIT ?""",
        args,
    )
    return {"query": q, "entities": rows}


def entity_get(db: Database, entity_id: int) -> dict:
    row = db.one(
        "SELECT id, type, canonical_name, description, user_description, created_at FROM entities WHERE id=?",
        (int(entity_id),),
    )
    if not row:
        raise RuntimeError("Entity not found")
    row["mentions"] = db.all(
        """SELECT DISTINCT e.id AS episode_id, e.title AS episode_title, p.title AS podcast_title,
                  s.id AS segment_id, s.start_ms, s.end_ms,
                  COALESCE(s.user_text, s.text, '') AS text
           FROM episode_entities ee
           JOIN episodes e ON e.id=ee.episode_id
           JOIN podcasts p ON p.id=e.podcast_id
           LEFT JOIN transcript_segments s ON s.id=ee.segment_id
           WHERE ee.entity_id=? ORDER BY e.imported_at DESC, s.start_ms LIMIT 120""",
        (int(entity_id),),
    )
    row["relations"] = db.all(
        """SELECT r.relation, en.id, en.type, en.canonical_name
           FROM entity_relations r JOIN entities en ON en.id=r.target_entity_id
           WHERE r.source_entity_id=? ORDER BY r.relation, en.canonical_name LIMIT 100""",
        (int(entity_id),),
    )
    return row


def memory_list(db: Database, limit: int = 40) -> dict:
    return {
        "memories": db.all(
            "SELECT id, kind, content, created_at, updated_at FROM ai_memories ORDER BY updated_at DESC LIMIT ?",
            (max(1, min(200, int(limit or 40))),),
        )
    }


def memory_remember(db: Database, content: str, kind: str = "preference") -> dict:
    text = re.sub(r"\s+", " ", str(content or "")).strip()[:2000]
    if not text:
        raise RuntimeError("Memory content is required")
    kind = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(kind or "preference").lower()).strip("-")[:40] or "preference"
    db.execute(
        """INSERT INTO ai_memories(kind, content) VALUES(?, ?)
           ON CONFLICT(kind, content) DO UPDATE SET updated_at=CURRENT_TIMESTAMP""",
        (kind, text),
    )
    row = db.one("SELECT id, kind, content, created_at, updated_at FROM ai_memories WHERE kind=? AND content=?", (kind, text))
    return {"remembered": row}


def memory_forget(db: Database, memory_id: int | None = None, content: str = "") -> dict:
    if memory_id is not None:
        row = db.one("SELECT id, kind, content FROM ai_memories WHERE id=?", (int(memory_id),))
        if not row:
            return {"forgotten": False, "reason": "Memory not found"}
        db.execute("DELETE FROM ai_memories WHERE id=?", (int(memory_id),))
        return {"forgotten": True, "memory": row}
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        raise RuntimeError("memory_id or content is required")
    rows = db.all("SELECT id, kind, content FROM ai_memories WHERE content LIKE ?", (f"%{text}%",))
    for row in rows:
        db.execute("DELETE FROM ai_memories WHERE id=?", (row["id"],))
    return {"forgotten": bool(rows), "memories": rows}



TOOLS = [
    {
        "name": "audiocodex.transcript.read",
        "description": (
            "Read timestamped transcript segments for one episode. Default context budget "
            "is 1,000,000 estimated tokens; use next_cursor to page extremely large transcripts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "episode_id": {"type": "integer"},
                "cursor": {"type": "integer", "default": 0},
                "max_tokens": {"type": "integer", "default": DEFAULT_CONTEXT_TOKENS},
                "start_ms": {"type": ["integer", "null"]},
                "end_ms": {"type": ["integer", "null"]},
                "include_timestamps": {"type": "boolean", "default": True},
            },
            "required": ["episode_id"],
        },
    },
    {
        "name": "audiocodex.transcript.search",
        "description": "Search transcript segments in an episode, podcast, collection, or library.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {"type": "string", "enum": ["episode", "podcast", "collection", "library"]},
                "episode_id": {"type": ["integer", "null"]},
                "podcast_id": {"type": ["integer", "null"]},
                "collection_id": {"type": ["integer", "null"]},
                "limit": {"type": "integer", "default": 80},
                "max_tokens": {"type": "integer", "default": DEFAULT_CONTEXT_TOKENS},
            },
            "required": ["query"],
        },
    },
    {
        "name": "audiocodex.episode.get",
        "description": "Read episode metadata, existing chapters, and extracted entities.",
        "inputSchema": {
            "type": "object", "properties": {"episode_id": {"type": "integer"}}, "required": ["episode_id"]
        },
    },
    {
        "name": "audiocodex.library.overview",
        "description": "Read a compact overview of the user's local Audio Codex library and recently used episodes.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 12}}},
    },
    {
        "name": "audiocodex.notes.list",
        "description": "Read local notes, optionally scoped to an episode/entity or filtered by text.",
        "inputSchema": {"type": "object", "properties": {
            "episode_id": {"type": ["integer", "null"]}, "entity_id": {"type": ["integer", "null"]},
            "query": {"type": "string"}, "limit": {"type": "integer", "default": 40}
        }},
    },
    {
        "name": "audiocodex.bookmarks.list",
        "description": "Read local playback bookmarks, optionally for one episode.",
        "inputSchema": {"type": "object", "properties": {
            "episode_id": {"type": ["integer", "null"]}, "limit": {"type": "integer", "default": 40}
        }},
    },
    {
        "name": "audiocodex.collections.list",
        "description": "Read the user's collections and their episode counts.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer", "default": 50}
        }},
    },
    {
        "name": "audiocodex.entity.search",
        "description": "Search Audio Codex knowledge entities such as people, topics, works, places, events and eras.",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"}, "entity_type": {"type": ["string", "null"]},
            "limit": {"type": "integer", "default": 40}
        }, "required": ["query"]},
    },
    {
        "name": "audiocodex.entity.get",
        "description": "Read one knowledge entity with its relations and transcript mentions.",
        "inputSchema": {"type": "object", "properties": {"entity_id": {"type": "integer"}}, "required": ["entity_id"]},
    },
    {
        "name": "audiocodex.memory.list",
        "description": "Read explicit, user-approved Audio Codex Intelligence memories stored locally.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 40}}},
    },
]


TOOLS.extend([
    {
        "name": "audiocodex.listening.rewind",
        "description": "Read the exact transcript window the user just heard before the current playhead. Use this for 'just now', 'what did they say', or rewind questions instead of guessing from general episode context.",
        "inputSchema": {"type": "object", "properties": {
            "episode_id": {"type": "integer"}, "playhead_ms": {"type": "integer"},
            "window_ms": {"type": "integer", "default": 120000, "minimum": 15000, "maximum": 900000},
            "session_id": {"type": ["string", "null"]}
        }, "required": ["episode_id", "playhead_ms"]},
    },
    {
        "name": "audiocodex.listening.session",
        "description": "Read the latest local listening-session ranges for an episode. It represents what was actually listened to, including seeks, without including skipped gaps.",
        "inputSchema": {"type": "object", "properties": {"episode_id": {"type": ["integer", "null"]}}},
    },
    {
        "name": "audiocodex.listening.recaps",
        "description": "Read recent sourced Listening Recaps and whether their transcript provenance is still current.",
        "inputSchema": {"type": "object", "properties": {
            "episode_id": {"type": ["integer", "null"]}, "limit": {"type": "integer", "default": 20}
        }},
    },
    {
        "name": "audiocodex.intelligence.schema",
        "description": "Read the Audio Codex entity/intent schema that defines first-class app objects and actions.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "audiocodex.provenance.get",
        "description": "Read Reference Transcript + Audio provenance behind generated Audio Codex knowledge, including whether transcript passages or the local source-audio fingerprint have changed.",
        "inputSchema": {"type": "object", "properties": {
            "object_type": {"type": "string"}, "object_id": {"type": ["string", "integer"]},
            "claim_key": {"type": ["string", "null"]},
            "full_audio_verify": {"type": "boolean", "default": False}
        }, "required": ["object_type", "object_id"]},
    },
    {
        "name": "audiocodex.watch.list",
        "description": "Read user-created Knowledge Watches and their unread event counts. Use only when relevant to monitoring or follow-up requests.",
        "inputSchema": {"type": "object", "properties": {"include_events": {"type": "boolean", "default": False}, "limit": {"type": "integer", "default": 50}}},
    },
    {
        "name": "audiocodex.watch.events",
        "description": "Read recent Knowledge Watch matches, optionally only unread ones.",
        "inputSchema": {"type": "object", "properties": {"unacknowledged": {"type": "boolean", "default": True}, "limit": {"type": "integer", "default": 50}}},
    },
])


def call_tool(db: Database, name: str, arguments: dict | None = None) -> dict:
    a = arguments or {}
    if name == "audiocodex.transcript.read":
        return transcript_read(
            db, int(a["episode_id"]), cursor=int(a.get("cursor") or 0),
            max_tokens=clamp_context_tokens(a.get("max_tokens")), start_ms=a.get("start_ms"),
            end_ms=a.get("end_ms"), include_timestamps=bool(a.get("include_timestamps", True)),
        )
    if name == "audiocodex.transcript.search":
        return transcript_search(
            db, str(a.get("query") or ""), scope=str(a.get("scope") or "library"),
            episode_id=a.get("episode_id"), collection_id=a.get("collection_id"),
            podcast_id=a.get("podcast_id"), limit=int(a.get("limit") or 80),
            max_tokens=clamp_context_tokens(a.get("max_tokens")),
        )
    if name == "audiocodex.episode.get":
        return episode_get(db, int(a["episode_id"]))
    if name == "audiocodex.library.overview":
        return library_overview(db, int(a.get("limit") or 12))
    if name == "audiocodex.notes.list":
        return notes_list(db, a.get("episode_id"), a.get("entity_id"), str(a.get("query") or ""), int(a.get("limit") or 40))
    if name == "audiocodex.bookmarks.list":
        return bookmarks_list(db, a.get("episode_id"), int(a.get("limit") or 40))
    if name == "audiocodex.collections.list":
        return collections_list(db, str(a.get("query") or ""), int(a.get("limit") or 50))
    if name == "audiocodex.entity.search":
        return entity_search(db, str(a.get("query") or ""), a.get("entity_type"), int(a.get("limit") or 40))
    if name == "audiocodex.entity.get":
        return entity_get(db, int(a["entity_id"]))
    if name == "audiocodex.memory.list":
        return memory_list(db, int(a.get("limit") or 40))
    if name == "audiocodex.listening.rewind":
        from .r12 import listening_rewind
        return listening_rewind(db, int(a["episode_id"]), int(a.get("playhead_ms") or 0), int(a.get("window_ms") or 120000), session_id=a.get("session_id"))
    if name == "audiocodex.listening.session":
        from .r12 import current_listening_session
        return {"session": current_listening_session(db, a.get("episode_id"))}
    if name == "audiocodex.listening.recaps":
        from .r12 import list_listening_recaps
        return list_listening_recaps(db, episode_id=a.get("episode_id"), limit=int(a.get("limit") or 20))
    if name == "audiocodex.intelligence.schema":
        from .r12 import schema_manifest
        return schema_manifest()
    if name == "audiocodex.provenance.get":
        from .r12 import provenance_get
        return {"records": provenance_get(
            db, str(a.get("object_type") or ""), a.get("object_id"),
            claim_key=a.get("claim_key"), full_audio_verify=bool(a.get("full_audio_verify", False))
        )}
    if name == "audiocodex.watch.list":
        from .r12 import list_watches
        return list_watches(db, include_events=bool(a.get("include_events", False)), limit=int(a.get("limit") or 50))
    if name == "audiocodex.watch.events":
        from .r12 import list_watch_events
        return list_watch_events(db, unacknowledged=bool(a.get("unacknowledged", True)), limit=int(a.get("limit") or 50))
    raise RuntimeError(f"Unknown MCP tool: {name}")


def jsonrpc(db: Database, payload: dict) -> dict | None:
    request_id = payload.get("id")
    method = str(payload.get("method") or "")
    params = payload.get("params") or {}
    try:
        if method == "initialize":
            result = {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "Audio Codex MCP", "version": "1.2.1"},
                "instructions": (
                    "Audio Codex exposes local transcripts, library entities, notes, bookmarks, collections, "
                    "explicit local AI memory, and optional DeepSeek current-web retrieval. Use local tools first; memory mutations require app confirmation."
                ),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            structured = call_tool(db, str(params.get("name") or ""), params.get("arguments") or {})
            result = {
                "content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False)}],
                "structuredContent": structured,
                "isError": False,
            }
        elif method.startswith("notifications/"):
            return None
        else:
            raise RuntimeError(f"Unsupported MCP method: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}
