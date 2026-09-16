from __future__ import annotations

import copy
import json
import math
import time
import re
import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Dict, Generator, List

import requests

from .db import Database

DEFAULT_CONTEXT_TOKENS = 1_000_000
MAX_CONTEXT_TOKENS = 1_000_000
MAX_TOOL_ROUNDS = 10
DEFAULT_DEEPSEEK_BASE = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"
DEFAULT_DEEPSEEK_VISION_MODEL = "deepseek-flash"
DEFAULT_DEEPSEEK_PRO_MODEL = "deepseek-flash"
DEFAULT_VISION_DETAIL = "auto"

# Provider output ceilings. Analyze can ask for a very large logical budget;
# _request() clamps the actual request to the selected provider's supported ceiling.
DEEPSEEK_MAX_OUTPUT_TOKENS = 393_216
OPENAI_MAX_OUTPUT_TOKENS = 32_768
MAX_OUTPUT_TOKENS = DEEPSEEK_MAX_OUTPUT_TOKENS
DEFAULT_CHAT_MAX_TOKENS = MAX_OUTPUT_TOKENS
DEFAULT_ANALYZE_MAX_TOKENS = MAX_OUTPUT_TOKENS

DEFAULT_REASONING_EFFORT = "high"
DEFAULT_TIMEOUT_SECONDS = 120.0


def clamp_context_tokens(value: Any) -> int:
    try:
        n = int(value)
    except Exception:
        n = DEFAULT_CONTEXT_TOKENS
    return max(32_000, min(MAX_CONTEXT_TOKENS, n))


