from __future__ import annotations

import json
from collections import defaultdict

from .ai import AISettings, EmbeddingProvider, cosine
from .db import Database


def scope_episode_ids(db: Database, scope: str, episode_id: int | None = None,
                      collection_id: int | None = None,
                      podcast_id: int | None = None) -> list[int] | None:
    if scope == "episode" and episode_id:
        return [episode_id]
    if scope == "collection" and collection_id:
        coll = db.one("SELECT * FROM collections WHERE id=?", (collection_id,))
        if not coll:
            return []
        if coll["is_smart"]:
            query = json.loads(coll["query_json"] or "{}")
            sql = "SELECT id FROM episodes WHERE 1=1"
            args: list[object] = []
            if query.get("discipline"):
                sql += " AND discipline=?"
                args.append(query["discipline"])
            if query.get("podcast_id"):
                sql += " AND podcast_id=?"
                args.append(query["podcast_id"])
            if query.get("unfinished"):
                sql += " AND completed=0 AND playhead_ms>0"
            return [r["id"] for r in db.all(sql, args)]
        return [
            r["episode_id"]
            for r in db.all(
                "SELECT episode_id FROM collection_episodes WHERE collection_id=?",
                (collection_id,),
            )
        ]
    if scope == "podcast" and podcast_id:
        return [r["id"] for r in db.all("SELECT id FROM episodes WHERE podcast_id=?", (podcast_id,))]
    return None


def lexical_search(db: Database, query: str, episode_ids: list[int] | None = None,
                   limit: int = 16) -> list[dict]:
    if not query.strip():
        return []
    # FTS MATCH syntax is intentionally simple: quote user terms to avoid operator surprises.
    terms = [t for t in query.replace('"', " ").split() if t]
    if not terms:
        return []
    match = " OR ".join(f'"{t}"' for t in terms[:12])
    sql = """
      SELECT f.segment_id, f.episode_id, bm25(transcript_fts) AS rank,
             s.segment_index, s.start_ms, s.end_ms,
             COALESCE(s.user_text, s.text) AS text,
             COALESCE(s.user_speaker_label, s.speaker_label, '') AS speaker_label,
             e.title AS episode_title, e.podcast_id AS podcast_id, p.title AS podcast_title
      FROM transcript_fts f
      JOIN transcript_segments s ON s.id = f.segment_id
      JOIN episodes e ON e.id = f.episode_id
      JOIN podcasts p ON p.id = e.podcast_id
      WHERE transcript_fts MATCH ?
    """
    args: list[object] = [match]
    if episode_ids is not None:
        if not episode_ids:
            return []
        placeholders = ",".join("?" for _ in episode_ids)
        sql += f" AND f.episode_id IN ({placeholders})"
        args.extend(episode_ids)
    sql += " ORDER BY rank LIMIT ?"
    args.append(limit)
    rows = db.all(sql, args)
    for row in rows:
        row["score"] = 1.0 / (1.0 + max(0.0, float(row["rank"])))
        row["source"] = "fts"
    return rows


def vector_search(db: Database, query: str, episode_ids: list[int] | None = None,
                  limit: int = 12) -> list[dict]:
    settings = AISettings.from_db(db)
    provider = EmbeddingProvider(settings)
    if not provider.available:
        return []
    vectors = provider.embed([query])
    if not vectors:
        return []
    q = vectors[0]
    sql = """
      SELECT emb.segment_id, emb.vector_json, s.episode_id, s.segment_index, s.start_ms, s.end_ms,
             COALESCE(s.user_text, s.text) AS text,
             COALESCE(s.user_speaker_label, s.speaker_label, '') AS speaker_label,
             e.title AS episode_title, e.podcast_id AS podcast_id, p.title AS podcast_title
      FROM embeddings emb
      JOIN transcript_segments s ON s.id=emb.segment_id
      JOIN episodes e ON e.id=s.episode_id
      JOIN podcasts p ON p.id=e.podcast_id
      WHERE emb.provider=? AND emb.model=?
    """
    rows = db.all(sql, (settings.provider, settings.embedding_model))
    scored = []
    allowed = set(episode_ids) if episode_ids is not None else None
    for row in rows:
        if allowed is not None and row["episode_id"] not in allowed:
            continue
        try:
            v = json.loads(row.pop("vector_json"))
        except Exception:
            continue
        row["score"] = cosine(q, v)
        row["source"] = "vector"
        scored.append(row)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def hybrid_search(db: Database, query: str, episode_ids: list[int] | None = None,
                  limit: int = 12) -> list[dict]:
    lexical = lexical_search(db, query, episode_ids=episode_ids, limit=limit * 2)
    vector = vector_search(db, query, episode_ids=episode_ids, limit=limit * 2)
    merged: dict[int, dict] = {}
    # Reciprocal rank fusion is robust even though lexical and vector scores differ in scale.
    for source_rows in (lexical, vector):
        for rank, row in enumerate(source_rows, start=1):
            sid = row["segment_id"]
            if sid not in merged:
                merged[sid] = {**row, "rrf": 0.0, "sources": []}
            merged[sid]["rrf"] += 1.0 / (60 + rank)
            merged[sid]["sources"].append(row["source"])
    out = sorted(merged.values(), key=lambda x: x["rrf"], reverse=True)
    return out[:limit]


def index_embeddings(db: Database, episode_id: int, batch_size: int = 24,
                     progress=None) -> int:
    settings = AISettings.from_db(db)
    provider = EmbeddingProvider(settings)
    if not provider.available:
        return 0
    rows = db.all(
        """SELECT id, COALESCE(user_text, text) AS text
           FROM transcript_segments WHERE episode_id=? ORDER BY segment_index""",
        (episode_id,),
    )
    done = 0
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset: offset + batch_size]
        vectors = provider.embed([r["text"] for r in batch])
        for row, vector in zip(batch, vectors):
            db.execute(
                """INSERT INTO embeddings(segment_id, provider, model, dimensions, vector_json)
                   VALUES(?, ?, ?, ?, ?)
                   ON CONFLICT(segment_id, provider, model)
                   DO UPDATE SET dimensions=excluded.dimensions, vector_json=excluded.vector_json""",
                (
                    row["id"], settings.provider, settings.embedding_model,
                    len(vector), json.dumps(vector),
                ),
            )
            done += 1
        if progress:
            progress(done / max(1, len(rows)), f"Embedded {done}/{len(rows)} segments")
    return done
