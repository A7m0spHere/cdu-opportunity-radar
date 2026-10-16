import hashlib
import sqlite3
from pathlib import Path

from .models import Article, Source, ParseError, utc_now

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
        self._migrate()
        from .auto_publish import initialize
        initialize(self.db)
        from .recheck import initialize as initialize_rechecks
        initialize_rechecks(self.db)

    def _migrate(self):
        """Append-only, transactional M0→M1 migration; never recreate notices."""
        additions = {
            "sources": {"unit_id":"TEXT", "unit_type":"TEXT", "official_domain":"TEXT", "discovered_from":"TEXT",
                "last_checked_at":"TEXT", "accessible":"INTEGER", "articles_found":"INTEGER", "parser_supported":"INTEGER DEFAULT 0",
                "error_category":"TEXT", "error_scope":"TEXT", "retryable":"INTEGER DEFAULT 1", "content_adapter":"TEXT"},
            "notices": {"content_source_id":"TEXT REFERENCES units(id)", "content_domain":"TEXT"},
            "notice_sources": {"published_at":"TEXT"},
            "collection_runs": {"pages_read":"INTEGER DEFAULT 1", "parse_failures":"INTEGER DEFAULT 0", "elapsed_seconds":"REAL DEFAULT 0", "duplicates":"INTEGER DEFAULT 0"},
        }
        with self.db:
            self.db.execute("BEGIN")
            self.db.execute("CREATE TABLE IF NOT EXISTS units (id TEXT PRIMARY KEY, name TEXT NOT NULL, unit_type TEXT, official_domain TEXT)")
            for table, columns in additions.items():
                current = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
                for name, definition in columns.items():
                    if name not in current:
                        self.db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            self.db.execute("""CREATE TABLE IF NOT EXISTS notice_links (
                notice_id INTEGER NOT NULL REFERENCES notices(id), kind TEXT NOT NULL CHECK(kind IN ('body','attachment')),
                name TEXT NOT NULL, url TEXT NOT NULL, PRIMARY KEY(notice_id,kind,url))""")
            current_version=self.db.execute("PRAGMA user_version").fetchone()[0]
            self.db.execute(f"PRAGMA user_version = {max(1,current_version)}")

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
                self.db.execute("""INSERT INTO sources
                    (id,college,homepage,column_name,column_url,column_type,adapter,status,last_verified_at,directory_checked_at,note)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET college=excluded.college, homepage=excluded.homepage,
                    column_name=excluded.column_name, column_url=excluded.column_url,
                    column_type=excluded.column_type, adapter=excluded.adapter,
                    directory_checked_at=excluded.directory_checked_at, note=excluded.note""",
                    (source.id, source.college, source.homepage, source.column_name, source.column_url,
                     source.column_type, source.adapter, source.status, source.last_verified_at,
                     source.directory_checked_at, source.note))
                unit_id = source.unit_id or source.id
                from urllib.parse import urlsplit
                domain = source.official_domain or (urlsplit(source.homepage).hostname if source.homepage else None)
                self.db.execute("""INSERT INTO units VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,unit_type=excluded.unit_type,official_domain=excluded.official_domain""",
                    (unit_id,source.college,source.unit_type,domain))
                self.db.execute("""UPDATE sources SET unit_id=?,unit_type=?,official_domain=?,discovered_from=?,
                    last_checked_at=?,accessible=?,articles_found=?,parser_supported=?,content_adapter=? WHERE id=?""",
                    (unit_id,source.unit_type,domain,source.discovered_from,source.last_checked_at,source.accessible,
                     source.articles_found,source.parser_supported,source.content_adapter,source.id))
            for row in self.db.execute("SELECT id,canonical_url FROM notices WHERE content_source_id IS NULL").fetchall():
                host = urlsplit(row["canonical_url"]).hostname
                matches = self.db.execute("SELECT id FROM units WHERE official_domain=?", (host,)).fetchall()
                unit = matches[0] if len(matches)==1 else None
                self.db.execute("UPDATE notices SET content_domain=?,content_source_id=? WHERE id=?",
                                (host,unit[0] if unit else None,row["id"]))

    def save_notice(self, source_id: str, url: str, article: Article, seen_at: str | None = None, *, discovery_published_at=None) -> str:
        now = seen_at or utc_now()
        digest = hashlib.sha256(article.content.encode("utf-8")).hexdigest()
        names_by_url = {}
        for kind, items in [("body", article.links), ("attachment", article.attachments)]:
            for link in items:
                names_by_url.setdefault((kind, link.url), set()).add(link.name)
        # Match the table key; select a stable nonempty caption regardless of DOM order.
        links = sorted((kind, max(names, key=lambda name: (len(name.strip()), name)), url)
                       for (kind, url), names in names_by_url.items())
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
                previous_links = sorted(tuple(r) for r in self.db.execute("SELECT kind,name,url FROM notice_links WHERE notice_id=?",(notice_id,)))
                changed = (digest != old["content_hash"] or article.title != old["title"] or published != old["published_at"] or links != previous_links)
                result = "updated" if changed else "unchanged"
                self.db.execute("""UPDATE notices SET title=?, published_at=?, content=?, content_hash=?,
                    last_seen_at=?, updated_at=? WHERE id=?""", (article.title, published, article.content, digest,
                    now, now if changed else old["updated_at"], notice_id))
            from urllib.parse import urlsplit
            domain = urlsplit(url).hostname
            matches = self.db.execute("SELECT id FROM units WHERE official_domain=?",(domain,)).fetchall()
            unit = matches[0] if len(matches)==1 else None
            content_source_id = article.content_source_id or (unit[0] if unit else None)
            self.db.execute("UPDATE notices SET content_domain=?,content_source_id=? WHERE id=?",(domain,content_source_id,notice_id))
            self.db.execute("DELETE FROM notice_links WHERE notice_id=?",(notice_id,))
            self.db.executemany("INSERT INTO notice_links VALUES (?,?,?,?)",[(notice_id,*link) for link in links])
            self.db.execute("""INSERT INTO notice_sources (notice_id,source_id,first_seen_at,last_seen_at,published_at) VALUES (?,?,?,?,?)
                ON CONFLICT(notice_id,source_id) DO UPDATE SET last_seen_at=excluded.last_seen_at,published_at=excluded.published_at""",
                (notice_id, source_id, now, now, discovery_published_at or article.published_at))
            from .auto_publish import parsed
            parsed(self.db,notice_id,now,changed=result=='updated')
            from .recheck import succeeded
            succeeded(self.db,notice_id,now)
        return result

    def fail_notice(self, url, failure, *, at=None):
        from .auto_publish import failed
        status = 'parse_failed' if isinstance(failure, ParseError) else 'unavailable'
        now = at or utc_now()
        with self.db:
            failed(self.db,url,status,failure.category,now)
            row = self.db.execute('SELECT id FROM notices WHERE canonical_url=?', (url,)).fetchone()
            if row:
                from .recheck import unsuccessful
                unsuccessful(self.db,row['id'],now,failure.category)

    def request_event(self, source_id, url, requested_at, attempt, status_code, error, elapsed_ms):
        with self.db:
            self.db.execute("""INSERT INTO request_events
                (source_id,url,requested_at,attempt,status_code,error,elapsed_ms) VALUES (?,?,?,?,?,?,?)""",
                (source_id, url, requested_at, attempt, status_code, error, elapsed_ms))

    def finish_run(self, source_id: str, started: str, counts: dict, error: str | None, *, failure=None):
        now = utc_now()
        with self.db:
            self.db.execute("""INSERT INTO collection_runs
                (source_id,started_at,finished_at,inserted,updated,unchanged,failed,error) VALUES (?,?,?,?,?,?,?,?)""",
                (source_id, started, now, counts["inserted"], counts["updated"], counts["unchanged"], counts["failed"], error))
            self.db.execute("UPDATE collection_runs SET pages_read=?,parse_failures=?,elapsed_seconds=?,duplicates=? WHERE id=last_insert_rowid()",
                            (counts.get("pages_read",1),counts.get("parse_failures",0),counts.get("elapsed_seconds",0),counts.get("duplicates",counts["updated"]+counts["unchanged"])))
            self.db.execute("""UPDATE sources SET status=?, last_attempt_at=?, last_error=?,
                last_verified_at=CASE WHEN ? IS NULL THEN ? ELSE last_verified_at END WHERE id=?""",
                ("failed" if error else "verified", now, error, error, now, source_id))
            self.db.execute("UPDATE sources SET error_category=?,error_scope=?,retryable=?,last_checked_at=? WHERE id=?",
                            (getattr(failure,"category",None),getattr(failure,"scope",None),int(getattr(failure,"retryable",True)),now,source_id))

    def links(self, notice_id: int, kind="body"):
        return self.db.execute("SELECT name,url FROM notice_links WHERE notice_id=? AND kind=? ORDER BY url",(notice_id,kind)).fetchall()

    def feed_rows(self, limit: int):
        return self.db.execute("""SELECT * FROM notices
            WHERE length(trim(content)) > 0 ORDER BY COALESCE(published_at,substr(discovered_at,1,10)) DESC,
            discovered_at DESC, canonical_url ASC LIMIT ?""", (limit,)).fetchall()

    def origins(self, notice_id: int):
        return self.db.execute("""SELECT s.college,s.column_name,s.column_url FROM sources s
            JOIN notice_sources ns ON ns.source_id=s.id WHERE ns.notice_id=? ORDER BY s.id""", (notice_id,)).fetchall()
