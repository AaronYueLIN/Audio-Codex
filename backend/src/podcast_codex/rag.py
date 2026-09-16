from __future__ import annotations

import base64
import binascii
import re
from typing import Any

from .ai import AISettings, ChatProvider
from .db import Database
from .mcp import transcript_read, transcript_search


MAX_CHAT_IMAGES = 4
MAX_CHAT_IMAGE_BYTES = 10 * 1024 * 1024
MAX_CHAT_IMAGE_TOTAL_BYTES = 24 * 1024 * 1024
MAX_HISTORY_MESSAGES = 10
MAX_HISTORY_CHARS = 48_000
MAX_SELECTED_TEXT_CHARS = 12_000
_SUPPORTED_IMAGE_MIME = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_DATA_URL_RE = re.compile(
    r"^data:(image/(?:jpeg|png|gif|webp));base64,([A-Za-z0-9+/=]+)$",
    re.IGNORECASE,
)


def _fmt_ts(ms: int) -> str:
    sec = max(0, int(ms or 0)) // 1000
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def _chapter_index(db: Database | None, episode_ids: set[int]) -> dict[int, list[dict]]:
    """episode_id -> chapters, so a citation can name the material's own section."""
    if not db or not episode_ids:
        return {}
    placeholders = ",".join("?" for _ in episode_ids)
    rows = db.all(
        f"SELECT episode_id, start_ms, end_ms, title FROM chapters "
        f"WHERE episode_id IN ({placeholders}) ORDER BY start_ms",
        tuple(sorted(episode_ids)),
    )
    out: dict[int, list[dict]] = {}
    for row in rows:
        out.setdefault(int(row["episode_id"]), []).append(row)
    return out


def _chapter_title(chapters: list[dict], start_ms: int) -> str:
    for ch in chapters:
        if int(ch["start_ms"]) <= int(start_ms) <= int(ch["end_ms"]):
            return str(ch.get("title") or "")
    return ""


def _render_source_line(source: dict) -> str:
    """One grounded citation line: carrier (podcast / episode) + timestamp + chapter + speaker."""
    where = " / ".join(x for x in (source.get("podcast_title"), source.get("episode_title")) if x)
    head = f"[{source['key']}] {where} @ {_fmt_ts(source['start_ms'])}-{_fmt_ts(source['end_ms'])}"
    if source.get("chapter_title"):
        head += f" «{source['chapter_title']}»"
    if source.get("speaker_label"):
        head += f" {source['speaker_label']}:"
    return f"{head} {source['text']}"


def _source_rows_from_read(context: dict, db: Database | None = None) -> tuple[list[dict], str]:
    ep = context["episode"]
    chapters = _chapter_index(db, {int(ep["id"])})
    sources: list[dict] = []
    for i, seg in enumerate(context.get("segments") or [], start=1):
        sources.append({
            "key": f"S{i}",
            "citation": f"S{i}",
            "segment_id": seg["segment_id"],
            "segment_index": seg.get("segment_index"),
            "episode_id": ep["id"],
            "episode_title": ep["title"],
            "podcast_id": ep.get("podcast_id"),
            "podcast_title": ep["podcast_title"],
            "start_ms": seg["start_ms"],
            "end_ms": seg["end_ms"],
            "speaker_label": seg.get("speaker_label") or "",
            "chapter_title": _chapter_title(chapters.get(int(ep["id"]), []), seg["start_ms"]),
            "text": seg["text"],
        })
    return sources, "\n".join(_render_source_line(s) for s in sources)


def _source_rows_from_search(context: dict, db: Database | None = None) -> tuple[list[dict], str]:
    hits = context.get("hits") or []
    chapters = _chapter_index(db, {int(h["episode_id"]) for h in hits})
    sources: list[dict] = []
    for i, hit in enumerate(hits, start=1):
        sources.append({
            "key": f"S{i}",
            "citation": f"S{i}",
            "segment_id": hit["segment_id"],
            "segment_index": hit.get("segment_index"),
            "episode_id": hit["episode_id"],
            "episode_title": hit["episode_title"],
            "podcast_id": hit.get("podcast_id"),
            "podcast_title": hit["podcast_title"],
            "start_ms": hit["start_ms"],
            "end_ms": hit["end_ms"],
            "speaker_label": hit.get("speaker_label") or "",
            "chapter_title": _chapter_title(chapters.get(int(hit["episode_id"]), []), hit["start_ms"]),
            "text": hit["text"],
        })
    return sources, "\n".join(_render_source_line(s) for s in sources)


