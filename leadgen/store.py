"""SQLite checkpoint store. Every unit of work is committed as soon as it finishes,
so a crash (or Ctrl+C, or a kill -9) loses at most the request that was in flight.

Run the same command again and it continues: pending tiles resume at their saved
offset, enrichment skips businesses that are already done.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .maps.geo import Tile
from .maps.parse import Place

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tiles (
    query TEXT NOT NULL, tile_id TEXT NOT NULL, depth INTEGER NOT NULL, parent TEXT,
    status TEXT NOT NULL DEFAULT 'pending',          -- pending | done | split | failed
    next_offset INTEGER NOT NULL DEFAULT 0, pages INTEGER NOT NULL DEFAULT 0,
    results INTEGER NOT NULL DEFAULT 0, in_tile INTEGER NOT NULL DEFAULT 0,
    new_unique INTEGER NOT NULL DEFAULT 0, stale_pages INTEGER NOT NULL DEFAULT 0,
    error TEXT, updated_at REAL,
    PRIMARY KEY (query, tile_id)
);
CREATE TABLE IF NOT EXISTS places (
    cid TEXT PRIMARY KEY, data TEXT NOT NULL, first_query TEXT, first_seen REAL
);
CREATE TABLE IF NOT EXISTS place_hits (cid TEXT, query TEXT, PRIMARY KEY (cid, query));
CREATE TABLE IF NOT EXISTS requests (
    n INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, query TEXT, tile_id TEXT, "offset" INTEGER,
    results INTEGER, in_tile INTEGER, new_unique INTEGER
);
CREATE TABLE IF NOT EXISTS enrich (
    cid TEXT PRIMARY KEY, status TEXT NOT NULL,      -- done | failed | skipped
    attempts INTEGER NOT NULL DEFAULT 0, data TEXT, error TEXT, updated_at REAL
);
CREATE TABLE IF NOT EXISTS failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, stage TEXT, key TEXT, error TEXT
);
"""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None)  # autocommit; explicit txns below
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    # ---------------------------------------------------------------- meta
    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, value: Any) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    # ---------------------------------------------------------------- tiles
    def add_tiles(self, query: str, tiles: list[Tile], parent: str | None = None) -> None:
        with self.tx() as db:
            db.executemany(
                "INSERT OR IGNORE INTO tiles (query, tile_id, depth, parent, updated_at) VALUES (?,?,?,?,?)",
                [(query, t.id, t.depth, parent, time.time()) for t in tiles],
            )

    def has_tiles(self, query: str) -> bool:
        return self.db.execute("SELECT 1 FROM tiles WHERE query=? LIMIT 1", (query,)).fetchone() is not None

    def pending_tiles(self, query: str) -> list[sqlite3.Row]:
        self.db.row_factory = sqlite3.Row
        try:
            return self.db.execute(
                "SELECT * FROM tiles WHERE query=? AND status='pending' ORDER BY depth, tile_id", (query,)
            ).fetchall()
        finally:
            self.db.row_factory = None

    def save_page(self, query: str, tile_id: str, offset: int, places: list[Place],
                  in_tile: int, stale_pages: int) -> int:
        """Atomically store one page of results and advance the tile cursor. Returns #new places."""
        now = time.time()
        new = 0
        with self.tx() as db:
            for p in places:
                cur = db.execute(
                    "INSERT OR IGNORE INTO places (cid, data, first_query, first_seen) VALUES (?,?,?,?)",
                    (p.cid, json.dumps(p.to_dict()), query, now),
                )
                if cur.rowcount:
                    new += 1
                else:  # keep the richest version we've seen (e.g. one page had the website)
                    old = json.loads(db.execute("SELECT data FROM places WHERE cid=?", (p.cid,)).fetchone()[0])
                    merged = {k: (v if v not in (None, [], "") else old.get(k)) for k, v in p.to_dict().items()}
                    db.execute("UPDATE places SET data=? WHERE cid=?", (json.dumps(merged), p.cid))
                db.execute("INSERT OR IGNORE INTO place_hits VALUES (?,?)", (p.cid, query))
            db.execute(
                "UPDATE tiles SET next_offset=?, pages=pages+1, results=results+?, in_tile=in_tile+?, "
                "new_unique=new_unique+?, stale_pages=?, updated_at=? WHERE query=? AND tile_id=?",
                (offset + 20, len(places), in_tile, new, stale_pages, now, query, tile_id),
            )
            db.execute(
                'INSERT INTO requests (ts, query, tile_id, "offset", results, in_tile, new_unique) '
                "VALUES (?,?,?,?,?,?,?)",
                (now, query, tile_id, offset, len(places), in_tile, new),
            )
        return new

    def finish_tile(self, query: str, tile_id: str, status: str, error: str | None = None,
                    children: list[Tile] | None = None) -> None:
        with self.tx() as db:
            db.execute("UPDATE tiles SET status=?, error=?, updated_at=? WHERE query=? AND tile_id=?",
                       (status, error, time.time(), query, tile_id))
            for t in children or []:
                db.execute(
                    "INSERT OR IGNORE INTO tiles (query, tile_id, depth, parent, updated_at) VALUES (?,?,?,?,?)",
                    (query, t.id, t.depth, tile_id, time.time()),
                )

    def recent_in_tile(self, query: str, tile_id: str, n: int) -> list[int]:
        rows = self.db.execute('SELECT in_tile FROM requests WHERE query=? AND tile_id=? ORDER BY n DESC LIMIT ?',
                               (query, tile_id, n)).fetchall()
        return [r[0] for r in rows]

    def query_started(self, query: str) -> bool:
        return self.db.execute("SELECT 1 FROM requests WHERE query=? LIMIT 1", (query,)).fetchone() is not None

    def reset_unstarted_tiles(self, query: str) -> None:
        self.db.execute("DELETE FROM tiles WHERE query=? AND pages=0 AND status='pending'", (query,))

    def tiles_for(self, query: str, status: str | None = None) -> list[tuple[str, int]]:
        q = "SELECT tile_id, depth FROM tiles WHERE query=?" + (" AND status=?" if status else "") + " ORDER BY tile_id"
        return self.db.execute(q, (query, status) if status else (query,)).fetchall()

    def tile_stats(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for q, status, n in self.db.execute("SELECT query, status, COUNT(*) FROM tiles GROUP BY 1, 2"):
            out.setdefault(q, {})[status] = n
        return out

    # ---------------------------------------------------------------- places
    def all_places(self) -> list[dict]:
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM places ORDER BY first_seen")]

    def hits_by_query(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {}
        for cid, q in self.db.execute("SELECT cid, query FROM place_hits"):
            out.setdefault(q, set()).add(cid)
        return out

    def request_log(self) -> list[tuple]:
        return self.db.execute(
            'SELECT n, query, tile_id, "offset", results, in_tile, new_unique FROM requests ORDER BY n'
        ).fetchall()

    # ---------------------------------------------------------------- enrichment
    def enrich_done(self) -> set[str]:
        return {r[0] for r in self.db.execute("SELECT cid FROM enrich WHERE status IN ('done','skipped')")}

    def enrich_attempts(self, cid: str) -> int:
        row = self.db.execute("SELECT attempts FROM enrich WHERE cid=?", (cid,)).fetchone()
        return row[0] if row else 0

    def save_enrichment(self, cid: str, status: str, data: dict | None, error: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO enrich (cid, status, attempts, data, error, updated_at) VALUES (?,?,1,?,?,?) "
            "ON CONFLICT(cid) DO UPDATE SET status=excluded.status, attempts=enrich.attempts+1, "
            "data=excluded.data, error=excluded.error, updated_at=excluded.updated_at",
            (cid, status, json.dumps(data) if data is not None else None, error, time.time()),
        )

    def enrichment(self) -> dict[str, dict]:
        out = {}
        for cid, status, data, error in self.db.execute("SELECT cid, status, data, error FROM enrich"):
            d = json.loads(data) if data else {}
            d["_status"], d["_error"] = status, error
            out[cid] = d
        return out

    # ---------------------------------------------------------------- failures
    def log_failure(self, stage: str, key: str, error: str) -> None:
        self.db.execute("INSERT INTO failures (ts, stage, key, error) VALUES (?,?,?,?)",
                        (time.time(), stage, key, error[:500]))

    def failures(self) -> list[dict]:
        return [dict(zip(("stage", "key", "error"), r))
                for r in self.db.execute("SELECT stage, key, error FROM failures ORDER BY id")]

    def close(self) -> None:
        self.db.close()
