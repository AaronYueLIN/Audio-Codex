from __future__ import annotations

import json
import re
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from .ai import AISettings, ChatProvider
from .db import Database
from .rag import _prepare_history, _prepare_images


_MAX_HISTORY_MESSAGES = 24
_MAX_SELECTION_CHARS = 8000


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compact_text(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def normalize_runtime_context(raw: dict | None) -> dict:
    """Whitelist useful UI state; arbitrary DOM/application state never reaches the model."""
    value = raw if isinstance(raw, dict) else {}
    allowed_views = {"home", "episodes", "collections", "jobs", "entities", "timeline", "notes", "intelligence", "system"}
    view = str(value.get("view") or "home")
    if view not in allowed_views:
        view = "home"

    def _int(*names: str) -> int | None:
        for name in names:
            try:
                v = value.get(name)
                if v is not None and v != "":
                    return max(0, int(v))
            except Exception:
                continue
        return None

    def _int_from(v: Any) -> int | None:
        try:
            return max(0, int(v)) if v is not None and v != "" else None
        except Exception:
            return None

    player_state = str(value.get("player_state") or "")[:32]
    if player_state not in {"", "playing", "paused", "ended", "loading"}:
        player_state = ""

    visible_items: list[dict[str, Any]] = []
    allowed_item_types = {"episode", "entity", "collection", "note", "bookmark", "transcript_segment", "watch"}
    for index, item in enumerate(value.get("visible_items") or []):
        if not isinstance(item, dict) or len(visible_items) >= 24:
            continue
        kind = str(item.get("type") or "").strip().lower()
        if kind not in allowed_item_types:
            continue
        try:
            item_id = int(item.get("id"))
        except Exception:
            continue
        if item_id < 1:
            continue
        try:
            position = max(1, min(1000, int(item.get("index") or index + 1)))
        except Exception:
            position = index + 1
        visible_items.append({"type": kind, "id": item_id, "index": position})

    selection_raw = value.get("selection") if isinstance(value.get("selection"), dict) else {}
    segment_ids: list[int] = []
    for raw_id in selection_raw.get("segment_ids") or []:
        try:
            sid = int(raw_id)
        except Exception:
            continue
        if sid > 0 and sid not in segment_ids:
            segment_ids.append(sid)
        if len(segment_ids) >= 40:
            break
    selection_start = _int_from(selection_raw.get("start_ms"))
    selection_end = _int_from(selection_raw.get("end_ms"))
    if selection_start is not None and selection_end is not None and selection_end < selection_start:
        selection_start, selection_end = selection_end, selection_start
    selection = {
        "segment_ids": segment_ids,
        "start_ms": selection_start,
        "end_ms": selection_end,
    } if segment_ids or selection_start is not None or selection_end is not None else None
    rewind_window_ms = _int_from(value.get("rewind_window_ms"))
    if rewind_window_ms is not None:
        rewind_window_ms = max(15_000, min(900_000, rewind_window_ms))

    return {
        "view": view,
        "selected_episode_id": _int("selected_episode_id", "episode_id"),
        "selected_entity_id": _int("selected_entity_id", "entity_id"),
        "selected_collection_id": _int("selected_collection_id", "collection_id"),
        "playhead_ms": _int("playhead_ms", "playback_ms"),
        "player_state": player_state,
        "visible_title": _compact_text(value.get("visible_title"), 300),
        "selected_text": _compact_text(value.get("selected_text"), _MAX_SELECTION_CHARS),
        "active_caption": _compact_text(value.get("active_caption"), 2000),
        "search_query": _compact_text(value.get("search_query"), 1000),
        "visible_items": visible_items,
        "selection": selection,
        "rewind_window_ms": rewind_window_ms,
        "listening_session_id": _compact_text(value.get("listening_session_id"), 80),
    }


def _library_counts(db: Database) -> dict:
    row = db.one(
        """SELECT COUNT(*) AS episodes,
                  COUNT(DISTINCT podcast_id) AS podcasts,
                  SUM(CASE WHEN transcript_status='ready' THEN 1 ELSE 0 END) AS transcribed,
                  SUM(CASE WHEN completed=0 THEN 1 ELSE 0 END) AS unfinished
           FROM episodes"""
    ) or {}
    return {k: int(row.get(k) or 0) for k in ("episodes", "podcasts", "transcribed", "unfinished")}


def _visible_objects(db: Database, items: list[dict], *, personal_context: bool) -> list[dict]:
    """Resolve viewport object IDs against local storage; DOM-provided titles are never trusted."""
    resolved: list[dict] = []
    for item in items[:24]:
        kind, item_id = str(item.get("type") or ""), int(item.get("id") or 0)
        row: dict | None = None
        if kind == "episode":
            row = db.one(
                """SELECT e.id, e.title, p.title AS podcast_title, e.playhead_ms, e.completed
                   FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.id=?""", (item_id,)
            )
        elif kind == "entity":
            row = db.one("SELECT id, type, canonical_name FROM entities WHERE id=?", (item_id,))
        elif kind == "collection":
            row = db.one("SELECT id, name, is_smart FROM collections WHERE id=?", (item_id,))
        elif kind == "transcript_segment":
            row = db.one(
                """SELECT s.id, s.episode_id, s.start_ms, s.end_ms,
                          COALESCE(s.user_speaker_label, s.speaker_label, '') AS speaker_label,
                          e.title AS episode_title
                   FROM transcript_segments s JOIN episodes e ON e.id=s.episode_id WHERE s.id=?""",
                (item_id,),
            )
        elif personal_context and kind == "watch":
            row = db.one("SELECT id, name, query, mode, scope, active FROM knowledge_watches WHERE id=?", (item_id,))
        elif personal_context and kind == "note":
            row = db.one(
                """SELECT n.id, n.episode_id, n.entity_id, e.title AS episode_title,
                          en.canonical_name AS entity_name
                   FROM notes n LEFT JOIN episodes e ON e.id=n.episode_id
                   LEFT JOIN entities en ON en.id=n.entity_id WHERE n.id=?""", (item_id,)
            )
        elif personal_context and kind == "bookmark":
            row = db.one(
                """SELECT b.id, b.episode_id, b.position_ms, b.label, e.title AS episode_title
                   FROM bookmarks b JOIN episodes e ON e.id=b.episode_id WHERE b.id=?""", (item_id,)
            )
        if row:
            resolved.append({"type": kind, "index": int(item.get("index") or 1), **row})
    return resolved


def context_snapshot(db: Database, raw_context: dict | None = None, *, personal_context: bool = True) -> dict:
    ui = normalize_runtime_context(raw_context)
    episode_id = ui.get("selected_episode_id")
    entity_id = ui.get("selected_entity_id")
    collection_id = ui.get("selected_collection_id")

    snapshot: dict[str, Any] = {
        "captured_at": _utc_now(),
        "ui": ui,
        "library": _library_counts(db),
    }

    # Personal context is local-first, bounded and minimised. Notes, bookmarks and explicit
    # memories are not injected wholesale; the Agent can fetch only the relevant rows via tools.
    if personal_context:
        snapshot["recent_activity"] = db.all(
            """SELECT e.id, e.title, e.playhead_ms, e.completed, e.last_played_at,
                      p.title AS podcast_title
               FROM episodes e JOIN podcasts p ON p.id=e.podcast_id
               WHERE e.last_played_at IS NOT NULL OR e.playhead_ms > 0
               ORDER BY COALESCE(e.last_played_at, e.imported_at) DESC LIMIT 6"""
        )

    visible = _visible_objects(db, ui.get("visible_items") or [], personal_context=personal_context)
    if visible:
        snapshot["visible_objects"] = visible

    if episode_id:
        snapshot["current_episode"] = db.one(
            """SELECT e.id, e.title, e.summary, e.discipline, e.duration_ms, e.playhead_ms,
                      e.completed, e.transcript_status, e.analysis_status,
                      p.id AS podcast_id, p.title AS podcast_title
               FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.id=?""",
            (episode_id,),
        )
        center = int(ui.get("playhead_ms") or 0)
        snapshot["current_passage"] = db.all(
            """SELECT id AS segment_id, segment_index, start_ms, end_ms,
                      COALESCE(user_speaker_label, speaker_label, '') AS speaker_label,
                      COALESCE(user_text, text) AS text
               FROM transcript_segments
               WHERE episode_id=? AND end_ms>=? AND start_ms<=?
               ORDER BY start_ms LIMIT 40""",
            (episode_id, max(0, center - 90_000), center + 90_000),
        )

    selection = ui.get("selection") or {}
    selected_segment_ids = [int(x) for x in selection.get("segment_ids") or [] if int(x) > 0]
    selected_rows: list[dict] = []
    if selected_segment_ids:
        placeholders = ",".join("?" for _ in selected_segment_ids)
        args: list[Any] = list(selected_segment_ids)
        where = f"s.id IN ({placeholders})"
        if episode_id:
            where += " AND s.episode_id=?"
            args.append(int(episode_id))
        selected_rows = db.all(
            f"""SELECT s.id AS segment_id, s.episode_id, s.segment_index, s.start_ms, s.end_ms,
                       COALESCE(s.user_speaker_label, s.speaker_label, '') AS speaker_label,
                       COALESCE(s.user_text, s.text) AS text
                FROM transcript_segments s WHERE {where} ORDER BY s.start_ms""",
            args,
        )
    elif episode_id and (selection.get("start_ms") is not None or selection.get("end_ms") is not None):
        sel_start = max(0, int(selection.get("start_ms") or 0))
        sel_end = max(sel_start, int(selection.get("end_ms") if selection.get("end_ms") is not None else sel_start))
        selected_rows = db.all(
            """SELECT s.id AS segment_id, s.episode_id, s.segment_index, s.start_ms, s.end_ms,
                      COALESCE(s.user_speaker_label, s.speaker_label, '') AS speaker_label,
                      COALESCE(s.user_text, s.text) AS text
               FROM transcript_segments s
               WHERE s.episode_id=? AND s.end_ms>=? AND s.start_ms<=?
               ORDER BY s.start_ms LIMIT 80""",
            (int(episode_id), sel_start, sel_end),
        )
    if selected_rows:
        snapshot["selected_transcript_segments"] = selected_rows
        try:
            from .r12 import transcript_source_hash
            snapshot["selected_passage_reference"] = {
                "episode_id": int(selected_rows[0]["episode_id"]),
                "segment_ids": [int(r["segment_id"]) for r in selected_rows],
                "start_ms": min(int(r.get("start_ms") or 0) for r in selected_rows),
                "end_ms": max(int(r.get("end_ms") or 0) for r in selected_rows),
                "source_hash": transcript_source_hash(selected_rows),
            }
        except Exception:
            pass

    if episode_id:
        try:
            from .r12 import current_listening_session
            requested_sid = str(ui.get("listening_session_id") or "").strip()
            session = None
            if requested_sid:
                row = db.one("SELECT * FROM listening_sessions WHERE id=? AND episode_id=?", (requested_sid, int(episode_id)))
                if row:
                    try:
                        row["ranges"] = json.loads(row.pop("ranges_json") or "[]")
                    except Exception:
                        row["ranges"] = []
                    session = row
            if not session:
                session = current_listening_session(db, int(episode_id))
            if session:
                snapshot["listening_session"] = session
        except Exception:
            pass

    if entity_id:
        snapshot["current_entity"] = db.one(
            """SELECT id, type, canonical_name, description,
                      COALESCE(user_description, '') AS user_description
               FROM entities WHERE id=?""",
            (entity_id,),
        )
    if collection_id:
        snapshot["current_collection"] = db.one(
            "SELECT id, name, description, is_smart FROM collections WHERE id=?",
            (collection_id,),
        )
    return snapshot


def _conversation(db: Database, conversation_id: str | None, message: str) -> str:
    cid = str(conversation_id or "").strip()
    if cid and db.one("SELECT id FROM ai_conversations WHERE id=?", (cid,)):
        return cid
    cid = uuid.uuid4().hex
    title = _compact_text(message, 100) or "Conversation"
    db.execute("INSERT INTO ai_conversations(id, title) VALUES(?, ?)", (cid, title))
    return cid


def conversation_messages(db: Database, conversation_id: str, limit: int = 100) -> dict:
    row = db.one("SELECT * FROM ai_conversations WHERE id=?", (conversation_id,))
    if not row:
        return {"id": conversation_id, "messages": []}
    rows = db.all(
        """SELECT id, role, content, metadata_json, created_at
           FROM ai_messages WHERE conversation_id=? ORDER BY id DESC LIMIT ?""",
        (conversation_id, max(1, min(500, int(limit)))),
    )
    rows.reverse()
    for item in rows:
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except Exception:
            item["metadata"] = {}
    row["messages"] = rows
    return row


def delete_conversation(db: Database, conversation_id: str) -> bool:
    if not db.one("SELECT id FROM ai_conversations WHERE id=?", (conversation_id,)):
        return False
    db.execute("DELETE FROM ai_conversations WHERE id=?", (conversation_id,))
    return True


def _history(db: Database, conversation_id: str) -> list[dict]:
    rows = db.all(
        """SELECT role, content FROM ai_messages
           WHERE conversation_id=? AND role IN ('user','assistant')
           ORDER BY id DESC LIMIT ?""",
        (conversation_id, _MAX_HISTORY_MESSAGES),
    )
    rows.reverse()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def _save_message(db: Database, conversation_id: str, role: str, content: str,
                  metadata: dict | None = None) -> int:
    message_id = db.execute(
        """INSERT INTO ai_messages(conversation_id, role, content, metadata_json)
           VALUES(?, ?, ?, ?)""",
        (conversation_id, role, content, json.dumps(metadata or {}, ensure_ascii=False)),
    )
    db.execute("UPDATE ai_conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?", (conversation_id,))
    return int(message_id)


def _safe_context_metadata(context: dict) -> dict:
    # Conversation metadata should not retain selected free-form text or live caption contents.
    # R12 context also contains structured values such as visible_items (list) and nested
    # dictionaries. Never test those values for membership in a set: list/dict are unhashable.
    def _present(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, dict, set)):
            return bool(value)
        return True

    return {
        k: v for k, v in context.items()
        if k not in {"selected_text", "active_caption"} and _present(v)
    }


