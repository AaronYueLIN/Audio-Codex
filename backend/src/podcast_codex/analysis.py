from __future__ import annotations

import json
import re
import time

from .ai import AISettings, ChatProvider
from .db import Database
from .search import index_embeddings

ENTITY_TYPES = {"PERSON", "TOPIC", "WORK", "PLACE", "EVENT", "ERA"}
DISCIPLINES = {"Culture", "History", "Philosophy", "Religion", "Science"}


def _parse_json_object(text: str) -> dict | None:
    value = str(text or "").strip()
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    fenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I | re.S).strip()
    if fenced != value:
        try:
            parsed = json.loads(fenced)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
    match = re.search(r"\{.*\}", value, re.S)
    if match:
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass
    return None


def _call_json(
    provider: ChatProvider,
    system: str,
    user: str,
    *,
    label: str,
    base_progress: float,
    progress_span: float,
    progress=None,
    max_output_tokens: int = 1_000_000,
) -> dict:
    last_update = 0.0
    streamed_chars = 0

    def observe(event: dict) -> None:
        nonlocal last_update, streamed_chars
        if progress is None:
            return
        now = time.monotonic()
        kind = event.get("type")
        message = None
        frac = 0.0
        if kind == "tool_start":
            message = f"AI · {label} · reading local archive via {event.get('name') or 'tool'}"
            frac = 0.12
        elif kind == "tool_end":
            message = f"AI · {label} · local archive context ready"
            frac = 0.24
        elif kind == "content_delta":
            streamed_chars += len(str(event.get("text") or ""))
            message = f"AI · {label} · receiving result · {streamed_chars:,} chars"
            frac = min(0.90, 0.32 + streamed_chars / 12000.0)
        elif kind == "upstream_done":
            finish = event.get("finish_reason") or "stream"
            usage = event.get("usage") or {}
            out = usage.get("completion_tokens") or usage.get("output_tokens") or 0
            message = f"AI · {label} · model round complete · {finish} · {out} output tokens"
            frac = 0.94
        elif kind == "reasoning":
            # Analyze should run thinking=false. If a provider still sends reasoning,
            # surface it as diagnostics instead of hiding a long apparent stall.
            message = f"AI · {label} · provider sent reasoning unexpectedly"
            frac = 0.08
        if message and (now - last_update >= 0.45 or kind in {"tool_start", "tool_end", "upstream_done"}):
            last_update = now
            progress(min(0.98, base_progress + progress_span * frac), message)

    raw = provider.chat(
        system,
        user,
        temperature=0.1,
        json_mode=True,
        max_output_tokens=max_output_tokens,
        event_callback=observe,
        thinking=False,       # critical: structured Analyze must not spend budget on reasoning
        use_tools=True,
        allow_client_actions=False,
    )
    payload = _parse_json_object(raw)
    if not payload:
        diag = provider.last_run or {}
        raise RuntimeError(
            "The configured LLM returned text, but Audio Codex could not parse a JSON object. "
            f"phase={label}, content_chars={len(raw)}, diagnostics={diag}"
        )
    return payload


def _summary_and_discipline(provider: ChatProvider, title: str, episode_id: int, progress=None) -> dict:
    return _call_json(
        provider,
        """You are the archival analyst inside Audio Codex. Use audiocodex.transcript.read to read the real episode transcript. Never invent facts. Return exactly one JSON object and no Markdown. If the episode is BBC In Our Time, discipline must be one of Culture, History, Philosophy, Religion, Science; otherwise discipline may be null.""",
        f"""Episode title: {title}\nEpisode ID: {episode_id}\n\nReturn exactly:\n{{\n  \"summary\": \"a precise archival summary, roughly 300-700 words when supported\",\n  \"discipline\": \"Culture|History|Philosophy|Religion|Science|null\"\n}}""",
        label="summary",
        base_progress=0.10,
        progress_span=0.16,
        progress=progress,
        max_output_tokens=1_000_000,
    )


