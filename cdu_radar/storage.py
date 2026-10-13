import hashlib
import sqlite3
from pathlib import Path

from .models import Article, Source, utc_now

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, college TEXT NOT NULL, homepage TEXT,
    column_name TEXT, column_url TEXT, column_type TEXT, adapter TEXT,
    status TEXT NOT NULL CHECK(status IN ('verified','pending','unsupported','failed')),
    last_verified_at TEXT, directory_checked_at TEXT NOT NULL, note TEXT NOT NULL,
    last_attempt_at TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    title TEXT NOT NULL CHECK(length(trim(title)) > 0), canonical_url TEXT NOT NULL UNIQUE,
    published_at TEXT, discovered_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    content TEXT NOT NULL CHECK(length(trim(content)) > 0), content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    application_deadline TEXT
);
CREATE TABLE IF NOT EXISTS notice_sources (
    notice_id INTEGER NOT NULL REFERENCES notices(id), source_id TEXT NOT NULL REFERENCES sources(id),
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    PRIMARY KEY(notice_id, source_id)
);
CREATE TABLE IF NOT EXISTS request_events (
    id INTEGER PRIMARY KEY, source_id TEXT REFERENCES sources(id), url TEXT NOT NULL,
    requested_at TEXT NOT NULL, attempt INTEGER NOT NULL, status_code INTEGER,
    error TEXT, elapsed_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS collection_runs (
    id INTEGER PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
    started_at TEXT NOT NULL, finished_at TEXT NOT NULL, inserted INTEGER NOT NULL,
    updated INTEGER NOT NULL, unchanged INTEGER NOT NULL, failed INTEGER NOT NULL,
    error TEXT
);
"""


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def sync_sources(self, sources: list[Source]):
        with self.db:
            for source in sources:
                # Catalog refresh must not erase operational failure/verification state.
                self.db.execute("""INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)
                    ON CONFLICT(id) DO UPDATE SET college=excluded.college, homepage=excluded.homepage,
                    column_name=excluded.column_name, column_url=excluded.column_url,
                    column_type=excluded.column_type, adapter=excluded.adapter,
                    directory_checked_at=excluded.directory_checked_at, note=excluded.note""",
                    (source.id, source.college, source.homepage, source.column_name, source.column_url,
                     source.column_type, source.adapter, source.status, source.last_verified_at,
                     source.directory_checked_at, source.note))

    def save_notice(self, source_id: str, url: str, article: Article, seen_at: str | None = None) -> str:
        now = seen_at or utc_now()
        digest = hashlib.sha256(article.content.encode("utf-8")).hexdigest()
        with self.db:
            old = self.db.execute("SELECT * FROM notices WHERE canonical_url=?", (url,)).fetchone()
            if old is None:
                cursor = self.db.execute("""INSERT INTO notices
                    (source_id,title,canonical_url,published_at,discovered_at,last_seen_at,content,content_hash,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)""", (source_id, article.title, url, article.published_at,
                                                  now, now, article.content, digest, now))
                notice_id, result = cursor.lastrowid, "inserted"
            else:
                notice_id = old["id"]
                published = article.published_at or old["published_at"]
                changed = (digest != old["content_hash"] or article.title != old["title"] or published != old["published_at"])
                result = "updated" if changed else "unchanged"
                self.db.execute("""UPDATE notices SET title=?, published_at=?, content=?, content_hash=?,
                    last_seen_at=?, updated_at=? WHERE id=?""", (article.title, published, article.content, digest,
                    now, now if changed else old["updated_at"], notice_id))
            self.db.execute("""INSERT INTO notice_sources VALUES (?,?,?,?)
                ON CONFLICT(notice_id,source_id) DO UPDATE SET last_seen_at=excluded.last_seen_at""",
                (notice_id, source_id, now, now))
        return result

    def request_event(self, source_id, url, requested_at, attempt, status_code, error, elapsed_ms):
        with self.db:
            self.db.execute("""INSERT INTO request_events
                (source_id,url,requested_at,attempt,status_code,error,elapsed_ms) VALUES (?,?,?,?,?,?,?)""",
                (source_id, url, requested_at, attempt, status_code, error, elapsed_ms))

    def finish_run(self, source_id: str, started: str, counts: dict, error: str | None):
        now = utc_now()
        with self.db:
            self.db.execute("""INSERT INTO collection_runs
                (source_id,started_at,finished_at,inserted,updated,unchanged,failed,error) VALUES (?,?,?,?,?,?,?,?)""",
                (source_id, started, now, counts["inserted"], counts["updated"], counts["unchanged"], counts["failed"], error))
            self.db.execute("""UPDATE sources SET status=?, last_attempt_at=?, last_error=?,
                last_verified_at=CASE WHEN ? IS NULL THEN ? ELSE last_verified_at END WHERE id=?""",
                ("failed" if error else "verified", now, error, error, now, source_id))

    def feed_rows(self, limit: int):
        return self.db.execute("""SELECT * FROM notices
            WHERE length(trim(content)) > 0 ORDER BY COALESCE(published_at,substr(discovered_at,1,10)) DESC,
            discovered_at DESC, canonical_url ASC LIMIT ?""", (limit,)).fetchall()

    def origins(self, notice_id: int):
        return self.db.execute("""SELECT s.college,s.column_name,s.column_url FROM sources s
            JOIN notice_sources ns ON ns.source_id=s.id WHERE ns.notice_id=? ORDER BY s.id""", (notice_id,)).fetchall()