def _routing_profile(settings: AISettings, message: str, context: dict, *, has_images: bool = False) -> dict:
    """Choose a Dynamic Intelligence Profile and a strict per-turn tool capability set."""
    text = str(message or "").strip().lower()
    current_markers = (
        "最新", "今天", "现在", "目前", "刚发布", "查一下", "联网", "current", "latest", "today", "right now", "web", "online",
    )
    research_markers = (
        "深入", "全面", "综合", "比较", "对比", "研究", "评估", "规划", "战略", "论证", "跨播客", "跨节目",
        "deep research", "research", "synthesize", "compare", "strategy", "comprehensive", "across my library",
    )
    listen_markers = (
        "刚才", "刚刚", "上一句", "上一段", "我刚听", "回放", "回顾", "听了什么", "听到什么", "总结刚才",
        "just now", "just heard", "rewind", "what did they say", "what did i hear", "listening recap", "recap what i heard",
    )
    organize_markers = (
        "笔记", "合集", "收藏", "记住", "watch", "监控", "提醒我", "以后再提", "note", "collection", "remember", "organize",
    )
    act_markers = (
        "打开", "跳到", "播放", "书签", "保存", "转录", "字幕", "分析", "标注", "重命名",
        "open ", "play ", "bookmark", "save ", "transcribe", "caption", "analyse", "analyze", "annotate", "rename", "verify", "验证", "核验",
    )

    local_core = {
        "audiocodex.library.overview", "audiocodex.episode.get", "audiocodex.transcript.search",
        "audiocodex.transcript.read", "audiocodex.entity.search", "audiocodex.entity.get",
        "audiocodex.collections.list", "audiocodex.provenance.get", "audiocodex.intelligence.schema",
    }
    navigation_actions = {
        "audiocodex.ui.open_episode", "audiocodex.ui.play_audio", "audiocodex.ui.open_entity", "audiocodex.ui.open_collection",
        "audiocodex.ui.open_view", "audiocodex.ui.search_library",
    }
    listen_tools = local_core | navigation_actions | {
        "audiocodex.listening.rewind", "audiocodex.listening.session", "audiocodex.listening.recaps",
        "audiocodex.ui.create_listening_recap", "audiocodex.ui.create_bookmark", "audiocodex.ui.create_note",
    }
    personal_reads = {
        "audiocodex.notes.list", "audiocodex.bookmarks.list", "audiocodex.memory.list",
        "audiocodex.watch.list", "audiocodex.watch.events",
    }
    organize_actions = {
        "audiocodex.ui.create_note", "audiocodex.ui.create_bookmark", "audiocodex.ui.create_collection",
        "audiocodex.ui.add_to_collection", "audiocodex.ui.update_collection", "audiocodex.ui.update_entity_annotation",
        "audiocodex.ui.remember_memory", "audiocodex.ui.forget_memory",
        "audiocodex.ui.create_knowledge_watch", "audiocodex.ui.set_knowledge_watch",
        "audiocodex.ui.acknowledge_watch_event",
    }
    execution_actions = navigation_actions | organize_actions | {
        "audiocodex.ui.transcribe_episode", "audiocodex.ui.analyze_episode", "audiocodex.ui.generate_captions",
        "audiocodex.ui.create_listening_recap",
    }
    all_local = local_core | listen_tools | personal_reads | execution_actions

    has_current = any(m in text for m in current_markers)
    base = {
        "model": settings.chat_model or "deepseek-flash",
        "thinking": settings.thinking, "web": False, "personal": settings.personal_context,
        "actions": True, "tools": sorted(local_core | navigation_actions),
    }
    if not settings.adaptive_routing:
        return {
            **base, "name": "configured", "model": settings.vision_model if has_images else base["model"],
            "web": False, "tools": sorted(all_local),
            "instruction": "Use the configured general-purpose profile, but stay inside the exposed capability set.",
        }
    if has_images:
        tools = local_core | navigation_actions | execution_actions
        if settings.personal_context:
            tools |= personal_reads
        return {
            **base, "name": "vision", "model": settings.vision_model or "deepseek-flash",
            "web": False, "tools": sorted(tools),
            "instruction": "Understand the supplied image first, resolve visible app entities, then use only tools needed by the visual task.",
        }
    if any(m in text for m in listen_markers) or context.get("rewind_window_ms"):
        return {
            **base, "name": "listen", "thinking": False, "web": False, "personal": False,
            "tools": sorted(listen_tools),
            "instruction": "Prioritize the current listening session and listening.rewind. Respect actually-heard ranges and never summarize skipped audio.",
        }
    if len(text) > 600 or any(m in text for m in research_markers) or has_current:
        tools = local_core | navigation_actions | {"audiocodex.listening.rewind"}
        if settings.personal_context:
            tools |= personal_reads
        return {
            **base, "name": "research", "model": settings.pro_model or base["model"], "thinking": True,
            "web": False, "tools": sorted(tools),
            "instruction": "Synthesize carefully across sourced local evidence. If the request requires current public information, state that verified current-web retrieval is unavailable in the present DeepSeek API compatibility mode instead of implying that you browsed.",
        }
    if any(m in text for m in organize_markers):
        tools = local_core | navigation_actions | organize_actions
        if settings.personal_context:
            tools |= personal_reads
        return {
            **base, "name": "organize", "thinking": False, "web": False,
            "tools": sorted(tools),
            "instruction": "Focus on local notes, collections, explicit memory and Knowledge Watches. Every mutation remains a confirmable proposal.",
        }
    if len(text) <= 240 and any(m in text for m in act_markers):
        tools = local_core | execution_actions
        if settings.personal_context:
            tools |= personal_reads
        return {
            **base, "name": "act", "thinking": False, "web": False,
            "tools": sorted(tools),
            "instruction": "Resolve the target entity precisely and prepare only the smallest confirmable app action that completes the request.",
        }
    tools = local_core | navigation_actions | {"audiocodex.listening.rewind"}
    if settings.personal_context:
        tools |= personal_reads
    return {
        **base, "name": "ask", "web": False, "tools": sorted(tools),
        "instruction": "Answer from the current entity and local archive context. Use provenance when present and avoid unnecessary tools.",
    }