def _chapters(provider: ChatProvider, title: str, episode_id: int,
              duration_ms: int, progress=None) -> list[dict]:
    payload = _call_json(
        provider,
        """You create chapter markers for spoken-word audio. Use audiocodex.transcript.read to read the real transcript for the Episode ID. Return exactly one JSON object and no Markdown. Chapters must follow real topical transitions, remain chronological, avoid tiny fragments, and use transcript-grounded timestamps.""",
        f"""Episode: {title}\nEpisode ID: {episode_id}\nDuration: {duration_ms} ms\n\nReturn exactly:\n{{\n  \"chapters\": [\n    {{\n      \"title\": \"short descriptive title\",\n      \"start_ms\": 0,\n      \"end_ms\": 0,\n      \"summary\": \"1-3 sentence chapter summary\"\n    }}\n  ]\n}}\nPrefer roughly 4-12 chapters for a normal long-form episode.""",
        label="chapters",
        base_progress=0.28,
        progress_span=0.20,
        progress=progress,
        max_output_tokens=1_000_000,
    )
    rows = payload.get("chapters")
    return rows if isinstance(rows, list) else []


def _entities(provider: ChatProvider, title: str, episode_id: int, progress=None) -> list[dict]:
    payload = _call_json(
        provider,
        """You extract archive entities from podcast transcripts. Use audiocodex.transcript.read to read the real transcript for the Episode ID. Never invent entities. Return exactly one JSON object and no Markdown. Valid types: PERSON, TOPIC, WORK, PLACE, EVENT, ERA. Prefer research-worthy entities over generic nouns and merge clear aliases.""",
        f"""Episode: {title}\nEpisode ID: {episode_id}\n\nReturn exactly:\n{{\n  \"entities\": [\n    {{\n      \"type\": \"PERSON|TOPIC|WORK|PLACE|EVENT|ERA\",\n      \"name\": \"canonical name\",\n      \"description\": \"short transcript-grounded description\",\n      \"confidence\": 0.0\n    }}\n  ]\n}}\nReturn at most 100 high-value entities.""",
        label="entities",
        base_progress=0.49,
        progress_span=0.18,
        progress=progress,
        max_output_tokens=1_000_000,
    )
    rows = payload.get("entities")
    return rows if isinstance(rows, list) else []


def _find_mentions(rows: list[dict], name: str) -> list[int]:
    needle = name.casefold()
    return [
        row["id"]
        for row in rows
        if needle in str(row.get("user_text") or row.get("text") or "").casefold()
    ]


