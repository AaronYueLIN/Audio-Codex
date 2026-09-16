from __future__ import annotations

import json
from .db import Database


def list_episodes(db: Database, mode: str | None = None, podcast_id: int | None = None, sort: str | None = None) -> list[dict]:
    sql = """
      SELECT e.*, p.title AS podcast_title
      FROM episodes e JOIN podcasts p ON p.id=e.podcast_id
      WHERE 1=1
    """
    args: list[object] = []
    if podcast_id:
        sql += " AND e.podcast_id=?"
        args.append(podcast_id)
    if mode == "unfinished":
        sql += " AND e.completed=0 AND e.playhead_ms>0"
    elif mode == "recent":
        sql += " AND e.last_played_at IS NOT NULL"
    if sort == "title":
        order = "e.title COLLATE NOCASE ASC, e.id ASC"
    elif sort == "recent_played":
        order = "CASE WHEN e.last_played_at IS NULL THEN 1 ELSE 0 END, e.last_played_at DESC, e.imported_at DESC, e.id DESC"
    elif sort == "imported":
        order = "e.imported_at DESC, e.id DESC"
    else:
        order = "e.last_played_at DESC, e.id DESC" if mode == "recent" else "COALESCE(e.published_at, e.imported_at) DESC, e.id DESC"
    sql += f" ORDER BY {order}"
    return db.all(sql, args)


def episode_detail(db: Database, episode_id: int) -> dict | None:
    ep = db.one(
        """SELECT e.*, p.title AS podcast_title
           FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE e.id=?""",
        (episode_id,),
    )
    if not ep:
        return None
    ep["chapters"] = db.all(
        "SELECT * FROM chapters WHERE episode_id=? ORDER BY start_ms", (episode_id,)
    )
    ep["bookmarks"] = db.all(
        "SELECT * FROM bookmarks WHERE episode_id=? ORDER BY position_ms", (episode_id,)
    )
    ep["notes"] = db.all(
        "SELECT * FROM notes WHERE episode_id=? ORDER BY created_at DESC", (episode_id,)
    )
    ep["entities"] = db.all(
        """SELECT DISTINCT en.*
           FROM episode_entities ee JOIN entities en ON en.id=ee.entity_id
           WHERE ee.episode_id=? ORDER BY en.type, en.canonical_name""",
        (episode_id,),
    )
    ep["collections"] = db.all(
        """SELECT c.* FROM collections c
           JOIN collection_episodes ce ON ce.collection_id=c.id
           WHERE ce.episode_id=? ORDER BY c.name""",
        (episode_id,),
    )
    return ep


def list_collections(db: Database) -> list[dict]:
    rows = db.all("SELECT * FROM collections ORDER BY name")
    for row in rows:
        if row["is_smart"]:
            query = json.loads(row["query_json"] or "{}")
            sql = "SELECT COUNT(*) AS n FROM episodes WHERE 1=1"
            args = []
            if query.get("discipline"):
                sql += " AND discipline=?"
                args.append(query["discipline"])
            if query.get("podcast_id"):
                sql += " AND podcast_id=?"
                args.append(query["podcast_id"])
            if query.get("unfinished"):
                sql += " AND completed=0 AND playhead_ms>0"
            row["episode_count"] = db.one(sql, args)["n"]
        else:
            row["episode_count"] = db.one(
                "SELECT COUNT(*) AS n FROM collection_episodes WHERE collection_id=?",
                (row["id"],),
            )["n"]
        row["query"] = json.loads(row.pop("query_json") or "{}")
    return rows


def collection_episodes(db: Database, collection_id: int) -> list[dict]:
    c = db.one("SELECT * FROM collections WHERE id=?", (collection_id,))
    if not c:
        return []
    if c["is_smart"]:
        query = json.loads(c["query_json"] or "{}")
        sql = """
          SELECT e.*, p.title AS podcast_title
          FROM episodes e JOIN podcasts p ON p.id=e.podcast_id WHERE 1=1
        """
        args = []
        if query.get("discipline"):
            sql += " AND e.discipline=?"
            args.append(query["discipline"])
        if query.get("podcast_id"):
            sql += " AND e.podcast_id=?"
            args.append(query["podcast_id"])
        if query.get("unfinished"):
            sql += " AND e.completed=0 AND e.playhead_ms>0"
        return db.all(sql + " ORDER BY e.imported_at DESC", args)
    return db.all(
        """SELECT e.*, p.title AS podcast_title
           FROM collection_episodes ce
           JOIN episodes e ON e.id=ce.episode_id
           JOIN podcasts p ON p.id=e.podcast_id
           WHERE ce.collection_id=?
           ORDER BY e.imported_at DESC""",
        (collection_id,),
    )