def answer(
    db: Database,
    message: str,
    *,
    conversation_id: str | None = None,
    runtime_context: dict | None = None,
    legacy_scope: str | None = None,
    episode_id: int | None = None,
    collection_id: int | None = None,
    podcast_id: int | None = None,
    passage_ms: int | None = None,
    images: list[dict] | None = None,
    client_history: list[dict] | None = None,
    event_callback: Callable[[dict], None] | None = None,
) -> dict:
    settings = AISettings.from_db(db)
    image_parts, image_meta = _prepare_images(images, settings.vision_detail)
    has_images = bool(image_parts)
    message = str(message or "").strip()
    if not message and has_images:
        message = "What should I know about the attached image in my current Audio Codex context?"
    if not message:
        raise ValueError("Ask a question or attach an image.")
    if has_images and not settings.vision_model:
        raise ValueError("A vision model is required before image questions can be sent.")
    provider_settings = replace(settings)
    # Runtime context is normalized before profile selection so “just now” UI signals can route to Listen.
    preliminary_context = normalize_runtime_context(runtime_context or {})
    profile = _routing_profile(settings, message, preliminary_context, has_images=has_images)
    model_override = profile.get("model")
    thinking = bool(profile.get("thinking"))
    route = str(profile.get("name") or "ask")
    if route == "research" and provider_settings.reasoning_effort in {"high", "xhigh", "max"}:
        provider_settings.reasoning_effort = "max"
    provider = ChatProvider(provider_settings, db)
    status = provider.status()
    if not status["available"]:
        return {
            "answer": "Audio Codex Intelligence requires a DeepSeek API Key. Open Settings → AI / MCP and connect DeepSeek. " + status.get("detail", ""),
            "sources": [], "actions": [], "mode": "unavailable", "provider": status,
        }

    merged_context = dict(runtime_context or {})
    if episode_id is not None:
        merged_context["selected_episode_id"] = episode_id
    if collection_id is not None:
        merged_context["selected_collection_id"] = collection_id
    if passage_ms is not None:
        merged_context["playhead_ms"] = passage_ms
    if legacy_scope == "episode" and episode_id:
        merged_context["view"] = "episodes"

    normalized = normalize_runtime_context(merged_context)
    snapshot = context_snapshot(db, normalized, personal_context=settings.personal_context)

    cid: str | None = None
    history: list[dict] = []
    if settings.conversation_memory:
        cid = _conversation(db, conversation_id, message)
        history = _history(db, cid)
    else:
        history = _prepare_history(client_history)

    system = f"""You are Audio Codex Intelligence, the central intelligence layer of the application — not a detached chatbot.
You understand the user's current app context, reason over the local podcast archive, and use Audio Codex actions to help complete tasks.

PRODUCT CONTRACT
1. APP CONTEXT is the user's current on-screen context. Resolve phrases such as “this episode”, “here”, “what I'm listening to”, “save this”, “that person”, or “this collection” from it before asking follow-up questions.
2. Audio Codex objects are first-class entities: episodes, podcasts, transcript passages, knowledge entities, collections, notes and bookmarks. Use local tools to resolve them instead of guessing.
3. Prefer doing useful work over explaining menu steps. For actions that change UI or user data, call the audiocodex.ui.* proposal tool. The UI will require explicit user confirmation. Never claim a proposed action already happened.
4. Personal context is user-controlled. Read notes, bookmarks or explicit memories only through the available local tools and only when they are relevant. Explicit memory actions may only be proposed when the user clearly says to remember or forget a stable preference/instruction. Never infer/store sensitive personal facts.
5. For archive facts, use transcript tools. When transcript rows include citation_key, cite them exactly as [T1], [T2], etc. Never fabricate citations.
6. Prefer local archive context for archive questions. The current DeepSeek compatibility mode does not expose verified server-side web search to Audio Codex; when genuinely current public information is required, say that it cannot be verified in this turn rather than implying that you browsed.
7. There are no destructive AI delete actions. Never pretend deletion occurred. Do not bypass user confirmation for app mutations.
8. Keep responses natural, concise and context-aware. Do not narrate hidden reasoning or tool mechanics.
9. If the current context makes an ambiguous reference obvious, use it. Ask only when a consequential ambiguity remains.
10. The active Dynamic Intelligence Profile is {route}. Profile instruction: {profile.get('instruction')}. Your current model is {model_override or status.get('model')}.
11. For “just now / what did I hear / recap” requests, use the listening session or audiocodex.listening.rewind so skipped audio is never treated as heard.
12. Transcript selections are first-class passage entities. If selected_transcript_segments exist, resolve “this / this part / these words” to those segment IDs.
13. Knowledge Watches are user-created future conditions. Creating, pausing or resuming one is a confirmable app action; never claim monitoring exists before confirmation.
14. Generated summaries and recaps can have Reference Provenance. When provenance is available, treat a stale source hash as a reason to re-verify rather than silently trusting old generated text.
15. Visual input: {'one or more images are attached to the current user message. Treat them as direct visual evidence; you may read screenshot text, understand charts, and use what you see to decide which local tools to call.' if has_images else 'no image is attached. Never imply that you can see content that was not supplied.'}
16. Attached image bytes are transient request input. Do not ask to store them as memory and do not claim Audio Codex persisted them.
17. Audio Codex uses a stable entity/intent schema. Think in entities and intents, not DOM coordinates or menu instructions.
"""
    context_text = json.dumps(snapshot, ensure_ascii=False, default=str, separators=(",", ":"))
    prompt_text = f"APP CONTEXT (local snapshot; include only what is needed in your reasoning):\n{context_text}\n\nUSER REQUEST:\n{message}"
    user: Any = ([{"type": "text", "text": prompt_text}, *image_parts] if has_images else prompt_text)

    if cid:
        _save_message(db, cid, "user", message, {
            "context": _safe_context_metadata(normalized),
            "image_count": len(image_meta),
        })
    try:
        generated = provider.chat(
            system,
            user,
            temperature=0.2,
            max_output_tokens=min(settings.chat_max_tokens, 32768),
            history=history,
            model_override=model_override,
            thinking=thinking,
            use_tools=True,
            allow_client_actions=bool(profile.get("actions", True)),
            include_personal_context=bool(settings.personal_context and profile.get("personal", True)),
            allow_web_search=bool(profile.get("web", False)),
            allowed_tool_names=set(profile.get("tools") or []),
            event_callback=event_callback,
        )
    except Exception as exc:
        return {
            "answer": f"Intelligence request failed: {exc}", "conversation_id": cid,
            "sources": [], "actions": [], "mode": "error", "provider": status,
            "intelligence": {"route": route, "model": model_override or status.get("model"),
                             "vision_used": has_images, "image_count": len(image_meta)},
        }

    run = provider.last_run or {}
    tool_results = run.get("tool_results") or []
    actions = run.get("actions") or []
    sources = run.get("tool_sources") or []
    metadata = {
        "tools_used": run.get("tools_used") or [], "actions": actions,
        "model": run.get("model") or model_override or status.get("model"),
        "route": route,
    }
    provenance_records: list[dict] = []
    if cid:
        assistant_message_id = _save_message(db, cid, "assistant", generated, metadata)
        try:
            from .r12 import record_provenance
            grouped: dict[int, list[dict]] = {}
            for src in sources:
                if str(src.get("type") or "") != "transcript":
                    continue
                eid = int(src.get("episode_id") or 0)
                sid = int(src.get("segment_id") or 0)
                if eid > 0 and sid > 0:
                    grouped.setdefault(eid, []).append(src)
            for eid, refs in grouped.items():
                ids = sorted({int(x.get("segment_id") or 0) for x in refs if int(x.get("segment_id") or 0) > 0})
                placeholders = ",".join("?" for _ in ids)
                rows = db.all(
                    f"""SELECT id AS segment_id, start_ms, end_ms,
                               COALESCE(user_speaker_label, speaker_label, '') AS speaker_label,
                               COALESCE(user_text, text) AS text
                        FROM transcript_segments WHERE episode_id=? AND id IN ({placeholders}) ORDER BY start_ms""",
                    (eid, *ids),
                ) if ids else []
                if rows:
                    provenance_records.append(record_provenance(
                        db, object_type="ai_message", object_id=assistant_message_id,
                        claim_key=f"episode:{eid}", episode_id=eid, source_rows=rows,
                        model=str(metadata.get("model") or ""), prompt_version="r12-intelligence-2",
                    ))
        except Exception:
            provenance_records = []

    return {
        "answer": generated,
        "conversation_id": cid,
        "sources": sources,
        "actions": actions,
        "mode": "intelligence",
        "provider": status,
        "intelligence": {
            "contextual": True,
            "personal_context": bool(settings.personal_context and profile.get("personal", True)),
            "conversation_memory": settings.conversation_memory,
            "route": route,
            "profile": {k: v for k, v in profile.items() if k not in {"instruction", "tools"}},
            "tool_scope": profile.get("tools") or [],
            "model": run.get("model") or model_override or status.get("model"),
            "thinking": thinking,
            "tools_used": run.get("tools_used") or [],
            "vision_used": has_images,
            "vision_model": (run.get("model") or model_override) if has_images else settings.vision_model,
            "vision_detail": settings.vision_detail,
            "image_count": len(image_meta),
            "images": image_meta,
            "provenance": provenance_records,
        },
    }