def _option_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _deepseek_base(value: str) -> str:
    url = (value or DEFAULT_DEEPSEEK_BASE).strip().rstrip("/")
    for suffix in ("/chat/completions", "/beta", "/v1", "/anthropic"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url or DEFAULT_DEEPSEEK_BASE


def _openai_base(value: str) -> str:
    url = (value or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
    return url


def _openai_url(base: str, path: str) -> str:
    base = _openai_base(base)
    path = path.lstrip("/")
    if base.endswith("/v1"):
        return f"{base}/{path}"
    return f"{base}/v1/{path}"


def _event(kind: str, **fields: Any) -> Dict[str, Any]:
    payload = {"type": kind}
    payload.update(fields)
    return payload


def _emit(callback: Callable[[dict], None] | None, event: dict) -> None:
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        # UI/observer callbacks are informational only and must never corrupt a model request.
        pass


def _request_json(url: str, *, payload: dict | None = None,
                  headers: dict[str, str] | None = None,
                  timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    try:
        response = requests.request(
            "GET" if payload is None else "POST",
            url,
            headers={
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if payload is not None else {}),
                **(headers or {}),
            },
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        detail = ""
        response = getattr(exc, "response", None)
        if response is not None:
            try:
                detail = (response.text or "")[:500]
            except Exception:
                detail = ""
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"AI endpoint request failed{suffix}") from exc
    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError("AI endpoint did not return valid JSON") from exc


def _strict_schema(schema: dict) -> dict:
    result = copy.deepcopy(schema or {})
    if result.get("type") == "object":
        props = result.get("properties") or {}
        result["additionalProperties"] = False
        result["required"] = list(props.keys())
        for key, value in list(props.items()):
            if isinstance(value, dict) and value.get("type") == "object":
                props[key] = _strict_schema(value)
    return result


def _wire_name(name: str) -> str:
    """Return a DeepSeek/OpenAI-compatible function name.

    Audio Codex MCP keeps dotted internal names; the wire protocol only accepts
    letters, numbers, underscore and hyphen.
    """
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", str(name or "")).strip("_") or "tool"


# Agent-side actions are deliberately proposals, not direct mutations. The model may
# prepare one, but the UI must show a button and the user must explicitly confirm it.
CLIENT_ACTION_TOOLS = [
    {
        "name": "audiocodex.ui.open_episode",
        "description": "Prepare a user-confirmable action to open an episode, optionally at a timestamp.",
        "inputSchema": {"type": "object", "properties": {
            "episode_id": {"type": "integer"}, "position_ms": {"type": ["integer", "null"]}
        }, "required": ["episode_id"]},
    },
    {
        "name": "audiocodex.ui.play_audio",
        "description": "Prepare a user-confirmable action to play an episode at a specific timestamp while keeping the current Intelligence context available.",
        "inputSchema": {"type": "object", "properties": {
            "episode_id": {"type": "integer"}, "position_ms": {"type": "integer", "minimum": 0}
        }, "required": ["episode_id", "position_ms"]},
    },
    {
        "name": "audiocodex.ui.open_entity",
        "description": "Prepare a user-confirmable action to open a knowledge entity in Audio Codex.",
        "inputSchema": {"type": "object", "properties": {"entity_id": {"type": "integer"}}, "required": ["entity_id"]},
    },
    {
        "name": "audiocodex.ui.open_collection",
        "description": "Prepare a user-confirmable action to open a collection in Audio Codex.",
        "inputSchema": {"type": "object", "properties": {"collection_id": {"type": "integer"}}, "required": ["collection_id"]},
    },
    {
        "name": "audiocodex.ui.open_view",
        "description": "Prepare a user-confirmable navigation action to an Audio Codex view.",
        "inputSchema": {"type": "object", "properties": {
            "view": {"type": "string", "enum": ["home", "episodes", "collections", "jobs", "entities", "timeline", "notes", "intelligence", "system"]}
        }, "required": ["view"]},
    },
    {
        "name": "audiocodex.ui.create_bookmark",
        "description": "Prepare a user-confirmable bookmark at an episode timestamp. Do not claim it is saved before confirmation.",
        "inputSchema": {"type": "object", "properties": {
            "episode_id": {"type": "integer"}, "position_ms": {"type": "integer"}, "label": {"type": "string"}
        }, "required": ["episode_id", "position_ms", "label"]},
    },
    {
        "name": "audiocodex.ui.transcribe_episode",
        "description": "Prepare a user-confirmable action to start local transcription for an episode.",
        "inputSchema": {"type": "object", "properties": {"episode_id": {"type": "integer"}}, "required": ["episode_id"]},
    },
    {
        "name": "audiocodex.ui.analyze_episode",
        "description": "Prepare a user-confirmable action to run Audio Codex Analyse on an episode and update its knowledge graph.",
        "inputSchema": {"type": "object", "properties": {"episode_id": {"type": "integer"}}, "required": ["episode_id"]},
    },
    {
        "name": "audiocodex.ui.generate_captions",
        "description": "Prepare a user-confirmable action to generate local live-caption segments for an episode.",
        "inputSchema": {"type": "object", "properties": {"episode_id": {"type": "integer"}}, "required": ["episode_id"]},
    },
    {
        "name": "audiocodex.ui.create_note",
        "description": "Prepare a user-confirmable local note attached to an episode or knowledge entity.",
        "inputSchema": {"type": "object", "properties": {
            "episode_id": {"type": ["integer", "null"]}, "entity_id": {"type": ["integer", "null"]},
            "segment_id": {"type": ["integer", "null"]}, "body": {"type": "string"}
        }, "required": ["body"]},
    },
    {
        "name": "audiocodex.ui.create_collection",
        "description": "Prepare a user-confirmable action to create a collection.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "description": {"type": "string"}
        }, "required": ["name"]},
    },
    {
        "name": "audiocodex.ui.add_to_collection",
        "description": "Prepare a user-confirmable action to add an episode to an existing collection.",
        "inputSchema": {"type": "object", "properties": {
            "collection_id": {"type": "integer"}, "episode_id": {"type": "integer"}
        }, "required": ["collection_id", "episode_id"]},
    },
    {
        "name": "audiocodex.ui.update_entity_annotation",
        "description": "Prepare a user-confirmable action to update the user's local annotation for a knowledge entity.",
        "inputSchema": {"type": "object", "properties": {
            "entity_id": {"type": "integer"}, "description": {"type": "string"}
        }, "required": ["entity_id", "description"]},
    },
    {
        "name": "audiocodex.ui.update_collection",
        "description": "Prepare a user-confirmable action to rename a collection or update its description without deleting anything.",
        "inputSchema": {"type": "object", "properties": {
            "collection_id": {"type": "integer"}, "name": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"]}
        }, "required": ["collection_id"]},
    },
    {
        "name": "audiocodex.ui.search_library",
        "description": "Prepare a user-confirmable Audio Codex Library search with a concise query.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    {
        "name": "audiocodex.ui.remember_memory",
        "description": "Prepare a user-confirmable action to remember one explicit, stable preference or instruction locally. Use only when the user clearly asks Audio Codex to remember it.",
        "inputSchema": {"type": "object", "properties": {
            "content": {"type": "string"}, "kind": {"type": "string"}
        }, "required": ["content"]},
    },
    {
        "name": "audiocodex.ui.forget_memory",
        "description": "Prepare a user-confirmable action to forget one specific explicit local AI memory. Resolve the memory with memory.list first and pass its exact ID.",
        "inputSchema": {"type": "object", "properties": {
            "memory_id": {"type": "integer"}
        }, "required": ["memory_id"]},
    },
    {
        "name": "audiocodex.ui.create_listening_recap",
        "description": "Prepare a user-confirmable action to generate a sourced recap of what was actually heard in the current listening session.",
        "inputSchema": {"type": "object", "properties": {
            "episode_id": {"type": "integer"}, "session_id": {"type": ["string", "null"]}
        }, "required": ["episode_id"]},
    },
    {
        "name": "audiocodex.ui.create_knowledge_watch",
        "description": "Prepare a user-confirmable Knowledge Watch. It monitors future local transcript/knowledge updates while Audio Codex is running. Use mention/topic for new mentions, contradiction only with explicit reference_text, or new_episode for any new matching episode.",
        "inputSchema": {"type": "object", "properties": {
            "name": {"type": "string"}, "query": {"type": "string"},
            "mode": {"type": "string", "enum": ["mention", "topic", "contradiction", "new_episode"]},
            "scope": {"type": "string", "enum": ["library", "episode", "collection", "entity"]},
            "episode_id": {"type": ["integer", "null"]}, "collection_id": {"type": ["integer", "null"]},
            "entity_id": {"type": ["integer", "null"]}, "reference_text": {"type": "string"}
        }, "required": ["query"]},
    },
    {
        "name": "audiocodex.ui.acknowledge_watch_event",
        "description": "Prepare a user-confirmable action to mark one surfaced Knowledge Watch match as read.",
        "inputSchema": {"type": "object", "properties": {
            "event_id": {"type": "integer"}
        }, "required": ["event_id"]},
    },
    {
        "name": "audiocodex.ui.set_knowledge_watch",
        "description": "Prepare a user-confirmable action to pause or resume an existing Knowledge Watch.",
        "inputSchema": {"type": "object", "properties": {
            "watch_id": {"type": "integer"}, "active": {"type": "boolean"}
        }, "required": ["watch_id", "active"]},
    },
]
_CLIENT_ACTION_NAMES = {str(tool["name"]) for tool in CLIENT_ACTION_TOOLS}
_PERSONAL_READ_TOOL_NAMES = {
    "audiocodex.notes.list",
    "audiocodex.bookmarks.list",
    "audiocodex.memory.list",
    "audiocodex.watch.list",
    "audiocodex.watch.events",
}
_PERSONAL_ACTION_TOOL_NAMES = {
    "audiocodex.ui.remember_memory",
    "audiocodex.ui.forget_memory",
}


def _all_tool_specs(
    include_client_actions: bool = True,
    include_web_search: bool = True,
    include_personal_context: bool = True,
    allowed_tool_names: set[str] | None = None,
) -> list[dict]:
    from .mcp import TOOLS
    tools = [
        tool for tool in TOOLS
        if (include_web_search or str(tool.get("name") or "") != "audiocodex.web.search")
        and (include_personal_context or str(tool.get("name") or "") not in _PERSONAL_READ_TOOL_NAMES)
        and (allowed_tool_names is None or str(tool.get("name") or "") in allowed_tool_names)
    ]
    actions = [
        tool for tool in CLIENT_ACTION_TOOLS
        if (include_personal_context or str(tool.get("name") or "") not in _PERSONAL_ACTION_TOOL_NAMES)
        and (allowed_tool_names is None or str(tool.get("name") or "") in allowed_tool_names)
    ] if include_client_actions else []
    return tools + actions


# Reverse map is refreshed lazily to avoid the ai -> mcp -> search -> ai import cycle.
_WIRE_NAMES: dict[str, str] = {}


def _refresh_wire_names() -> dict[str, str]:
    global _WIRE_NAMES
    _WIRE_NAMES = {
        _wire_name(str(tool["name"])): str(tool["name"])
        for tool in _all_tool_specs(
            include_client_actions=True, include_web_search=True, include_personal_context=True
        )
    }
    return _WIRE_NAMES