def analyze_episode(db: Database, episode_id: int, generate_embeddings: bool = True,
                    progress=None) -> None:
    ep = db.one("SELECT * FROM episodes WHERE id=?", (episode_id,))
    if not ep:
        raise RuntimeError("Episode not found")
    rows = db.all(
        "SELECT * FROM transcript_segments WHERE episode_id=? ORDER BY segment_index",
        (episode_id,),
    )
    if not rows:
        raise RuntimeError("Transcribe the episode before analysis")

    settings = AISettings.from_db(db)
    provider = ChatProvider(settings, db)
    status = provider.status()
    if not status["available"]:
        raise RuntimeError(
            "Analyze requires a remote API-key AI provider. Open Settings → AI / MCP and enter a DeepSeek API Key. "
            + status.get("detail", "")
        )

    db.execute("UPDATE episodes SET analysis_status='running' WHERE id=?", (episode_id,))
    try:
        if progress:
            progress(0.08, "AI · structured Analyze · thinking disabled")
        overview = _summary_and_discipline(provider, ep["title"], episode_id, progress=progress)

        if progress:
            progress(0.28, "AI · chapters · thinking disabled")
        chapters = _chapters(
            provider, ep["title"], episode_id, int(ep.get("duration_ms") or 0), progress=progress
        )

        if progress:
            progress(0.49, "AI · entities · thinking disabled")
        entities = _entities(provider, ep["title"], episode_id, progress=progress)

        discipline = overview.get("discipline")
        if discipline not in DISCIPLINES:
            discipline = None
        summary = str(overview.get("summary") or "")[:12000]

        normalized_chapters: list[dict] = []
        duration = max(0, int(ep.get("duration_ms") or 0))
        for item in chapters[:50]:
            if not isinstance(item, dict):
                continue
            try:
                start = max(0, int(item.get("start_ms") or 0))
                end = max(0, int(item.get("end_ms") or 0))
            except Exception:
                continue
            normalized_chapters.append({
                "title": str(item.get("title") or "Chapter")[:240],
                "start_ms": start,
                "end_ms": end,
                "summary": str(item.get("summary") or "")[:2000],
            })
        normalized_chapters.sort(key=lambda x: x["start_ms"])
        for i, item in enumerate(normalized_chapters):
            next_start = normalized_chapters[i + 1]["start_ms"] if i + 1 < len(normalized_chapters) else duration
            if item["end_ms"] <= item["start_ms"]:
                item["end_ms"] = max(item["start_ms"], next_start)
            if duration:
                item["start_ms"] = min(item["start_ms"], duration)
                item["end_ms"] = min(max(item["start_ms"], item["end_ms"]), duration)

        with db.tx() as conn:
            conn.execute(
                "UPDATE episodes SET summary=?, discipline=? WHERE id=?",
                (summary, discipline, episode_id),
            )
            conn.execute("DELETE FROM chapters WHERE episode_id=?", (episode_id,))
            for chapter in normalized_chapters:
                conn.execute(
                    """INSERT INTO chapters(episode_id, start_ms, end_ms, title, summary)
                       VALUES(?, ?, ?, ?, ?)""",
                    (episode_id, chapter["start_ms"], chapter["end_ms"], chapter["title"], chapter["summary"]),
                )
            conn.execute(
                "DELETE FROM episode_entities WHERE episode_id=? AND user_confirmed=0",
                (episode_id,),
            )

        clean_entities = entities if isinstance(entities, list) else []
        for idx, item in enumerate(clean_entities[:100]):
            if not isinstance(item, dict):
                continue
            typ = str(item.get("type") or "TOPIC").upper()
            name = str(item.get("name") or "").strip()
            if typ not in ENTITY_TYPES or len(name) < 2:
                continue
            normalized = re.sub(r"\s+", " ", name.casefold()).strip()
            entity = db.one(
                "SELECT * FROM entities WHERE type=? AND normalized_key=?",
                (typ, normalized),
            )
            description = str(item.get("description") or "")[:4000]
            if entity:
                entity_id = entity["id"]
                if not entity.get("user_description") and description:
                    db.execute("UPDATE entities SET description=? WHERE id=?", (description, entity_id))
            else:
                entity_id = db.execute(
                    """INSERT INTO entities(type, canonical_name, normalized_key, description)
                       VALUES(?, ?, ?, ?)""",
                    (typ, name, normalized, description),
                )
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 0.7))))
            except Exception:
                confidence = 0.7
            mentions = _find_mentions(rows, name)
            if mentions:
                for segment_id in mentions[:80]:
                    db.execute(
                        """INSERT OR IGNORE INTO episode_entities(
                           episode_id, entity_id, segment_id, confidence, user_confirmed
                        ) VALUES(?, ?, ?, ?, 0)""",
                        (episode_id, entity_id, segment_id, confidence),
                    )
            else:
                db.execute(
                    """INSERT OR IGNORE INTO episode_entities(
                       episode_id, entity_id, segment_id, confidence, user_confirmed
                    ) VALUES(?, ?, NULL, ?, 0)""",
                    (episode_id, entity_id, confidence),
                )
            if progress:
                progress(0.68 + 0.13 * ((idx + 1) / max(1, len(clean_entities))), f"Index · entity · {name}")

        entity_ids = [
            row["entity_id"]
            for row in db.all(
                "SELECT DISTINCT entity_id FROM episode_entities WHERE episode_id=?",
                (episode_id,),
            )
        ]
        for source in entity_ids:
            for target in entity_ids:
                if source == target:
                    continue
                db.execute(
                    """INSERT OR IGNORE INTO entity_relations(
                       source_entity_id, target_entity_id, relation, confidence
                    ) VALUES(?, ?, 'CO_OCCURS_IN_EPISODE', 0.25)""",
                    (source, target),
                )

        if generate_embeddings:
            if progress:
                progress(0.84, "Index · embeddings when an embedding API is configured")
            index_embeddings(
                db,
                episode_id,
                progress=(lambda p, m: progress(0.84 + p * 0.13, m)) if progress else None,
            )

        db.execute("UPDATE episodes SET analysis_status='ready' WHERE id=?", (episode_id,))
        if progress:
            progress(0.99, "AI analysis ready")
    except Exception:
        db.execute("UPDATE episodes SET analysis_status='error' WHERE id=?", (episode_id,))
        raise