def _image_signature_matches(mime: str, raw: bytes) -> bool:
    mime = mime.lower()
    if mime == "image/jpeg":
        return raw.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return raw.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/gif":
        return raw.startswith((b"GIF87a", b"GIF89a"))
    if mime == "image/webp":
        return len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    return False


def _prepare_images(images: list[dict] | None, default_detail: str) -> tuple[list[dict], list[dict]]:
    rows = list(images or [])
    if len(rows) > MAX_CHAT_IMAGES:
        raise ValueError(f"Attach at most {MAX_CHAT_IMAGES} images per AI session.")
    content_parts: list[dict] = []
    meta: list[dict] = []
    total = 0
    for index, item in enumerate(rows, start=1):
        data_url = str(item.get("data_url") or "").strip()
        match = _DATA_URL_RE.fullmatch(data_url)
        if not match:
            raise ValueError("Images must be JPEG, PNG, GIF, or WebP data URLs.")
        mime = match.group(1).lower()
        if mime not in _SUPPORTED_IMAGE_MIME:
            raise ValueError(f"Unsupported image format: {mime}")
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f"Image {index} contains invalid base64 data.") from exc
        if not raw or not _image_signature_matches(mime, raw):
            raise ValueError(f"Image {index} content does not match its declared format.")
        if len(raw) > MAX_CHAT_IMAGE_BYTES:
            raise ValueError(
                f"Image {index} is larger than Audio Codex's {MAX_CHAT_IMAGE_BYTES // (1024 * 1024)} MiB per-image safety limit."
            )
        total += len(raw)
        if total > MAX_CHAT_IMAGE_TOTAL_BYTES:
            raise ValueError(
                f"Attached images exceed Audio Codex's {MAX_CHAT_IMAGE_TOTAL_BYTES // (1024 * 1024)} MiB session limit."
            )
        detail = str(item.get("detail") or default_detail or "auto").strip().lower()
        if detail not in {"low", "high", "original", "auto"}:
            detail = "auto"
        name = str(item.get("name") or f"Image {index}").strip()[:160] or f"Image {index}"
        content_parts.append({
            "type": "image_url",
            "image_url": {"url": data_url, "detail": detail},
        })
        meta.append({"name": name, "mime": mime, "bytes": len(raw), "detail": detail})
    return content_parts, meta