def _api_tool_name(internal_name: str) -> str:
    return _wire_name(internal_name)


def _internal_tool_name(api_name: str) -> str:
    return _refresh_wire_names().get(api_name, api_name)

def _tool_definitions(
    strict: bool = False,
    include_client_actions: bool = True,
    include_web_search: bool = True,
    include_personal_context: bool = True,
    allowed_tool_names: set[str] | None = None,
) -> list[dict]:
    result = []
    for tool in _all_tool_specs(
        include_client_actions=include_client_actions,
        include_web_search=include_web_search,
        include_personal_context=include_personal_context,
        allowed_tool_names=allowed_tool_names,
    ):
        internal_name = str(tool["name"])
        function = {
            "name": _api_tool_name(internal_name),
            "description": tool["description"],
            "parameters": copy.deepcopy(tool["inputSchema"]),
        }
        if strict:
            function["parameters"] = _strict_schema(function["parameters"])
            function["strict"] = True
        result.append({"type": "function", "function": function})
    return result


def _execute_tool(db: Database | None, name: str, arguments: dict) -> dict:
    internal_name = _internal_tool_name(name)
    args = arguments or {}
    if internal_name in _CLIENT_ACTION_NAMES:
        try:
            action: dict[str, Any]
            if internal_name == "audiocodex.ui.search_library":
                query = str(args.get("query") or "").strip()[:500]
                if not query:
                    raise RuntimeError("Search query is required")
                action = {"type": "search_library", "arguments": {"query": query}}
            elif internal_name == "audiocodex.ui.open_view":
                view = str(args.get("view") or "").strip()
                if view not in {"home", "episodes", "collections", "jobs", "entities", "timeline", "notes", "intelligence", "system"}:
                    raise RuntimeError("Unsupported view")
                action = {"type": "open_view", "arguments": {"view": view}}
            elif internal_name == "audiocodex.ui.create_collection":
                collection_name = str(args.get("name") or "").strip()[:160]
                if not collection_name:
                    raise RuntimeError("Collection name is required")
                action = {"type": "create_collection", "arguments": {
                    "name": collection_name, "description": str(args.get("description") or "").strip()[:2000]
                }}
            elif internal_name == "audiocodex.ui.create_listening_recap":
                episode_id = int(args.get("episode_id"))
                if db is None or not db.one("SELECT id FROM episodes WHERE id=?", (episode_id,)):
                    raise RuntimeError("Episode not found")
                session_id = str(args.get("session_id") or "").strip()[:80] or None
                action = {"type": "create_listening_recap", "arguments": {
                    "episode_id": episode_id, "session_id": session_id
                }}
            elif internal_name == "audiocodex.ui.create_knowledge_watch":
                mode = str(args.get("mode") or "mention").strip().lower()
                scope = str(args.get("scope") or "library").strip().lower()
                query = re.sub(r"\s+", " ", str(args.get("query") or "")).strip()[:1000]
                reference_text = re.sub(r"\s+", " ", str(args.get("reference_text") or "")).strip()[:8000]
                if mode not in {"mention", "topic", "contradiction", "new_episode"}:
                    raise RuntimeError("Unsupported Knowledge Watch mode")
                if scope not in {"library", "episode", "collection", "entity"}:
                    raise RuntimeError("Unsupported Knowledge Watch scope")
                if mode != "new_episode" and not query:
                    raise RuntimeError("Knowledge Watch query is required")
                if mode == "contradiction" and not reference_text:
                    raise RuntimeError("Contradiction Watch requires reference text")
                action = {"type": "create_knowledge_watch", "arguments": {
                    "name": str(args.get("name") or "").strip()[:180], "query": query, "mode": mode, "scope": scope,
                    "episode_id": args.get("episode_id"), "collection_id": args.get("collection_id"),
                    "entity_id": args.get("entity_id"), "reference_text": reference_text,
                }}
            else:
                if db is None:
                    raise RuntimeError("Audio Codex database is unavailable")

                if internal_name == "audiocodex.ui.acknowledge_watch_event":
                    event_id = int(args.get("event_id"))
                    event = db.one(
                        """SELECT kwe.id, kwe.acknowledged, kw.name AS watch_name
                           FROM knowledge_watch_events kwe
                           JOIN knowledge_watches kw ON kw.id=kwe.watch_id
                           WHERE kwe.id=?""", (event_id,)
                    )
                    if not event:
                        raise RuntimeError("Knowledge Watch event not found")
                    action = {"type": "acknowledge_watch_event", "arguments": {"event_id": event_id},
                              "watch_name": event.get("watch_name") or ""}

                elif internal_name == "audiocodex.ui.set_knowledge_watch":
                    watch_id = int(args.get("watch_id"))
                    watch = db.one("SELECT id, name, active FROM knowledge_watches WHERE id=?", (watch_id,))
                    if not watch:
                        raise RuntimeError("Knowledge Watch not found")
                    action = {"type": "set_knowledge_watch", "arguments": {
                        "watch_id": watch_id, "active": bool(args.get("active"))
                    }, "watch_name": watch.get("name") or ""}

                elif internal_name in {"audiocodex.ui.open_entity", "audiocodex.ui.update_entity_annotation"}:
                    entity_id = int(args.get("entity_id"))
                    entity = db.one("SELECT id, canonical_name FROM entities WHERE id=?", (entity_id,))
                    if not entity:
                        raise RuntimeError("Entity not found")
                    if internal_name == "audiocodex.ui.open_entity":
                        action = {"type": "open_entity", "arguments": {"entity_id": entity_id},
                                  "entity_name": entity.get("canonical_name") or ""}
                    else:
                        description = str(args.get("description") or "").strip()[:12000]
                        if not description:
                            raise RuntimeError("Entity annotation is required")
                        action = {"type": "update_entity_annotation", "arguments": {
                            "entity_id": entity_id, "description": description
                        }, "entity_name": entity.get("canonical_name") or ""}

                elif internal_name in {"audiocodex.ui.open_collection", "audiocodex.ui.update_collection"}:
                    collection_id = int(args.get("collection_id"))
                    collection = db.one("SELECT id, name, description FROM collections WHERE id=?", (collection_id,))
                    if not collection:
                        raise RuntimeError("Collection not found")
                    if internal_name == "audiocodex.ui.open_collection":
                        action = {"type": "open_collection", "arguments": {"collection_id": collection_id},
                                  "collection_name": collection.get("name") or ""}
                    else:
                        raw_name = args.get("name")
                        raw_description = args.get("description")
                        new_name = None if raw_name is None else str(raw_name).strip()[:160]
                        new_description = None if raw_description is None else str(raw_description).strip()[:2000]
                        if new_name == "":
                            raise RuntimeError("Collection name cannot be empty")
                        if new_name is None and new_description is None:
                            raise RuntimeError("A collection name or description is required")
                        action = {"type": "update_collection", "arguments": {
                            "collection_id": collection_id, "name": new_name, "description": new_description
                        }, "collection_name": collection.get("name") or ""}

                elif internal_name == "audiocodex.ui.add_to_collection":
                    collection_id = int(args.get("collection_id"))
                    episode_id = int(args.get("episode_id"))
                    collection = db.one("SELECT id, name FROM collections WHERE id=?", (collection_id,))
                    episode = db.one("SELECT id, title FROM episodes WHERE id=?", (episode_id,))
                    if not collection or not episode:
                        raise RuntimeError("Collection or episode not found")
                    action = {"type": "add_to_collection", "arguments": {
                        "collection_id": collection_id, "episode_id": episode_id
                    }, "collection_name": collection.get("name") or "", "episode_title": episode.get("title") or ""}

                elif internal_name == "audiocodex.ui.create_note":
                    episode_id = args.get("episode_id")
                    entity_id = args.get("entity_id")
                    segment_id = args.get("segment_id")
                    episode_id = None if episode_id is None else int(episode_id)
                    entity_id = None if entity_id is None else int(entity_id)
                    segment_id = None if segment_id is None else int(segment_id)
                    episode = db.one("SELECT id, title FROM episodes WHERE id=?", (episode_id,)) if episode_id is not None else None
                    entity = db.one("SELECT id, canonical_name FROM entities WHERE id=?", (entity_id,)) if entity_id is not None else None
                    if episode_id is not None and not episode:
                        raise RuntimeError("Episode not found")
                    if entity_id is not None and not entity:
                        raise RuntimeError("Entity not found")
                    if segment_id is not None and not db.one("SELECT id FROM transcript_segments WHERE id=?", (segment_id,)):
                        raise RuntimeError("Transcript segment not found")
                    body = str(args.get("body") or "").strip()[:12000]
                    if not body:
                        raise RuntimeError("Note body is required")
                    action = {"type": "create_note", "arguments": {
                        "episode_id": episode_id, "entity_id": entity_id, "segment_id": segment_id, "body": body
                    }, "episode_title": (episode or {}).get("title") or "", "entity_name": (entity or {}).get("canonical_name") or ""}

                elif internal_name == "audiocodex.ui.remember_memory":
                    content = re.sub(r"\s+", " ", str(args.get("content") or "")).strip()[:2000]
                    if not content:
                        raise RuntimeError("Memory content is required")
                    kind = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(args.get("kind") or "preference").lower()).strip("-")[:40] or "preference"
                    action = {"type": "remember_memory", "arguments": {"content": content, "kind": kind}}

                elif internal_name == "audiocodex.ui.forget_memory":
                    memory_id = int(args.get("memory_id"))
                    memory = db.one("SELECT id, kind, content FROM ai_memories WHERE id=?", (memory_id,))
                    if not memory:
                        raise RuntimeError("Memory not found")
                    action = {"type": "forget_memory", "arguments": {"memory_id": memory_id},
                              "memory_preview": str(memory.get("content") or "")[:240]}

                else:
                    episode_id = int(args.get("episode_id"))
                    episode = db.one("SELECT id, title FROM episodes WHERE id=?", (episode_id,))
                    if not episode:
                        raise RuntimeError("Episode not found")
                    episode_title = str(episode.get("title") or "")
                    if internal_name == "audiocodex.ui.open_episode":
                        raw_position = args.get("position_ms")
                        position_ms = None if raw_position is None else max(0, int(raw_position))
                        action = {"type": "open_episode", "arguments": {
                            "episode_id": episode_id, "position_ms": position_ms
                        }, "episode_title": episode_title}
                    elif internal_name == "audiocodex.ui.play_audio":
                        action = {"type": "play_audio", "arguments": {
                            "episode_id": episode_id, "position_ms": max(0, int(args.get("position_ms") or 0))
                        }, "episode_title": episode_title}
                    elif internal_name == "audiocodex.ui.create_bookmark":
                        action = {"type": "create_bookmark", "arguments": {
                            "episode_id": episode_id, "position_ms": max(0, int(args.get("position_ms") or 0)),
                            "label": str(args.get("label") or "Bookmark").strip()[:240] or "Bookmark"
                        }, "episode_title": episode_title}
                    elif internal_name == "audiocodex.ui.transcribe_episode":
                        action = {"type": "transcribe_episode", "arguments": {"episode_id": episode_id},
                                  "episode_title": episode_title}
                    elif internal_name == "audiocodex.ui.analyze_episode":
                        action = {"type": "analyze_episode", "arguments": {"episode_id": episode_id},
                                  "episode_title": episode_title}
                    elif internal_name == "audiocodex.ui.generate_captions":
                        action = {"type": "generate_captions", "arguments": {"episode_id": episode_id},
                                  "episode_title": episode_title}
                    else:
                        raise RuntimeError("Unsupported Audio Codex action")

            return {"ok": True, "tool": internal_name, "client_action": action, "data": {
                "status": "awaiting_user_confirmation", "action": action,
                "instruction": "The action is prepared only. The user must confirm it in Audio Codex."
            }}
        except Exception as exc:
            return {"ok": False, "tool": internal_name, "error": str(exc)}
    if db is None:
        return {"ok": False, "tool": internal_name, "error": "Audio Codex database is unavailable"}
    try:
        from .mcp import call_tool
        value = call_tool(db, internal_name, args)
        return {"ok": True, "tool": internal_name, "data": value}
    except Exception as exc:
        return {"ok": False, "tool": internal_name, "error": str(exc)}

