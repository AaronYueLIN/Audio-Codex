from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


class ImportRequest(BaseModel):
    paths: list[str]
    podcast_title: str | None = None


class TranscribeRequest(BaseModel):
    provider: Literal["faster-whisper", "windows-ai"] | None = None
    diarize: bool | None = None
    model: str | None = None
    language: str | None = None
    recognizer_id: str | None = None


class AnalyzeRequest(BaseModel):
    regenerate: bool = False
    embeddings: bool = True


class PlayheadRequest(BaseModel):
    position_ms: int = Field(ge=0)
    completed: bool | None = None


class SegmentCorrection(BaseModel):
    text: str | None = None
    speaker_label: str | None = None
    apply_speaker_to_episode: bool = True


class CollectionCreate(BaseModel):
    name: str
    description: str = ""
    is_smart: bool = False
    query: dict[str, Any] = Field(default_factory=dict)


class CollectionUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class CollectionEpisodeUpdate(BaseModel):
    episode_id: int
    present: bool = True


class BookmarkCreate(BaseModel):
    episode_id: int
    position_ms: int = Field(ge=0)
    label: str = ""


class NoteCreate(BaseModel):
    episode_id: int | None = None
    segment_id: int | None = None
    entity_id: int | None = None
    body: str


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class ChatImage(BaseModel):
    data_url: str
    name: str = ""
    detail: Literal["low", "high", "original", "auto"] | None = None


class ChatHistoryItem(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatSelectionRange(BaseModel):
    segment_ids: list[int] = Field(default_factory=list)
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=0)


class ChatVisibleItem(BaseModel):
    type: Literal["episode", "entity", "collection", "note", "bookmark", "transcript_segment", "watch"]
    id: int = Field(ge=1)
    index: int = Field(default=1, ge=1, le=1000)
    title: str = ""


class ChatScreenContext(BaseModel):
    view: str = ""
    episode_id: int | None = None
    selected_episode_id: int | None = None
    selected_entity_id: int | None = None
    selected_collection_id: int | None = None
    playback_ms: int | None = Field(default=None, ge=0)
    playhead_ms: int | None = Field(default=None, ge=0)
    player_state: str = ""
    visible_title: str = ""
    selected_text: str = ""
    active_caption: str = ""
    search_query: str = ""
    visible_items: list[ChatVisibleItem] = Field(default_factory=list)
    selection: ChatSelectionRange | None = None
    rewind_window_ms: int | None = Field(default=None, ge=15000, le=900000)
    listening_session_id: str = ""


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    scope: Literal["passage", "episode", "collection", "podcast", "library"] = "episode"
    episode_id: int | None = None
    collection_id: int | None = None
    podcast_id: int | None = None
    passage_ms: int | None = None
    images: list[ChatImage] = Field(default_factory=list)
    history: list[ChatHistoryItem] = Field(default_factory=list)
    screen_context: ChatScreenContext | None = None


class IntelligenceMemoryCreate(BaseModel):
    content: str
    kind: str = "preference"


class IntelligenceActionReceipt(BaseModel):
    conversation_id: str | None = None
    action: dict[str, Any]
    status: Literal["confirmed", "failed"] = "confirmed"
    result: dict[str, Any] = Field(default_factory=dict)


class EntityUpdate(BaseModel):
    canonical_name: str | None = None
    description: str | None = None
    entity_type: str | None = None


class ReadingItemCreate(BaseModel):
    entity_id: int
    status: str = "to-read"
    note: str = ""


class ListeningSessionTouch(BaseModel):
    episode_id: int
    position_ms: int = Field(ge=0)
    previous_position_ms: int | None = Field(default=None, ge=0)
    event: Literal["play", "progress", "seek", "pause", "ended"] = "progress"
    session_id: str | None = None


class ListeningRecapRequest(BaseModel):
    episode_id: int
    session_id: str | None = None


class KnowledgeWatchCreate(BaseModel):
    name: str = ""
    query: str = ""
    mode: Literal["mention", "topic", "contradiction", "new_episode"] = "mention"
    scope: Literal["library", "episode", "collection", "entity"] = "library"
    episode_id: int | None = None
    collection_id: int | None = None
    entity_id: int | None = None
    reference_text: str = ""


class KnowledgeWatchUpdate(BaseModel):
    active: bool
