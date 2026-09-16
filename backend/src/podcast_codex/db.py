from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .config import PATHS
from .schema import SCHEMA


class Database:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or PATHS.db)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if not getattr(self._local, "conn", None):
            self._local.conn = self._connect()
        return self._local.conn

    def initialize(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            # One-time prune. AudioCodex 1.2.1 and earlier materialised a full co-occurrence
            # clique per episode into entity_relations -- every entity paired with every
            # other, in both directions. Those relationships are now derived on read
            # (library.entity_cooccurrence), so the stored rows are dead weight. The settings
            # marker keeps the DELETE to one run per database instead of every open.
            if not conn.execute(
                "SELECT 1 FROM settings WHERE key='pruned_cooccurrence_relations'"
            ).fetchone():
                conn.execute("DELETE FROM entity_relations WHERE relation='CO_OCCURS_IN_EPISODE'")
                conn.execute(
                    "INSERT OR REPLACE INTO settings(key, value_json) "
                    "VALUES('pruned_cooccurrence_relations', '1')"
                )
            conn.commit()
        finally:
            conn.close()

    @contextlib.contextmanager
    def tx(self):
        conn = self.conn
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        cur = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def setting(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value_json FROM settings WHERE key=?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value_json"])
        except json.JSONDecodeError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            """INSERT INTO settings(key, value_json) VALUES(?, ?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json""",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def replace_transcript(self, episode_id: int, segments: list[dict]) -> None:
        with self.tx() as conn:
            old_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM transcript_segments WHERE episode_id=?", (episode_id,)
                ).fetchall()
            ]
            for sid in old_ids:
                conn.execute("DELETE FROM transcript_fts WHERE segment_id=?", (sid,))
            conn.execute("DELETE FROM transcript_segments WHERE episode_id=?", (episode_id,))
            for i, seg in enumerate(segments):
                cur = conn.execute(
                    """INSERT INTO transcript_segments(
                       episode_id, segment_index, start_ms, end_ms,
                       speaker_label, text, confidence
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)""",
                    (
                        episode_id,
                        i,
                        int(seg["start_ms"]),
                        int(seg["end_ms"]),
                        seg.get("speaker_label"),
                        seg["text"].strip(),
                        seg.get("confidence"),
                    ),
                )
                segment_id = cur.lastrowid
                conn.execute(
                    "INSERT INTO transcript_fts(text, segment_id, episode_id) VALUES(?, ?, ?)",
                    (seg["text"].strip(), segment_id, episode_id),
                )
            conn.execute(
                "UPDATE episodes SET transcript_status='ready' WHERE id=?", (episode_id,)
            )

    def replace_captions(self, episode_id: int, segments: list[dict]) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM caption_segments WHERE episode_id=?", (episode_id,))
            for i, seg in enumerate(segments):
                text = str(seg.get("text") or "").strip()
                if not text:
                    continue
                conn.execute(
                    """INSERT INTO caption_segments(
                       episode_id, segment_index, start_ms, end_ms, text, confidence
                    ) VALUES(?, ?, ?, ?, ?, ?)""",
                    (
                        episode_id, i, int(seg.get("start_ms") or 0),
                        int(seg.get("end_ms") or 0), text, seg.get("confidence"),
                    ),
                )

    def clear_captions(self, episode_id: int) -> None:
        self.execute("DELETE FROM caption_segments WHERE episode_id=?", (episode_id,))

    def update_segment_fts(self, segment_id: int, text: str) -> None:
        row = self.one(
            "SELECT episode_id FROM transcript_segments WHERE id=?", (segment_id,)
        )
        if not row:
            return
        with self.tx() as conn:
            conn.execute("DELETE FROM transcript_fts WHERE segment_id=?", (segment_id,))
            conn.execute(
                "INSERT INTO transcript_fts(text, segment_id, episode_id) VALUES(?, ?, ?)",
                (text, segment_id, row["episode_id"]),
            )


db = Database()