def _tool_result_text(result: dict) -> str:
    value = result.get("data") if result.get("ok") else {"error": result.get("error")}
    return json.dumps(value, ensure_ascii=False, default=str)


def _tool_preview(value: Any, limit: int = 2400) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str, indent=2)
    except Exception:
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "\n…"


def _annotate_tool_grounding(
    internal_name: str, result: dict, citation_index: int
) -> tuple[dict, list[dict], int]:
    """Attach stable transcript/web citation keys before tool output reaches the model."""
    if not result.get("ok"):
        return result, [], citation_index

    annotated = copy.deepcopy(result)
    data = annotated.get("data") or {}
    sources: list[dict] = []


    if internal_name not in {"audiocodex.transcript.search", "audiocodex.transcript.read", "audiocodex.listening.rewind"}:
        return result, [], citation_index

    if internal_name == "audiocodex.transcript.search":
        rows = data.get("hits") or []
        for row in rows:
            citation_index += 1
            key = f"T{citation_index}"
            row["citation_key"] = key
            sources.append({
                "type": "transcript", "key": key, "citation": key,
                "segment_id": row.get("segment_id"),
                "segment_index": row.get("segment_index"),
                "episode_id": row.get("episode_id"),
                "episode_title": row.get("episode_title") or "",
                "podcast_id": row.get("podcast_id"),
                "podcast_title": row.get("podcast_title") or "",
                "start_ms": int(row.get("start_ms") or 0),
                "end_ms": int(row.get("end_ms") or 0),
                "speaker_label": row.get("speaker_label") or "",
                "chapter_title": "",
                "text": row.get("text") or "",
            })
    else:
        episode = data.get("episode") or {}
        for row in data.get("segments") or []:
            citation_index += 1
            key = f"T{citation_index}"
            row["citation_key"] = key
            sources.append({
                "type": "transcript", "key": key, "citation": key,
                "segment_id": row.get("segment_id"),
                "segment_index": row.get("segment_index"),
                "episode_id": episode.get("id"),
                "episode_title": episode.get("title") or "",
                "podcast_id": episode.get("podcast_id"),
                "podcast_title": episode.get("podcast_title") or "",
                "start_ms": int(row.get("start_ms") or 0),
                "end_ms": int(row.get("end_ms") or 0),
                "speaker_label": row.get("speaker_label") or "",
                "chapter_title": "",
                "text": row.get("text") or "",
            })
    if sources:
        data["citation_instruction"] = (
            "When using these transcript rows in the answer, cite their citation_key exactly, "
            "for example [T1]."
        )
    return annotated, sources, citation_index