def _prepare_history(history: list[dict] | None) -> list[dict]:
    items = list(history or [])[-MAX_HISTORY_MESSAGES:]
    out: list[dict] = []
    used = 0
    # Keep the newest useful context while bounding prompt growth.
    for item in reversed(items):
        role = str(item.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        content = content[:12_000]
        if used + len(content) > MAX_HISTORY_CHARS:
            remaining = MAX_HISTORY_CHARS - used
            if remaining < 500:
                break
            content = content[-remaining:]
        used += len(content)
        out.append({"role": role, "content": content})
        if used >= MAX_HISTORY_CHARS:
            break
    out.reverse()
    return out


def _screen_context_text(db: Database, screen_context: dict | None) -> tuple[str, dict]:
    raw = dict(screen_context or {})
    view = str(raw.get("view") or "").strip()[:80]
    episode_id = raw.get("episode_id")
    playback_ms = raw.get("playback_ms")
    selected_text = str(raw.get("selected_text") or "").strip()[:MAX_SELECTED_TEXT_CHARS]
    meta: dict[str, Any] = {
        "view": view,
        "episode_id": None,
        "episode_title": "",
        "podcast_title": "",
        "playback_ms": None,
        "selected_text_chars": len(selected_text),
    }
    lines: list[str] = []
    if view:
        lines.append(f"Current Audio Codex view: {view}")
    if episode_id is not None:
        try:
            eid = int(episode_id)
        except Exception:
            eid = 0
        if eid > 0:
            ep = db.one(
                """SELECT e.id, e.title, p.title AS podcast_title
                   FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.id=?""",
                (eid,),
            )
            if ep:
                meta["episode_id"] = eid
                meta["episode_title"] = str(ep.get("title") or "")
                meta["podcast_title"] = str(ep.get("podcast_title") or "")
                lines.append(
                    f"Visible episode: {meta['podcast_title']} / {meta['episode_title']} (episode_id={eid})"
                )
    if playback_ms is not None:
        try:
            pos = max(0, int(playback_ms))
        except Exception:
            pos = 0
        meta["playback_ms"] = pos
        lines.append(f"Visible playback position: {_fmt_ts(pos)} ({pos} ms)")
    if selected_text:
        lines.append("Text currently selected onscreen:\n" + selected_text)
    return "\n".join(lines), meta


def _merge_sources(primary: list[dict], tool_sources: list[dict], limit: int = 160) -> list[dict]:
    out: list[dict] = []
    seen: set[tuple] = set()
    # Keep both S# and T# variants if the model may have cited either marker.
    for source in list(primary or []) + list(tool_sources or []):
        key = str(source.get("citation") or source.get("key") or "")
        fingerprint = (
            key,
            source.get("segment_id"),
            source.get("episode_id"),
            int(source.get("start_ms") or 0),
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        out.append(source)
        if len(out) >= limit:
            break
    return out


def _agent_system_prompt(has_images: bool) -> str:
    visual_rule = (
        "The user attached one or more images in the current user message. Treat them as direct visual evidence. "
        "You may read screenshot text, understand charts, identify visible objects, and combine what you see with Audio Codex tools."
        if has_images
        else
        "No image is attached in the current user message. Do not imply that you can see anything that was not provided."
    )
    return f"""You are Audio Codex Intelligence, the context-aware AI agent inside Audio Codex.
Your job is to understand what the user is doing now, combine that with their private local podcast archive,
and respond naturally without making them restate obvious context.

Context and grounding rules:
- {visual_rule}
- The current-screen context is supplied by Audio Codex itself. Resolve phrases such as "this episode", "here",
  "this part", or "what I'm looking at" from that context when possible.
- Archive context marked [S#] comes from local Audio Codex transcript retrieval. Any substantive claim based on it
  must cite the matching [S#] marker and mention the episode/timestamp naturally when useful.
- Transcript tools may return rows with citation_key values such as T1. Cite those exactly as [T1] when you use them.
  This is especially important when you derive a transcript search query from an attached image.
- Visual observations do not need transcript citation markers; make clear when a statement comes from an attached image.
- You may use general model knowledge for explanation when helpful, but distinguish it from facts found in the user's
  archive or images. Never fabricate archive matches, timestamps, speakers, or visual details.
- Prefer the most relevant current context over asking the user to repeat information already visible in Audio Codex.

Actions:
- You can use Audio Codex UI action tools to PREPARE opening an episode, searching the Library, creating a bookmark,
  or creating a note. These tools do not execute the mutation. The user must confirm the action in the UI.
- Never claim a bookmark/note/search/navigation action is completed merely because you called a preparation tool.

Style:
- Be concise, capable, and conversational. Give the useful result first.
- Do not expose hidden chain-of-thought or internal reasoning. If uncertain, state the uncertainty plainly."""


def _user_prompt(message: str, screen_text: str, archive_context: str, has_images: bool) -> str:
    return f"""User request:
{message}

Current Audio Codex screen context:
{screen_text or '(No specific onscreen item was supplied.)'}

Local archive context retrieved before this model call:
{archive_context or '(No matching transcript rows were found before this model call. You may use transcript tools if needed.)'}

Current visual input:
{'Attached images follow this text block.' if has_images else 'No attached images.'}

Answer the user using the strongest available evidence. Preserve citation markers for archive evidence."""


def answer(
    db: Database,
    message: str,
    scope: str,
    episode_id: int | None = None,
    collection_id: int | None = None,
    podcast_id: int | None = None,
    passage_ms: int | None = None,
    *,
    images: list[dict] | None = None,
    history: list[dict] | None = None,
    screen_context: dict | None = None,
) -> dict:
    message = str(message or "").strip()
    settings = AISettings.from_db(db)
    provider = ChatProvider(settings, db)
    status = provider.status()

    if not status["available"]:
        return {
            "answer": (
                "Ask requires an API Key. Open Settings → AI / MCP and configure "
                "DeepSeek or an OpenAI-compatible API. "
                + status.get("detail", "")
            ),
            "sources": [],
            "actions": [],
            "mode": "unavailable",
            "mcp": {"context_tokens": settings.context_tokens},
            "agent": {"vision_used": False, "image_count": 0},
        }

    image_parts, image_meta = _prepare_images(images, settings.vision_detail)
    has_images = bool(image_parts)
    if not message and has_images:
        message = "What should I know about the attached image in my current Audio Codex context?"
    if not message:
        raise ValueError("Ask a question or attach an image.")
    if has_images and not settings.vision_model:
        raise ValueError("A vision model is required before image questions can be sent.")
    history_messages = _prepare_history(history)
    screen_text, screen_meta = _screen_context_text(db, screen_context)

    # If the UI supplied the visible episode but the request omitted the duplicate ID, use the trusted DB-validated ID.
    if not episode_id and scope in {"episode", "passage"} and screen_meta.get("episode_id"):
        episode_id = int(screen_meta["episode_id"])
    if passage_ms is None and scope == "passage" and screen_meta.get("playback_ms") is not None:
        passage_ms = int(screen_meta["playback_ms"])

    sources: list[dict] = []
    archive_context = ""
    if scope == "episode":
        if episode_id:
            context = transcript_read(
                db,
                episode_id,
                max_tokens=settings.context_tokens,
                include_timestamps=True,
            )
            sources, archive_context = _source_rows_from_read(context, db)
            mcp_meta = {
                "tool": "audiocodex.transcript.read",
                "token_estimate": context["token_estimate"],
                "context_tokens": context["context_budget_tokens"],
                "truncated": context["truncated"],
            }
        else:
            mcp_meta = {"tool": "audiocodex.transcript.read", "context_tokens": settings.context_tokens, "missing_episode": True}
    elif scope == "passage":
        if episode_id:
            center = max(0, int(passage_ms or 0))
            context = transcript_read(
                db,
                episode_id,
                start_ms=max(0, center - 5 * 60 * 1000),
                end_ms=center + 5 * 60 * 1000,
                max_tokens=settings.context_tokens,
                include_timestamps=True,
            )
            sources, archive_context = _source_rows_from_read(context, db)
            mcp_meta = {
                "tool": "audiocodex.transcript.read",
                "token_estimate": context["token_estimate"],
                "context_tokens": context["context_budget_tokens"],
                "truncated": context["truncated"],
                "passage_ms": center,
            }
        else:
            mcp_meta = {"tool": "audiocodex.transcript.read", "context_tokens": settings.context_tokens, "missing_episode": True}
    else:
        context = transcript_search(
            db,
            message,
            scope=scope,
            episode_id=episode_id,
            collection_id=collection_id,
            podcast_id=podcast_id,
            limit=120,
            max_tokens=settings.context_tokens,
        )
        sources, archive_context = _source_rows_from_search(context, db)
        mcp_meta = {
            "tool": "audiocodex.transcript.search",
            "token_estimate": context["token_estimate"],
            "context_tokens": context["context_budget_tokens"],
            "truncated": context["truncated"],
        }

    system = _agent_system_prompt(has_images)
    prompt_text = _user_prompt(message, screen_text, archive_context, has_images)
    user_content: Any
    if has_images:
        user_content = [{"type": "text", "text": prompt_text}, *image_parts]
    else:
        user_content = prompt_text
    model_override = settings.vision_model if has_images else None

    try:
        generated = provider.chat(
            system,
            user_content,
            history=history_messages,
            model_override=model_override,
            temperature=0.2,
            max_output_tokens=8192,
            allow_client_actions=True,
        )
    except Exception as exc:
        # Model-native context can be smaller than Audio Codex's configured MCP budget.
        if scope not in {"episode", "passage"} or len(sources) < 8:
            raise
        last = exc
        for fraction in (0.5, 0.25, 0.125):
            n = max(4, int(len(sources) * fraction))
            subset = sources[:n]
            smaller = "\n".join(_render_source_line(s) for s in subset)
            try:
                retry_prompt = _user_prompt(message, screen_text, smaller, has_images)
                retry_content: Any = (
                    [{"type": "text", "text": retry_prompt}, *image_parts]
                    if has_images else retry_prompt
                )
                generated = provider.chat(
                    system,
                    retry_content,
                    history=history_messages,
                    model_override=model_override,
                    temperature=0.2,
                    max_output_tokens=8192,
                    allow_client_actions=True,
                )
                sources = subset
                mcp_meta["model_context_retry"] = fraction
                break
            except Exception as retry_exc:
                last = retry_exc
        else:
            raise last

    run = provider.last_run or {}
    merged_sources = _merge_sources(sources, run.get("tool_sources") or [])
    actions = list(run.get("actions") or [])
    actual_model = str(run.get("model") or model_override or status.get("model") or "")
    return {
        "answer": generated,
        "sources": merged_sources,
        "actions": actions,
        "mode": "multimodal" if has_images else "generated",
        "mcp": mcp_meta,
        "provider": {**status, "actual_model": actual_model},
        "agent": {
            "vision_used": has_images,
            "vision_model": actual_model if has_images else settings.vision_model,
            "vision_detail": settings.vision_detail,
            "image_count": len(image_meta),
            "images": image_meta,
            "screen_context_used": bool(screen_text),
            "screen_context": screen_meta,
            "history_messages": len(history_messages),
            "tools_used": run.get("tools_used") or [],
            "action_count": len(actions),
        },
    }
