from __future__ import annotations
import os

import json
import queue
import threading
import hashlib
import importlib.util
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from .analysis import analyze_episode
from .config import HOST, PORT, PATHS
from .db import db
from .importer import import_paths, normalize_existing_in_our_time_titles
from .media import extract_artwork, image_media_type
from .jobs import (create_job, submit, update_job, raise_if_cancelled, cancel_job, delete_job, clear_history)
from .library import list_episodes, episode_detail, list_collections, collection_episodes
from .intelligence import answer as intelligence_answer, conversation_messages, delete_conversation
from .ai import AISettings, ChatProvider, CLIENT_ACTION_TOOLS
from .mcp import TOOLS as MCP_TOOLS, DEFAULT_CONTEXT_TOKENS, jsonrpc as mcp_jsonrpc
from .search import hybrid_search
from .streaming import range_response
from .r12 import (
    schema_manifest as r12_schema_manifest, touch_listening_session, current_listening_session,
    listening_rewind, generate_listening_recap, list_listening_recaps, provenance_get,
    record_episode_summary_provenance, create_watch, set_watch_active, list_watches,
    list_watch_events, acknowledge_watch_event, evaluate_watches_for_episode,
)
from .transcription import (
    transcribe_episode, generate_captions_episode, continuous_text_from_segments,
    windows_ai_bridge_path, windows_ai_host_path,
)
from .types import (
    AnalyzeRequest, BookmarkCreate, ChatRequest, CollectionCreate,
    CollectionEpisodeUpdate, CollectionUpdate, EntityUpdate, ImportRequest, NoteCreate,
    PlayheadRequest, SegmentCorrection, SettingsUpdate, TranscribeRequest,
    ReadingItemCreate, IntelligenceMemoryCreate, IntelligenceActionReceipt,
    ListeningSessionTouch, ListeningRecapRequest, KnowledgeWatchCreate, KnowledgeWatchUpdate,
)

app = FastAPI(title="Audio Codex", version="1.2.1")
# The UI is served same-origin from "/", so the only cross-origin caller is a local dev
# server. The literal origin "null" is deliberately not allowed: it matches file:// pages
# and sandboxed iframes, which any website can create, and this API has no authentication
# of its own to fall back on.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


UI_FILE = Path(__file__).resolve().parents[2] / "ui" / "index.html"
MIC_ASSET = Path(__file__).resolve().parents[2] / "ui" / "assets" / "mic-mark.png"

# Normalize matching In Our Time filenames without touching other titles.
normalize_existing_in_our_time_titles(db)


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, AttributeError):
        return False


def _windows_speech_status() -> dict:
    if sys.platform != "win32":
        return {"windows_build": 0, "system_speech": False, "recognizers": [],
                "package_identity_registered": False, "windows_ai_type_visible": False,
                "windows_ai_host_built": False, "windows_ai_available": False,
                "windows_ai_ready": False, "windows_ai_ready_state": "", "developer_mode": False,
                "windows_ai_engine": "", "windows_ai_error": ""}
    script = Path(__file__).resolve().parents[2] / "windows" / "WindowsSpeechStatus.ps1"
    if not script.exists():
        return {"windows_build": 0, "system_speech": False, "recognizers": [],
                "package_identity_registered": False, "windows_ai_type_visible": False,
                "windows_ai_host_built": False, "windows_ai_available": False,
                "windows_ai_ready": False, "windows_ai_ready_state": "", "developer_mode": False,
                "windows_ai_engine": "", "windows_ai_error": ""}
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(script)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=12,
        )
        for line in reversed((proc.stdout or "").splitlines()):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
    except Exception:
        pass
    return {"windows_build": 0, "system_speech": False, "recognizers": [],
            "package_identity_registered": False, "windows_ai_type_visible": False,
            "windows_ai_host_built": False, "windows_ai_available": False,
            "windows_ai_ready": False, "windows_ai_ready_state": "", "developer_mode": False,
            "windows_ai_engine": "", "windows_ai_error": ""}



def _known_windows_speech_packs(recognizers: list[dict]) -> list[dict]:
    targets = [
        ("zh-CN", "MS-2052-80-DESK", "Chinese (Simplified)"),
        ("en-US", "MS-1033-80-DESK", "English (United States)"),
        ("zh-TW", "MS-1028-80-DESK", "Chinese (Traditional)"),
        ("es-ES", "MS-3082-80-DESK", "Spanish (Spain)"),
    ]
    by_id = {str(x.get("id") or "").casefold(): x for x in recognizers}
    by_lang = {str(x.get("language") or "").casefold(): x for x in recognizers}
    out = []
    for language, rid, label in targets:
        found = by_id.get(rid.casefold()) or by_lang.get(language.casefold())
        out.append({
            "language": language,
            "id": rid,
            "label": label,
            "installed": bool(found),
            "name": (found or {}).get("name") or "",
            "detected_id": (found or {}).get("id") or "",
        })
    return out

def _powershell_json(script: str):
    if sys.platform != "win32":
        raise HTTPException(400, "Native picker is only available on Windows")
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
    )
    if proc.returncode != 0:
        raise HTTPException(500, proc.stderr.strip() or "Windows picker failed")
    raw = proc.stdout.strip()
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except Exception:
        return []
    return value if isinstance(value, list) else [value]