def _estimate_tokens(value: Any) -> int:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    cjk = sum(1 for char in text if "\u3400" <= char <= "\u9fff")
    other = max(0, len(text) - cjk)
    return max(1, int(cjk * 0.65 + other * 0.34) + 8)


@dataclass
class AISettings:
    provider: str = "deepseek"
    base_url: str = DEFAULT_DEEPSEEK_BASE
    api_key: str = ""
    chat_model: str = DEFAULT_DEEPSEEK_MODEL
    vision_model: str = DEFAULT_DEEPSEEK_VISION_MODEL
    vision_detail: str = DEFAULT_VISION_DETAIL
    embedding_model: str = ""
    context_tokens: int = DEFAULT_CONTEXT_TOKENS
    thinking: bool = True
    show_reasoning: bool = False
    strict_tools: bool = False
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    chat_max_tokens: int = DEFAULT_CHAT_MAX_TOKENS
    adaptive_routing: bool = True
    personal_context: bool = True
    conversation_memory: bool = True
    web_search: bool = False
    pro_model: str = DEFAULT_DEEPSEEK_PRO_MODEL

    @classmethod
    def from_db(cls, db: Database) -> "AISettings":
        raw = db.setting("ai", {}) or {}
        provider = str(raw.get("provider") or "deepseek").strip().lower()
        if provider in {"auto", "ollama", "extractive", "local", "anthropic-compatible"}:
            provider = "deepseek"
        if provider not in {"deepseek", "openai-compatible", "disabled", "none"}:
            provider = "deepseek"
        base = str(raw.get("base_url") or "").strip()
        model = str(raw.get("chat_model") or "").strip()
        if provider == "deepseek":
            base = _deepseek_base(base)
            model = model or DEFAULT_DEEPSEEK_MODEL
        vision_model = str(raw.get("vision_model") or "").strip()
        if provider == "deepseek":
            # DeepSeek V4.1 Flash is the current text + vision model. The API still
            # accepts older V4 aliases, but new/default configurations are normalized
            # to the current documented model so the app does not depend on retired IDs.
            if model in {"", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}:
                model = DEFAULT_DEEPSEEK_MODEL
            if vision_model in {"", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}:
                vision_model = DEFAULT_DEEPSEEK_VISION_MODEL
        detail = str(raw.get("vision_detail") or DEFAULT_VISION_DETAIL).strip().lower()
        if detail not in {"low", "high", "original", "auto"}:
            detail = DEFAULT_VISION_DETAIL
        effort = str(raw.get("reasoning_effort") or DEFAULT_REASONING_EFFORT).strip().lower()
        if effort not in {"low", "high", "xhigh", "max"}:
            effort = DEFAULT_REASONING_EFFORT
        try:
            max_tokens = int(raw.get("chat_max_tokens") or DEFAULT_CHAT_MAX_TOKENS)
        except Exception:
            max_tokens = DEFAULT_CHAT_MAX_TOKENS
        return cls(
            provider=provider,
            base_url=base,
            api_key=str(raw.get("api_key") or "").strip(),
            chat_model=model,
            vision_model=vision_model,
            vision_detail=detail,
            embedding_model=str(raw.get("embedding_model") or "").strip(),
            context_tokens=clamp_context_tokens(raw.get("context_tokens")),
            thinking=_option_bool(raw.get("thinking"), True),
            show_reasoning=_option_bool(raw.get("show_reasoning"), False),
            strict_tools=_option_bool(raw.get("strict_tools"), False),
            reasoning_effort=effort,
            chat_max_tokens=max(256, min(MAX_OUTPUT_TOKENS, max_tokens)),
            adaptive_routing=_option_bool(raw.get("adaptive_routing"), True),
            personal_context=_option_bool(raw.get("personal_context"), True),
            conversation_memory=_option_bool(raw.get("conversation_memory"), True),
            # Current DeepSeek Responses compatibility (2026-09-10) ignores built-in web_search.
            # Never expose a capability the provider will silently ignore.
            web_search=False,
            pro_model=(
                (DEFAULT_DEEPSEEK_PRO_MODEL if str(raw.get("pro_model") or "").strip() in
                 {"", "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp"}
                 else str(raw.get("pro_model") or "").strip())
                if provider == "deepseek" else str(raw.get("pro_model") or "").strip()
            ),
        )


@dataclass
class ResolvedChat:
    provider: str
    base_url: str
    model: str
    available: bool
    detail: str = ""


class ChatProvider:
    """DeepSeek/OpenAI-compatible provider adapted from the user's mature reference.

    DeepSeek uses its OpenAI-compatible Chat Completions endpoint. Streaming parses
    reasoning_content, content, tool_calls and usage independently. Tool-call assistant
    history preserves reasoning_content exactly so the next tool round is protocol-valid.
    """

    def __init__(self, settings: AISettings, database: Database | None = None,
                 http_post=None):
        self.s = settings
        self.db = database
        self.http_post = http_post or requests.post
        self._resolved: ResolvedChat | None = None
        self.last_run: dict[str, Any] = {}

    def _endpoint(self, strict_tools: bool | None = None) -> str:
        strict = self.s.strict_tools if strict_tools is None else bool(strict_tools)
        if self.s.provider == "deepseek":
            base = _deepseek_base(self.s.base_url)
            return f"{base}/beta/chat/completions" if strict else f"{base}/chat/completions"
        return _openai_url(self.s.base_url, "chat/completions")

    def resolve(self, refresh: bool = False) -> ResolvedChat:
        if self._resolved is not None and not refresh:
            return self._resolved
        wanted = (self.s.provider or "deepseek").strip().lower()
        if wanted in {"disabled", "none"}:
            self._resolved = ResolvedChat("disabled", "", "", False, "Generative AI is disabled.")
            return self._resolved
        if not self.s.api_key:
            base = _deepseek_base(self.s.base_url) if wanted == "deepseek" else _openai_base(self.s.base_url)
            model = self.s.chat_model or (DEFAULT_DEEPSEEK_MODEL if wanted == "deepseek" else "")
            self._resolved = ResolvedChat(wanted, base, model, False, "API Key is required.")
            return self._resolved
        if wanted == "deepseek":
            base = _deepseek_base(self.s.base_url)
            model = self.s.chat_model or DEFAULT_DEEPSEEK_MODEL
        elif wanted == "openai-compatible":
            base = _openai_base(self.s.base_url)
            model = self.s.chat_model
        else:
            self._resolved = ResolvedChat(wanted, self.s.base_url, self.s.chat_model, False, "Unsupported AI provider.")
            return self._resolved
        if not base or not model:
            self._resolved = ResolvedChat(wanted, base, model, False, "API Base URL and model are required.")
            return self._resolved

        if refresh:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "max_tokens": 8,
            }
            if wanted == "deepseek":
                payload["thinking"] = {"type": "disabled"}
                payload["temperature"] = 0
            try:
                response = self.http_post(
                    self._endpoint(False),
                    headers={
                        "Authorization": f"Bearer {self.s.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=20,
                )
                response.raise_for_status()
                data = response.json()
                if not (data.get("choices") or []):
                    raise RuntimeError("DeepSeek response has no choices")
                self._resolved = ResolvedChat(wanted, base, model, True, "API connected.")
            except Exception as exc:
                self._resolved = ResolvedChat(wanted, base, model, False, str(exc))
        else:
            self._resolved = ResolvedChat(wanted, base, model, True, "API configured.")
        return self._resolved

    @property
    def available(self) -> bool:
        return self.resolve().available

    def status(self, refresh: bool = False) -> dict:
        r = self.resolve(refresh=refresh)
        return {
            "available": r.available,
            "provider": r.provider,
            "base_url": r.base_url,
            "model": r.model,
            "detail": r.detail,
            "vision_model": self.s.vision_model,
            "vision_detail": self.s.vision_detail,
            "vision_enabled": bool(r.available and self.s.vision_model),
            "context_tokens": self.s.context_tokens,
            "api_key_required": r.provider not in {"disabled", "none"},
            "local_llm_enabled": False,
            "transcript_mode": "local",
            "deepseek_protocol": "openai-chat-completions" if r.provider == "deepseek" else "",
            "streaming": True,
            "tool_reasoning_history_enabled": True,
            "max_tool_rounds": MAX_TOOL_ROUNDS,
            "chat_thinking": self.s.thinking,
            "show_reasoning": self.s.show_reasoning,
            "strict_tools": self.s.strict_tools,
            "reasoning_effort": self.s.reasoning_effort,
            "structured_analysis_thinking": False,
            "adaptive_routing": self.s.adaptive_routing,
            "personal_context": self.s.personal_context,
            "conversation_memory": self.s.conversation_memory,
            "web_search": False,
            "web_search_available": False,
            "web_search_detail": "Current DeepSeek Responses compatibility exposes function tools only; built-in web_search is ignored.",
            "pro_model": self.s.pro_model,
        }

    def _request(
        self,
        messages: list[dict],
        *,
        thinking: bool,
        use_tools: bool,
        max_tokens: int,
        temperature: float,
        event_callback: Callable[[dict], None] | None,
        round_index: int,
        require_json: bool = False,
        model_override: str | None = None,
        allow_client_actions: bool = True,
        include_personal_context: bool = True,
        allow_web_search: bool | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> dict:
        r = self.resolve()
        provider_max_tokens = (
            DEEPSEEK_MAX_OUTPUT_TOKENS
            if r.provider == "deepseek"
            else OPENAI_MAX_OUTPUT_TOKENS
        )
        request_model = str(model_override or r.model).strip() or r.model
        payload: dict[str, Any] = {
            "model": request_model,
            "messages": messages,
            "max_tokens": max(256, min(provider_max_tokens, int(max_tokens))),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if require_json:
            # DeepSeek accepts JSON Output together with tools. This removes the
            # prose/Markdown ambiguity that previously broke structured Analyze.
            payload["response_format"] = {"type": "json_object"}
        if use_tools:
            payload["tools"] = _tool_definitions(
                self.s.strict_tools,
                include_client_actions=allow_client_actions,
                include_web_search=bool(r.provider == "deepseek" and self.s.web_search and (True if allow_web_search is None else allow_web_search)),
                include_personal_context=include_personal_context,
                allowed_tool_names=allowed_tool_names,
            )
        if r.provider == "deepseek":
            payload["thinking"] = {"type": "enabled" if thinking else "disabled"}
            if thinking:
                payload["reasoning_effort"] = self.s.reasoning_effort
            else:
                if use_tools:
                    payload["tool_choice"] = "auto"
                payload["temperature"] = float(temperature)
        else:
            if use_tools:
                payload["tool_choice"] = "auto"
            payload["temperature"] = float(temperature)

        started = time.perf_counter()
        try:
            response = self.http_post(
                self._endpoint(self.s.strict_tools),
                headers={
                    "Authorization": f"Bearer {self.s.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                stream=True,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            detail = ""
            resp = getattr(exc, "response", None)
            if resp is not None:
                try:
                    detail = (resp.text or "")[:500]
                except Exception:
                    pass
            raise RuntimeError(f"DeepSeek request failed{': ' + detail if detail else ''}") from exc

        first_delta_at: float | None = None
        last_reasoning_at: float | None = None
        reasoning_parts: list[str] = []
        content_parts: list[str] = []
        tool_accumulator: dict[int, dict] = {}
        finish_reason = None
        usage = None
        chunk_count = 0
        malformed_chunks = 0

        try:
            for raw_line in response.iter_lines(chunk_size=1, decode_unicode=True):
                if raw_line is None:
                    continue
                line = raw_line.decode("utf-8", "replace") if isinstance(raw_line, bytes) else str(raw_line)
                line = line.strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data_text = line[5:].strip()
                if data_text == "[DONE]":
                    break
                if not data_text:
                    continue
                try:
                    chunk = json.loads(data_text)
                except json.JSONDecodeError:
                    malformed_chunks += 1
                    continue
                if chunk.get("usage") is not None:
                    usage = chunk.get("usage")
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                chunk_count += 1
                now = time.perf_counter()
                if first_delta_at is None:
                    first_delta_at = now
                choice = choices[0]
                if choice.get("finish_reason") is not None:
                    finish_reason = choice.get("finish_reason")
                delta = choice.get("delta") or {}

                reasoning_delta = delta.get("reasoning_content")
                if reasoning_delta:
                    text = str(reasoning_delta)
                    reasoning_parts.append(text)
                    last_reasoning_at = now
                    event = _event("reasoning", text=text, delta=True, round=round_index + 1,
                                   elapsed_seconds=round(now - started, 3))
                    _emit(event_callback, event)

                content_delta = delta.get("content")
                if content_delta:
                    text = str(content_delta)
                    content_parts.append(text)
                    event = _event("content_delta", text=text, round=round_index + 1,
                                   elapsed_seconds=round(now - started, 3))
                    _emit(event_callback, event)

                for raw_tool_delta in delta.get("tool_calls") or []:
                    try:
                        index = int(raw_tool_delta.get("index", 0))
                    except Exception:
                        index = 0
                    entry = tool_accumulator.setdefault(index, {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    })
                    call_id = raw_tool_delta.get("id")
                    if call_id:
                        if not entry["id"]:
                            entry["id"] = str(call_id)
                        elif str(call_id) != entry["id"]:
                            entry["id"] += str(call_id)
                    if raw_tool_delta.get("type"):
                        entry["type"] = raw_tool_delta["type"]
                    function_delta = raw_tool_delta.get("function") or {}
                    if function_delta.get("name"):
                        entry["function"]["name"] += str(function_delta["name"])
                    if function_delta.get("arguments"):
                        entry["function"]["arguments"] += str(function_delta["arguments"])
                    _emit(event_callback, _event(
                        "tool_delta", index=index, id=entry["id"],
                        name=entry["function"]["name"],
                        arguments=entry["function"]["arguments"],
                        round=round_index + 1,
                    ))
        finally:
            try:
                response.close()
            except Exception:
                pass

        elapsed = time.perf_counter() - started
        reasoning_seconds = (last_reasoning_at - started) if last_reasoning_at is not None else 0.0
        metrics = {
            "upstream_stream": True,
            "elapsed_seconds": round(elapsed, 3),
            "reasoning_seconds": round(reasoning_seconds, 3),
            "first_delta_seconds": round(
                (first_delta_at - started) if first_delta_at is not None else elapsed, 3
            ),
            "chunk_count": chunk_count,
            "malformed_chunks": malformed_chunks,
            "finish_reason": finish_reason,
            "usage": usage,
            "reasoning_chars": len("".join(reasoning_parts)),
            "content_chars": len("".join(content_parts)),
        }
        _emit(event_callback, _event("upstream_done", round=round_index + 1, **metrics))
        return {
            "role": "assistant",
            "content": "".join(content_parts),
            "reasoning_content": "".join(reasoning_parts),
            "tool_calls": [tool_accumulator[i] for i in sorted(tool_accumulator)],
            "_stream_metrics": metrics,
        }

    def stream_reply(
        self,
        system: str,
        user: Any,
        *,
        history: list[dict] | None = None,
        model_override: str | None = None,
        thinking: bool | None = None,
        use_tools: bool = True,
        allow_client_actions: bool = True,
        include_personal_context: bool = True,
        allow_web_search: bool | None = None,
        allowed_tool_names: set[str] | None = None,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
        event_callback: Callable[[dict], None] | None = None,
        require_json: bool = False,
    ) -> Generator[dict, None, None]:
        r = self.resolve()
        if not r.available:
            yield _event("error", message=r.detail or "No remote AI provider is available")
            yield _event("done", tools_used=[])
            return
        thinking_enabled = self.s.thinking if thinking is None else bool(thinking)
        max_tokens = max_output_tokens or self.s.chat_max_tokens
        request_model = str(model_override or r.model).strip() or r.model
        messages: list[dict] = [{"role": "system", "content": system}]
        for item in history or []:
            if item.get("role") in {"user", "assistant"} and str(item.get("content") or "").strip():
                messages.append({"role": item["role"], "content": str(item["content"])})
        messages.append({"role": "user", "content": user})
        tools_used: list[str] = []
        tool_sources: list[dict] = []
        client_actions: list[dict] = []
        tool_results: list[dict] = []
        citation_index = 0
        rounds: list[dict] = []
        started = time.perf_counter()
        yield _event("start", model=request_model, thinking=thinking_enabled, provider=r.provider)

        for round_index in range(MAX_TOOL_ROUNDS):
            try:
                assistant = self._request(
                    messages,
                    thinking=thinking_enabled,
                    use_tools=use_tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    event_callback=event_callback,
                    round_index=round_index,
                    require_json=require_json,
                    model_override=request_model,
                    allow_client_actions=allow_client_actions,
                    include_personal_context=include_personal_context,
                    allow_web_search=allow_web_search,
                    allowed_tool_names=allowed_tool_names,
                )
            except Exception as exc:
                yield _event("error", message=str(exc))
                yield _event("done", tools_used=tools_used,
                             elapsed_seconds=round(time.perf_counter() - started, 3),
                             rounds=rounds)
                return
            metrics = assistant.pop("_stream_metrics", {}) or {}
            rounds.append({"round": round_index + 1, **metrics})
            content = str(assistant.get("content") or "")
            reasoning = str(assistant.get("reasoning_content") or "")
            tool_calls = assistant.get("tool_calls") or []

            if reasoning and self.s.show_reasoning:
                yield _event("reasoning_complete", text=reasoning, round=round_index + 1)

            if not tool_calls:
                if content.strip():
                    yield _event("text", text=content)
                    self.last_run = {
                        "rounds": rounds,
                        "tools_used": tools_used,
                        "tool_sources": tool_sources,
                        "actions": client_actions,
                        "tool_results": tool_results,
                        "model": request_model,
                        "thinking": thinking_enabled,
                        "content_chars": len(content),
                        "reasoning_chars": sum(int(x.get("reasoning_chars") or 0) for x in rounds),
                    }
                    yield _event("done", tools_used=tools_used,
                                 elapsed_seconds=round(time.perf_counter() - started, 3),
                                 rounds=rounds)
                    return

                finish = metrics.get("finish_reason")
                usage = metrics.get("usage") or {}
                completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                message = (
                    "DeepSeek returned no final text. "
                    f"finish_reason={finish or 'unknown'}, completion_tokens={completion}, "
                    f"reasoning_chars={len(reasoning)}."
                )
                yield _event("error", message=message, diagnostics=metrics)
                yield _event("done", tools_used=tools_used,
                             elapsed_seconds=round(time.perf_counter() - started, 3),
                             rounds=rounds)
                return

            assistant_tool_message = {
                "role": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            }
            if thinking_enabled and assistant.get("reasoning_content") is not None:
                assistant_tool_message["reasoning_content"] = assistant.get("reasoning_content")
            messages.append(assistant_tool_message)

            for index, call in enumerate(tool_calls, start=1):
                call_id = str(call.get("id") or f"tool-{round_index + 1}-{index}")
                function = call.get("function") or {}
                api_name = str(function.get("name") or "")
                name = _internal_tool_name(api_name)
                raw_arguments = function.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else dict(raw_arguments or {})
                except Exception:
                    arguments = {}
                tools_used.append(name)
                yield _event("tool_start", id=call_id, name=name, arguments=arguments,
                             model_round=round_index + 1, tool_index=index, tool_total=len(tool_calls))
                tool_started = time.perf_counter()
                result = _execute_tool(self.db, name, arguments)
                tool_results.append(result)
                model_result, grounded, citation_index = _annotate_tool_grounding(
                    name, result, citation_index
                )
                tool_sources.extend(grounded)
                if result.get("client_action"):
                    action = result["client_action"]
                    signature = json.dumps(action, ensure_ascii=False, sort_keys=True, default=str)
                    if all(
                        json.dumps(existing, ensure_ascii=False, sort_keys=True, default=str) != signature
                        for existing in client_actions
                    ):
                        client_actions.append(action)
                yield _event(
                    "tool_end", id=call_id, name=name, success=bool(result.get("ok")),
                    error=result.get("error"), result_preview=_tool_preview(model_result.get("data") if result.get("ok") else result),
                    duration_ms=round((time.perf_counter() - tool_started) * 1000, 2),
                    model_round=round_index + 1, tool_index=index, tool_total=len(tool_calls),
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _tool_result_text(model_result),
                })

        self.last_run = {
            "rounds": rounds, "tools_used": tools_used, "tool_sources": tool_sources,
            "actions": client_actions, "tool_results": tool_results, "model": request_model, "thinking": thinking_enabled,
        }
        yield _event("error", message=f"Tool call limit reached after {MAX_TOOL_ROUNDS} rounds")
        yield _event("done", tools_used=tools_used,
                     elapsed_seconds=round(time.perf_counter() - started, 3), rounds=rounds)

    def chat(
        self,
        system: str,
        user: Any,
        temperature: float = 0.2,
        *,
        history: list[dict] | None = None,
        model_override: str | None = None,
        json_mode: bool = False,
        max_output_tokens: int = DEFAULT_ANALYZE_MAX_TOKENS,
        event_callback: Callable[[dict], None] | None = None,
        thinking: bool | None = None,
        use_tools: bool = True,
        allow_client_actions: bool = True,
        include_personal_context: bool = True,
        allow_web_search: bool | None = None,
        allowed_tool_names: set[str] | None = None,
    ) -> str:
        # Structured Analyze uses both thinking=False and JSON Output. Thinking control
        # prevents reasoning from consuming the full output budget; response_format
        # prevents prose/Markdown from making the JSON stage unparsable.
        parts: list[str] = []
        error: str | None = None
        for event in self.stream_reply(
            system,
            user,
            history=history,
            model_override=model_override,
            thinking=thinking,
            use_tools=use_tools,
            allow_client_actions=allow_client_actions,
            include_personal_context=include_personal_context,
            allow_web_search=allow_web_search,
            allowed_tool_names=allowed_tool_names,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            event_callback=event_callback,
            require_json=json_mode,
        ):
            if event_callback:
                _emit(event_callback, event)
            if event.get("type") == "text":
                parts.append(str(event.get("text") or ""))
            elif event.get("type") == "error":
                error = str(event.get("message") or "AI request failed")
        text = "".join(parts).strip()
        if not text:
            raise RuntimeError(error or "AI endpoint returned no final text")
        return text


class EmbeddingProvider:
    def __init__(self, settings: AISettings):
        self.s = settings

    @property
    def available(self) -> bool:
        return bool(
            self.s.provider == "openai-compatible"
            and self.s.api_key
            and self.s.base_url
            and self.s.embedding_model
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or not self.available:
            return []
        data = _request_json(
            _openai_url(self.s.base_url, "embeddings"),
            payload={"model": self.s.embedding_model, "input": texts},
            headers={"Authorization": f"Bearer {self.s.api_key}"},
            timeout=900,
        )
        rows = sorted(data.get("data", []), key=lambda x: x.get("index", 0))
        return [row["embedding"] for row in rows if isinstance(row, dict) and "embedding" in row]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