@app.get("/", include_in_schema=False)
def web_ui():
    if not UI_FILE.exists():
        return PlainTextResponse("Audio Codex UI file is missing", status_code=500)
    return FileResponse(
        UI_FILE,
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/ui-assets/mic-mark.png", include_in_schema=False)
def mic_asset():
    if not MIC_ASSET.exists():
        raise HTTPException(404, "UI asset not found")
    return FileResponse(MIC_ASSET, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/system")
def system_status():
    speech = _windows_speech_status()
    return {
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "ffmpeg": shutil.which("ffmpeg") or "",
        "ffprobe": shutil.which("ffprobe") or "",
        "faster_whisper": _has_module("faster_whisper"),
        "pyannote": _has_module("pyannote.audio"),
        "windows_build": speech.get("windows_build", 0),
        "windows_system_speech": bool(speech.get("system_speech")),
        "windows_speech_recognizers": speech.get("recognizers") or [],
        "windows_ai_bridge": bool(windows_ai_bridge_path()) and bool(speech.get("windows_ai_available")),
        "windows_ai_bridge_path": str(windows_ai_bridge_path() or ""),
        "windows_ai_host_path": str(windows_ai_host_path() or ""),
        "windows_ai_host_built": bool(speech.get("windows_ai_host_built")) or bool(windows_ai_host_path()),
        "windows_ai_available": bool(speech.get("windows_ai_available")),
        "windows_ai_ready": bool(speech.get("windows_ai_ready")),
        "windows_ai_ready_state": str(speech.get("windows_ai_ready_state") or ""),
        "windows_ai_engine": str(speech.get("windows_ai_engine") or ""),
        "windows_ai_error": str(speech.get("windows_ai_error") or ""),
        "windows_developer_mode": bool(speech.get("developer_mode")),
        "windows_package_identity_registered": bool(speech.get("package_identity_registered")),
        "windows_ai_type_visible": bool(speech.get("windows_ai_type_visible")),
        "windows_speech_packs": _known_windows_speech_packs(speech.get("recognizers") or []),
        "windows_effective_speech_engine": (
            # Windows AI Speech only works inside the registered package identity that grants
            # systemAIModels; without it the cascade falls through to the other local engines.
            "windows-ai-speech" if speech.get("windows_ai_ready") and speech.get("package_identity_registered")
            else "faster-whisper" if _has_module("faster_whisper")
            else "windows-system-speech" if speech.get("system_speech")
            else "windows-ai-speech-model-needed" if speech.get("windows_ai_available")
            else "unavailable"
        ),
    }


def _run_windows_ai_control(*args: str, timeout: int = 7200) -> dict:
    if sys.platform != "win32":
        raise RuntimeError("Windows AI Speech is only available on Windows.")
    script = windows_ai_bridge_path()
    if not script:
        raise RuntimeError("Windows AI Speech launcher is missing.")
    proc = subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-File", str(script), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )
    payload = None
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    if payload is None:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or
                           "Windows AI Speech returned no JSON result.")
    return payload


@app.post("/api/system/windows-ai-speech/ensure")
def ensure_windows_ai_speech():
    if sys.platform != "win32":
        raise HTTPException(400, "Windows AI Speech is only available on Windows")
    speech = _windows_speech_status()
    if not speech.get("package_identity_registered"):
        raise HTTPException(409, "Audio Codex Windows speech package identity is not registered. Repair the installation first.")
    job_id = create_job("windows-ai-speech-model")

    def work():
        update_job(job_id, progress=.08, message="Preparing Windows AI speech recognition model through Windows Update")
        payload = _run_windows_ai_control("-Ensure", timeout=7200)
        if not payload.get("ok"):
            raise RuntimeError(str(payload.get("error") or "Windows AI speech model preparation failed"))
        update_job(job_id, progress=.98, message="Windows AI speech recognition model is ready")

    submit(job_id, work)
    return {"job_id": job_id}


@app.get("/api/ai/status")
def ai_status(refresh: bool = False):
    settings = AISettings.from_db(db)
    provider = ChatProvider(settings)
    return {
        **provider.status(refresh=refresh),
        "mcp_context_tokens": settings.context_tokens,
        "mcp_default_context_tokens": DEFAULT_CONTEXT_TOKENS,
        "mcp_tools": [tool["name"] for tool in MCP_TOOLS],
        "agent_actions": [tool["name"] for tool in CLIENT_ACTION_TOOLS],
        "mcp_endpoint": "/mcp",
        "intelligence_layer": True,
        "on_screen_context": True,
        "local_conversation_store": settings.conversation_memory,
        "personal_context": settings.personal_context,
        "adaptive_routing": settings.adaptive_routing,
        "web_search": False,
        "web_search_available": False,
        "web_search_detail": "Current DeepSeek Responses compatibility ignores built-in web_search; Audio Codex does not expose it as an Agent capability.",
        "pro_model": settings.pro_model,
    }


@app.post("/mcp")
async def mcp_endpoint(request: Request):
    payload = await request.json()
    if isinstance(payload, list):
        responses = [
            mcp_jsonrpc(db, item)
            for item in payload
            if isinstance(item, dict)
        ]
        return [item for item in responses if item is not None]
    if not isinstance(payload, dict):
        raise HTTPException(400, "MCP request must be a JSON object or batch")
    response = mcp_jsonrpc(db, payload)
    return response or {"jsonrpc": "2.0", "result": {}}



@app.post("/api/pick-files")
def pick_files():
    script = r'''
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.Show()
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.Title = 'Import audio'
$d.Multiselect = $true
$d.Filter = 'Audio|*.mp3;*.m4a;*.aac;*.flac;*.ogg;*.wav;*.opus;*.mp4|All files|*.*'
$result = $d.ShowDialog($owner)
$owner.Close()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
  @($d.FileNames) | ConvertTo-Json -Compress
}
'''
    return _powershell_json(script)


@app.post("/api/pick-folder")
def pick_folder():
    script = r'''
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.Opacity = 0
$owner.Show()
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = 'Select podcast folder'
$d.ShowNewFolderButton = $false
$result = $d.ShowDialog($owner)
$owner.Close()
if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
  @($d.SelectedPath) | ConvertTo-Json -Compress
}
'''
    return _powershell_json(script)


@app.get("/api/health")
def health():
    return {"ok": True, "version": "1.2.1", "home": str(PATHS.home),
        "build": os.environ.get("AUDIO_CODEX_BUILD_ID", "1.2.1-R12.1-AUDIO-INTELLIGENCE-WINDOWSAISPEECH1"),
    }
@app.get("/api/overview")
def overview():
    return {
        "episodes": db.one("SELECT COUNT(*) AS n FROM episodes")["n"],
        "podcasts": db.one("SELECT COUNT(*) AS n FROM podcasts")["n"],
        "transcribed": db.one("SELECT COUNT(*) AS n FROM episodes WHERE transcript_status='ready'")["n"],
        "unfinished": db.one(
            "SELECT COUNT(*) AS n FROM episodes WHERE completed=0 AND playhead_ms>0"
        )["n"],
        "recent": list_episodes(db, mode="recent")[:6],
        "intelligence": {
            "active_watches": int((db.one("SELECT COUNT(*) AS n FROM knowledge_watches WHERE active=1") or {}).get("n") or 0),
            "unread_watch_events": int((db.one("SELECT COUNT(*) AS n FROM knowledge_watch_events WHERE acknowledged=0") or {}).get("n") or 0),
            "listening_recaps": int((db.one("SELECT COUNT(*) AS n FROM listening_recaps") or {}).get("n") or 0),
        },
    }


@app.post("/api/import")
def import_audio(req: ImportRequest):
    result = import_paths(db, req.paths, req.podcast_title)
    for episode_id in result.get("new_episode_ids") or []:
        try:
            evaluate_watches_for_episode(db, int(episode_id))
        except Exception:
            pass
    return result


@app.get("/api/podcasts")
def podcasts():
    rows = db.all(
        """SELECT p.*, COUNT(e.id) AS episode_count
           FROM podcasts p LEFT JOIN episodes e ON e.podcast_id=p.id
           GROUP BY p.id ORDER BY p.title"""
    )
    return rows


@app.get("/api/episodes")
def episodes(mode: str | None = None, podcast_id: int | None = None, sort: str | None = None):
    return list_episodes(db, mode=mode, podcast_id=podcast_id, sort=sort)


@app.get("/api/episodes/{episode_id}")
def get_episode(episode_id: int):
    row = episode_detail(db, episode_id)
    if not row:
        raise HTTPException(404, "Episode not found")
    return row


@app.delete("/api/episodes/{episode_id}")
def delete_episode(episode_id: int):
    ep = db.one("SELECT id, podcast_id FROM episodes WHERE id=?", (episode_id,))
    if not ep:
        raise HTTPException(404, "Episode not found")
    db.execute("DELETE FROM episodes WHERE id=?", (episode_id,))
    remaining = db.one("SELECT COUNT(*) AS n FROM episodes WHERE podcast_id=?", (ep["podcast_id"],))
    if remaining and int(remaining["n"] or 0) == 0:
        db.execute("DELETE FROM podcasts WHERE id=?", (ep["podcast_id"],))
    return {"ok": True}


@app.get("/api/episodes/{episode_id}/transcript")
def transcript(episode_id: int):
    return db.all(
        """SELECT id, episode_id, segment_index, start_ms, end_ms,
                  COALESCE(user_speaker_label, speaker_label, '') AS speaker_label,
                  COALESCE(user_text, text) AS text,
                  confidence, user_text, user_speaker_label
           FROM transcript_segments
           WHERE episode_id=? ORDER BY segment_index""",
        (episode_id,),
    )


@app.get("/api/episodes/{episode_id}/transcript/text")
def transcript_text(episode_id: int):
    rows = db.all(
        """SELECT start_ms, end_ms, COALESCE(user_text, text) AS text
           FROM transcript_segments WHERE episode_id=? ORDER BY segment_index""",
        (episode_id,),
    )
    return {"text": continuous_text_from_segments(rows), "segment_count": len(rows)}


@app.get("/api/episodes/{episode_id}/captions")
def captions(episode_id: int):
    return db.all(
        """SELECT id, episode_id, segment_index, start_ms, end_ms, text, confidence
           FROM caption_segments WHERE episode_id=? ORDER BY segment_index""",
        (episode_id,),
    )


@app.post("/api/episodes/{episode_id}/captions/generate")
def generate_captions(episode_id: int, req: TranscribeRequest):
    if not db.one("SELECT id FROM episodes WHERE id=?", (episode_id,)):
        raise HTTPException(404, "Episode not found")
    settings = db.setting("transcription", {}) or {}
    provider = req.provider or settings.get("provider", "windows-ai")
    model = req.model or settings.get("model", "base")
    language = req.language if req.language is not None else settings.get("language")
    recognizer_id = req.recognizer_id if req.recognizer_id is not None else settings.get("recognizer_id")
    job_id = create_job("captions", episode_id)

    def work():
        def progress(p, message):
            raise_if_cancelled(job_id)
            update_job(job_id, progress=p, message=message)
        generate_captions_episode(
            db, episode_id, provider, model, language, recognizer_id=recognizer_id, progress=progress
        )
    submit(job_id, work)
    return {"job_id": job_id}


@app.post("/api/episodes/{episode_id}/transcribe")
def transcribe(episode_id: int, req: TranscribeRequest):
    if not db.one("SELECT id FROM episodes WHERE id=?", (episode_id,)):
        raise HTTPException(404, "Episode not found")
    settings = db.setting("transcription", {}) or {}
    provider = req.provider or settings.get("provider", "windows-ai")
    model = req.model or settings.get("model", "base")
    language = req.language if req.language is not None else settings.get("language")
    recognizer_id = req.recognizer_id if req.recognizer_id is not None else settings.get("recognizer_id")
    diarize_enabled = req.diarize if req.diarize is not None else bool(settings.get("diarize", False))
    token = settings.get("hf_token") or None
    job_id = create_job("transcribe", episode_id)

    def work():
        def progress(p, message):
            raise_if_cancelled(job_id)
            update_job(job_id, progress=p, message=message)
        transcribe_episode(
            db, episode_id, provider, model, language,
            diarize_enabled, token, recognizer_id=recognizer_id, progress=progress
        )
        try:
            evaluate_watches_for_episode(db, episode_id)
        except Exception:
            pass
    submit(job_id, work)
    return {"job_id": job_id}


@app.post("/api/episodes/{episode_id}/analyze")
def analyze(episode_id: int, req: AnalyzeRequest):
    if not db.one("SELECT id FROM episodes WHERE id=?", (episode_id,)):
        raise HTTPException(404, "Episode not found")
    job_id = create_job("analyze", episode_id)

    def work():
        def progress(p, message):
            raise_if_cancelled(job_id)
            update_job(job_id, progress=p, message=message)
        analyze_episode(db, episode_id, generate_embeddings=req.embeddings, progress=progress)
        try:
            ai_settings = AISettings.from_db(db)
            record_episode_summary_provenance(db, episode_id, model=ai_settings.chat_model)
        except Exception:
            pass
        try:
            evaluate_watches_for_episode(db, episode_id)
        except Exception:
            pass
    submit(job_id, work)
    return {"job_id": job_id}


@app.patch("/api/episodes/{episode_id}/playhead")
def set_playhead(episode_id: int, req: PlayheadRequest):
    completed = req.completed
    if completed is None:
        ep = db.one("SELECT duration_ms FROM episodes WHERE id=?", (episode_id,))
        if not ep:
            raise HTTPException(404, "Episode not found")
        completed = bool(ep["duration_ms"] and req.position_ms >= ep["duration_ms"] * 0.96)
    db.execute(
        """UPDATE episodes
           SET playhead_ms=?, completed=?, last_played_at=?
           WHERE id=?""",
        (
            req.position_ms, 1 if completed else 0,
            datetime.now(timezone.utc).isoformat(), episode_id,
        ),
    )
    return {"ok": True, "completed": completed}


@app.patch("/api/segments/{segment_id}")
def correct_segment(segment_id: int, req: SegmentCorrection):
    row = db.one("SELECT * FROM transcript_segments WHERE id=?", (segment_id,))
    if not row:
        raise HTTPException(404, "Segment not found")
    if req.text is not None:
        db.execute("UPDATE transcript_segments SET user_text=? WHERE id=?", (req.text, segment_id))
        db.update_segment_fts(segment_id, req.text)
    if req.speaker_label is not None:
        if req.apply_speaker_to_episode and row.get("speaker_label"):
            db.execute(
                """UPDATE transcript_segments
                   SET user_speaker_label=?
                   WHERE episode_id=? AND speaker_label=?""",
                (req.speaker_label, row["episode_id"], row["speaker_label"]),
            )
        else:
            db.execute(
                "UPDATE transcript_segments SET user_speaker_label=? WHERE id=?",
                (req.speaker_label, segment_id),
            )
    return {"ok": True}


@app.get("/api/entities")
def entities(entity_type: str | None = None, query: str | None = None):
    sql = """
      SELECT en.*, COUNT(DISTINCT ee.episode_id) AS episode_count,
             COUNT(ee.segment_id) AS mention_count
      FROM entities en LEFT JOIN episode_entities ee ON ee.entity_id=en.id
      WHERE 1=1
    """
    args: list[Any] = []
    if entity_type:
        sql += " AND en.type=?"
        args.append(entity_type.upper())
    if query:
        sql += " AND en.canonical_name LIKE ?"
        args.append(f"%{query}%")
    sql += " GROUP BY en.id ORDER BY episode_count DESC, en.canonical_name LIMIT 500"
    return db.all(sql, args)


@app.get("/api/entities/{entity_id}")
def entity(entity_id: int):
    row = db.one("SELECT * FROM entities WHERE id=?", (entity_id,))
    if not row:
        raise HTTPException(404, "Entity not found")
    row["mentions"] = db.all(
        """SELECT DISTINCT ee.segment_id, e.id AS episode_id, s.start_ms, s.end_ms,
                  COALESCE(s.user_text, s.text, '') AS text,
                  e.title AS episode_title, p.title AS podcast_title
           FROM episode_entities ee
           JOIN episodes e ON e.id=ee.episode_id
           JOIN podcasts p ON p.id=e.podcast_id
           LEFT JOIN transcript_segments s ON s.id=ee.segment_id
           WHERE ee.entity_id=?
           ORDER BY e.imported_at DESC, s.start_ms
           LIMIT 300""",
        (entity_id,),
    )
    row["relations"] = db.all(
        """SELECT r.relation, e.id, e.type, e.canonical_name
           FROM entity_relations r JOIN entities e ON e.id=r.target_entity_id
           WHERE r.source_entity_id=? ORDER BY r.relation, e.canonical_name""",
        (entity_id,),
    )
    row["notes"] = db.all(
        "SELECT * FROM notes WHERE entity_id=? ORDER BY created_at DESC", (entity_id,)
    )
    return row


@app.patch("/api/entities/{entity_id}")
def update_entity(entity_id: int, req: EntityUpdate):
    row = db.one("SELECT * FROM entities WHERE id=?", (entity_id,))
    if not row:
        raise HTTPException(404, "Entity not found")
    if req.canonical_name:
        db.execute(
            "UPDATE entities SET canonical_name=?, normalized_key=? WHERE id=?",
            (req.canonical_name, req.canonical_name.casefold().strip(), entity_id),
        )
    if req.description is not None:
        db.execute(
            "UPDATE entities SET user_description=? WHERE id=?",
            (req.description, entity_id),
        )
    if req.entity_type:
        typ = req.entity_type.upper()
        if typ not in {"PERSON", "TOPIC", "WORK", "PLACE", "EVENT", "ERA"}:
            raise HTTPException(400, "Unsupported entity type")
        db.execute("UPDATE entities SET type=? WHERE id=?", (typ, entity_id))
    return {"ok": True}


@app.get("/api/collections")
def collections():
    return list_collections(db)


@app.post("/api/collections")
def create_collection(req: CollectionCreate):
    try:
        cid = db.execute(
            """INSERT INTO collections(name, description, is_smart, query_json)
               VALUES(?, ?, ?, ?)""",
            (req.name.strip(), req.description, 1 if req.is_smart else 0,
             json.dumps(req.query, ensure_ascii=False)),
        )
    except Exception as exc:
        raise HTTPException(400, str(exc))
    return {"id": cid}


@app.get("/api/collections/{collection_id}")
def get_collection(collection_id: int):
    row = db.one("SELECT * FROM collections WHERE id=?", (collection_id,))
    if not row:
        raise HTTPException(404, "Collection not found")
    row["episode_count"] = db.one(
        "SELECT COUNT(*) AS n FROM collection_episodes WHERE collection_id=?",
        (collection_id,),
    )["n"] if not row["is_smart"] else len(collection_episodes(db, collection_id))
    try:
        row["query"] = json.loads(row.pop("query_json") or "{}")
    except Exception:
        row["query"] = {}
    return row


@app.patch("/api/collections/{collection_id}")
def update_collection(collection_id: int, req: CollectionUpdate):
    row = db.one("SELECT * FROM collections WHERE id=?", (collection_id,))
    if not row:
        raise HTTPException(404, "Collection not found")
    parts = []
    args = []
    if req.name is not None:
        name = req.name.strip()
        if not name:
            raise HTTPException(400, "Collection name cannot be empty")
        parts.append("name=?")
        args.append(name)
    if req.description is not None:
        parts.append("description=?")
        args.append(req.description.strip())
    if parts:
        args.append(collection_id)
        try:
            db.execute(f"UPDATE collections SET {', '.join(parts)} WHERE id=?", args)
        except Exception as exc:
            raise HTTPException(400, str(exc))
    return {"ok": True}


@app.delete("/api/collections/{collection_id}")
def delete_collection(collection_id: int):
    row = db.one("SELECT id FROM collections WHERE id=?", (collection_id,))
    if not row:
        raise HTTPException(404, "Collection not found")
    db.execute("DELETE FROM collections WHERE id=?", (collection_id,))
    return {"ok": True}


@app.get("/api/collections/{collection_id}/episodes")
def get_collection_episodes(collection_id: int):
    if not db.one("SELECT id FROM collections WHERE id=?", (collection_id,)):
        raise HTTPException(404, "Collection not found")
    return collection_episodes(db, collection_id)


@app.patch("/api/collections/{collection_id}/episodes")
def update_collection_episode(collection_id: int, req: CollectionEpisodeUpdate):
    if req.present:
        db.execute(
            "INSERT OR IGNORE INTO collection_episodes(collection_id, episode_id) VALUES(?, ?)",
            (collection_id, req.episode_id),
        )
    else:
        db.execute(
            "DELETE FROM collection_episodes WHERE collection_id=? AND episode_id=?",
            (collection_id, req.episode_id),
        )
    return {"ok": True}


@app.get("/api/bookmarks")
def list_bookmarks():
    return db.all(
        """SELECT b.*, e.title AS episode_title, p.title AS podcast_title
           FROM bookmarks b
           JOIN episodes e ON e.id=b.episode_id
           JOIN podcasts p ON p.id=e.podcast_id
           ORDER BY b.created_at DESC"""
    )


@app.get("/api/notes")
def list_notes():
    return db.all(
        """SELECT n.*, e.title AS episode_title, p.title AS podcast_title,
                  s.start_ms AS segment_start_ms
           FROM notes n
           LEFT JOIN episodes e ON e.id=n.episode_id
           LEFT JOIN podcasts p ON p.id=e.podcast_id
           LEFT JOIN transcript_segments s ON s.id=n.segment_id
           ORDER BY n.updated_at DESC"""
    )


@app.post("/api/bookmarks")
def create_bookmark(req: BookmarkCreate):
    bid = db.execute(
        "INSERT INTO bookmarks(episode_id, position_ms, label) VALUES(?, ?, ?)",
        (req.episode_id, req.position_ms, req.label),
    )
    return {"id": bid}


@app.delete("/api/bookmarks/{bookmark_id}")
def delete_bookmark(bookmark_id: int):
    db.execute("DELETE FROM bookmarks WHERE id=?", (bookmark_id,))
    return {"ok": True}


@app.post("/api/notes")
def create_note(req: NoteCreate):
    nid = db.execute(
        """INSERT INTO notes(episode_id, segment_id, entity_id, body)
           VALUES(?, ?, ?, ?)""",
        (req.episode_id, req.segment_id, req.entity_id, req.body),
    )
    return {"id": nid}


@app.get("/api/search")
def search(q: str, scope: str = "library", episode_id: int | None = None):
    ids = [episode_id] if scope == "episode" and episode_id else None
    return hybrid_search(db, q, episode_ids=ids, limit=40)


@app.get("/api/intelligence/schema")
def intelligence_schema():
    return r12_schema_manifest()


@app.post("/api/intelligence/listening/session")
def intelligence_listening_touch(req: ListeningSessionTouch):
    try:
        return touch_listening_session(
            db, req.episode_id, req.position_ms, event=req.event, session_id=req.session_id,
            previous_position_ms=req.previous_position_ms,
        )
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/intelligence/listening/session")
def intelligence_listening_session(episode_id: int | None = None):
    return {"session": current_listening_session(db, episode_id)}


@app.get("/api/intelligence/listening/rewind")
def intelligence_listening_rewind(episode_id: int, playhead_ms: int, window_ms: int = 120000, session_id: str | None = None):
    try:
        return listening_rewind(db, episode_id, playhead_ms, window_ms, session_id=session_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/intelligence/listening/recaps")
def intelligence_create_recap(req: ListeningRecapRequest):
    try:
        return generate_listening_recap(db, req.episode_id, session_id=req.session_id)
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/intelligence/listening/recaps")
def intelligence_recaps(episode_id: int | None = None, limit: int = 50):
    return list_listening_recaps(db, episode_id=episode_id, limit=limit)


@app.get("/api/intelligence/provenance")
def intelligence_provenance(object_type: str, object_id: str, claim_key: str | None = None, full_audio_verify: bool = False):
    return {"records": provenance_get(db, object_type, object_id, claim_key=claim_key, full_audio_verify=full_audio_verify)}


@app.get("/api/intelligence/watches")
def intelligence_watches(include_events: bool = True, limit: int = 100):
    return list_watches(db, include_events=include_events, limit=limit)


@app.post("/api/intelligence/watches")
def intelligence_create_watch(req: KnowledgeWatchCreate):
    try:
        return create_watch(
            db, name=req.name, query=req.query, mode=req.mode, scope=req.scope,
            episode_id=req.episode_id, collection_id=req.collection_id, entity_id=req.entity_id,
            reference_text=req.reference_text,
        )
    except RuntimeError as exc:
        raise HTTPException(400, str(exc)) from exc

@app.patch("/api/intelligence/watches/{watch_id}")
def intelligence_update_watch(watch_id: int, req: KnowledgeWatchUpdate):
    try:
        return set_watch_active(db, watch_id, req.active)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/intelligence/watch-events")
def intelligence_watch_events(unacknowledged: bool = False, limit: int = 100):
    return list_watch_events(db, unacknowledged=unacknowledged, limit=limit)


@app.post("/api/intelligence/watch-events/{event_id}/acknowledge")
def intelligence_ack_watch_event(event_id: int):
    try:
        return acknowledge_watch_event(db, event_id)
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/chat")
def chat(req: ChatRequest):
    try:
        return intelligence_answer(
            db,
            req.message,
            conversation_id=req.conversation_id,
            runtime_context=req.screen_context.model_dump() if req.screen_context else None,
            legacy_scope=req.scope,
            episode_id=req.episode_id,
            collection_id=req.collection_id,
            podcast_id=req.podcast_id,
            passage_ms=req.passage_ms,
            images=[item.model_dump() for item in req.images],
            client_history=[item.model_dump() for item in req.history],
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest):
    """Stream Intelligence progress/content as server-sent events; final result remains identical to /api/chat."""
    events: queue.Queue[dict | None] = queue.Queue()

    def emit(event: dict) -> None:
        # Reasoning text is intentionally never forwarded to the browser. The UI only receives
        # content deltas and high-level tool progress, preserving the product's concise surface.
        if str(event.get("type") or "") == "reasoning":
            return
        events.put(event)

    def work() -> None:
        try:
            result = intelligence_answer(
                db, req.message, conversation_id=req.conversation_id,
                runtime_context=req.screen_context.model_dump() if req.screen_context else None,
                legacy_scope=req.scope, episode_id=req.episode_id, collection_id=req.collection_id,
                podcast_id=req.podcast_id, passage_ms=req.passage_ms,
                images=[item.model_dump() for item in req.images],
                client_history=[item.model_dump() for item in req.history],
                event_callback=emit,
            )
            events.put({"type": "result", "result": result})
        except ValueError as exc:
            events.put({"type": "fatal_error", "message": str(exc), "status": 400})
        except Exception as exc:
            events.put({"type": "fatal_error", "message": str(exc), "status": 500})
        finally:
            events.put(None)

    threading.Thread(target=work, name="AudioCodex-Intelligence-Stream", daemon=True).start()

    def generate():
        while True:
            event = events.get()
            if event is None:
                break
            yield "data: " + json.dumps(event, ensure_ascii=False, default=str) + "\n\n"

    return StreamingResponse(
        generate(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/intelligence/actions/receipt")
def intelligence_action_receipt(req: IntelligenceActionReceipt):
    action = req.action if isinstance(req.action, dict) else {}
    action_type = str(action.get("type") or "").strip()
    allowed = {
        "open_episode", "play_audio", "open_entity", "open_collection", "open_view", "search_library",
        "create_bookmark", "create_note", "create_collection", "add_to_collection",
        "update_entity_annotation", "update_collection", "remember_memory", "forget_memory",
        "transcribe_episode", "analyze_episode", "generate_captions",
        "create_listening_recap", "create_knowledge_watch", "set_knowledge_watch", "acknowledge_watch_event",
    }
    if action_type not in allowed:
        raise HTTPException(400, "Unsupported Intelligence action receipt")
    arguments = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
    rid = db.execute(
        """INSERT INTO ai_action_receipts(conversation_id, action_type, arguments_json, status, result_json)
           VALUES(?, ?, ?, ?, ?)""",
        (
            str(req.conversation_id or "").strip() or None, action_type,
            json.dumps(arguments, ensure_ascii=False, default=str), req.status,
            json.dumps(req.result or {}, ensure_ascii=False, default=str),
        ),
    )
    return {"id": rid, "ok": True}


@app.get("/api/intelligence/conversations/{conversation_id}")
def intelligence_conversation(conversation_id: str, limit: int = 100):
    return conversation_messages(db, conversation_id, limit=limit)


@app.delete("/api/intelligence/conversations/{conversation_id}")
def remove_intelligence_conversation(conversation_id: str):
    return {"ok": delete_conversation(db, conversation_id)}


@app.get("/api/intelligence/memories")
def intelligence_memories():
    return db.all(
        "SELECT id, kind, content, created_at, updated_at FROM ai_memories ORDER BY updated_at DESC LIMIT 200"
    )


@app.post("/api/intelligence/memories")
def create_intelligence_memory(req: IntelligenceMemoryCreate):
    content = " ".join(str(req.content or "").split()).strip()[:2000]
    if not content:
        raise HTTPException(400, "Memory content is required")
    kind = "".join(ch if ch.isalnum() or ch in "_-" else "-" for ch in str(req.kind or "preference").lower())
    kind = kind.strip("-")[:40] or "preference"
    db.execute(
        """INSERT INTO ai_memories(kind, content) VALUES(?, ?)
           ON CONFLICT(kind, content) DO UPDATE SET updated_at=CURRENT_TIMESTAMP""",
        (kind, content),
    )
    return db.one(
        "SELECT id, kind, content, created_at, updated_at FROM ai_memories WHERE kind=? AND content=?",
        (kind, content),
    )


@app.delete("/api/intelligence/memories/{memory_id}")
def remove_intelligence_memory(memory_id: int):
    db.execute("DELETE FROM ai_memories WHERE id=?", (memory_id,))
    return {"ok": True}


@app.get("/api/jobs")
def jobs(limit: int = 50, scope: str = "all"):
    limit = max(1, min(500, limit))
    where = ""
    if scope == "current":
        where = "WHERE j.status IN ('queued','running','canceling')"
    elif scope == "history":
        where = "WHERE j.status NOT IN ('queued','running','canceling')"
    return db.all(
        f"""SELECT j.*, e.title AS episode_title, p.title AS podcast_title
            FROM jobs j
            LEFT JOIN episodes e ON e.id=j.episode_id
            LEFT JOIN podcasts p ON p.id=e.podcast_id
            {where}
            ORDER BY j.id DESC LIMIT ?""",
        (limit,),
    )


@app.delete("/api/jobs/history")
def clear_job_history():
    return {"deleted": clear_history(db)}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job_endpoint(job_id: int):
    try:
        return cancel_job(job_id, db)
    except KeyError:
        raise HTTPException(404, "Job not found")


@app.delete("/api/jobs/{job_id}")
def delete_job_endpoint(job_id: int):
    try:
        if not delete_job(job_id, db):
            raise HTTPException(404, "Job not found")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"ok": True}


@app.get("/api/jobs/{job_id}")
def job(job_id: int):
    row = db.one(
        """SELECT j.*, e.title AS episode_title, p.title AS podcast_title
           FROM jobs j
           LEFT JOIN episodes e ON e.id=j.episode_id
           LEFT JOIN podcasts p ON p.id=e.podcast_id
           WHERE j.id=?""",
        (job_id,),
    )
    if not row:
        raise HTTPException(404, "Job not found")
    return row


@app.get("/api/settings")
def get_settings():
    ai_defaults = {
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "api_key": "",
        "chat_model": "deepseek-flash",
        "vision_model": "deepseek-flash",
        "vision_detail": "auto",
        "thinking": True,
        "show_reasoning": False,
        "strict_tools": False,
        "reasoning_effort": "high",
        "chat_max_tokens": 4096,
        "embedding_model": "",
        "context_tokens": 1000000,
        "adaptive_routing": True,
        "pro_model": "deepseek-flash",
        "personal_context": True,
        "conversation_memory": True,
        "auto_listening_recap": False,
        "web_search": False,
    }
    raw_ai = {**ai_defaults, **(db.setting("ai", {}) or {})}
    normalized = AISettings.from_db(db)
    # Surface the same model migration the runtime uses so an upgraded R11 install
    # never presents or re-saves an obsolete model alias in Settings.
    raw_ai.update({
        "provider": normalized.provider,
        "base_url": normalized.base_url,
        "chat_model": normalized.chat_model,
        "vision_model": normalized.vision_model,
        "vision_detail": normalized.vision_detail,
        "pro_model": normalized.pro_model,
        "web_search": False,
        "web_search_available": False,
        "web_search_detail": "Current DeepSeek Responses compatibility ignores built-in web_search.",
    })
    return {
        "transcription": db.setting("transcription", {
            "provider": "windows-ai",
            "model": "base",
            "language": "",
            "recognizer_id": "",
            "diarize": False,
            "hf_token": "",
        }),
        "ai": raw_ai,
        "appearance": db.setting("appearance", {"theme": "light"}),
    }


@app.patch("/api/settings")
def set_settings(req: SettingsUpdate):
    for key, value in req.values.items():
        if key not in {"transcription", "ai", "appearance"}:
            raise HTTPException(400, f"Unsupported settings group: {key}")
        db.set_setting(key, value)
    return {"ok": True}


@app.get("/api/graph")
def graph(limit: int = 80):
    limit = max(10, min(250, limit))
    nodes = db.all(
        """SELECT en.id, en.type, en.canonical_name,
                  COUNT(DISTINCT ee.episode_id) AS episode_count,
                  COUNT(ee.segment_id) AS mention_count
           FROM entities en
           LEFT JOIN episode_entities ee ON ee.entity_id=en.id
           GROUP BY en.id
           ORDER BY episode_count DESC, mention_count DESC, en.canonical_name
           LIMIT ?""",
        (limit,),
    )
    ids = [n["id"] for n in nodes]
    if not ids:
        return {"nodes": [], "links": []}
    placeholders = ",".join("?" for _ in ids)
    links = db.all(
        f"""SELECT source_entity_id AS source, target_entity_id AS target,
                   relation, confidence
            FROM entity_relations
            WHERE source_entity_id IN ({placeholders})
              AND target_entity_id IN ({placeholders})
              AND source_entity_id < target_entity_id
            ORDER BY confidence DESC
            LIMIT 500""",
        [*ids, *ids],
    )
    return {"nodes": nodes, "links": links}


@app.get("/api/timeline")
def timeline():
    return db.all(
        """SELECT e.id, e.title, e.published_at, e.imported_at, e.discipline,
                  p.title AS podcast_title, e.duration_ms
           FROM episodes e JOIN podcasts p ON p.id=e.podcast_id
           ORDER BY COALESCE(e.published_at, e.imported_at), e.id"""
    )


@app.get("/api/reading-list")
def reading_list():
    return db.all(
        """SELECT r.*, e.canonical_name, e.description, e.user_description, e.type
           FROM reading_items r JOIN entities e ON e.id=r.entity_id
           ORDER BY r.created_at DESC"""
    )


@app.post("/api/reading-list")
def add_reading_item(req: ReadingItemCreate):
    entity = db.one("SELECT * FROM entities WHERE id=?", (req.entity_id,))
    if not entity:
        raise HTTPException(404, "Entity not found")
    if entity["type"] != "WORK":
        raise HTTPException(400, "Only WORK entities can be added to the reading list")
    rid = db.execute(
        """INSERT INTO reading_items(entity_id, status, note)
           VALUES(?, ?, ?)
           ON CONFLICT(entity_id) DO UPDATE SET status=excluded.status, note=excluded.note""",
        (req.entity_id, req.status, req.note),
    )
    return {"id": rid or (db.one("SELECT id FROM reading_items WHERE entity_id=?", (req.entity_id,))["id"])}


@app.delete("/api/reading-list/{item_id}")
def delete_reading_item(item_id: int):
    db.execute("DELETE FROM reading_items WHERE id=?", (item_id,))
    return {"ok": True}


@app.get("/api/export/episode/{episode_id}")
def export_episode(episode_id: int, format: str = "markdown"):
    ep = episode_detail(db, episode_id)
    if not ep:
        raise HTTPException(404, "Episode not found")
    tx = db.all(
        """SELECT start_ms, end_ms,
                  COALESCE(user_speaker_label, speaker_label, 'SPEAKER') AS speaker,
                  COALESCE(user_text, text) AS text
           FROM transcript_segments WHERE episode_id=? ORDER BY segment_index""",
        (episode_id,),
    )
    if format == "json":
        return {"episode": ep, "transcript": tx}
    def stamp(ms):
        sec = max(0, int(ms // 1000))
        return f"{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}"
    lines = [
        f"# {ep['title']}",
        "",
        f"**Podcast:** {ep['podcast_title']}",
        f"**Date:** {ep.get('published_at') or ep.get('imported_at') or ''}",
        f"**Discipline:** {ep.get('discipline') or ''}",
        "",
    ]
    if ep.get("summary"):
        lines += ["## Abstract", "", ep["summary"], ""]
    lines += ["## Transcript", ""]
    continuous = continuous_text_from_segments(tx)
    lines += [continuous, ""]
    return PlainTextResponse("\n".join(lines), media_type="text/markdown; charset=utf-8")


@app.get("/api/artwork/episode/{episode_id}")
def episode_artwork(episode_id: int):
    ep = db.one(
        "SELECT id, podcast_id, audio_path, artwork_path FROM episodes WHERE id=?",
        (episode_id,),
    )
    if not ep:
        raise HTTPException(404, "Episode not found")
    artwork = Path(ep["artwork_path"]) if ep.get("artwork_path") else None
    if not artwork or not artwork.is_file():
        audio_path = Path(ep["audio_path"])
        if audio_path.is_file():
            artwork_key = hashlib.sha1(str(audio_path).encode("utf-8")).hexdigest()[:20]
            artwork = extract_artwork(audio_path, PATHS.cache / "artwork" / artwork_key)
            if artwork:
                artwork_str = str(artwork)
                db.execute("UPDATE episodes SET artwork_path=? WHERE id=?", (artwork_str, episode_id))
                db.execute(
                    "UPDATE podcasts SET artwork_path=COALESCE(artwork_path, ?) WHERE id=?",
                    (artwork_str, ep["podcast_id"]),
                )
    if not artwork or not Path(artwork).is_file():
        raise HTTPException(404, "Artwork not found")
    return FileResponse(
        artwork, media_type=image_media_type(Path(artwork)),
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/api/audio/{episode_id}")
def audio(episode_id: int, request: Request):
    ep = db.one("SELECT audio_path FROM episodes WHERE id=?", (episode_id,))
    if not ep:
        raise HTTPException(404, "Episode not found")
    return range_response(Path(ep["audio_path"]), request)


def main():
    uvicorn.run("podcast_codex.server:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
