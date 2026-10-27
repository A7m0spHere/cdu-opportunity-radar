"""Private publisher-owned collection and global (not per-filter) first times.

The source DB and human history are untouched. Back up this sidecar with the
service state directory; startup never serves its old collection unchecked.
"""
from contextlib import contextmanager
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from .analysis_models import fingerprint, reference_datetime

SCHEMA = '''
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE first_featured (guid TEXT PRIMARY KEY, first_featured_at TEXT NOT NULL);
CREATE TABLE collection (id INTEGER PRIMARY KEY CHECK(id=1), token TEXT NOT NULL, decisions TEXT NOT NULL);
'''


@contextmanager
def connection(path, raw_path, *, write=False):
    path = Path(path)
    if any(Path(str(path) + suffix).is_symlink() for suffix in ('', '-journal', '-wal', '-shm')):
        raise ValueError('Unsafe subscription state')
    db = sqlite3.connect(path.as_uri() + ('?mode=rwc' if write else '?mode=ro'), uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        if db.execute('PRAGMA journal_mode').fetchone()[0].lower() not in {'delete', 'truncate', 'persist'}:
            raise ValueError('Subscription rollback journaling required')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables and write:
            db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
            db.execute('INSERT INTO metadata VALUES (?,?)', ('source', fingerprint(str(Path(raw_path).resolve()))))
            db.execute('INSERT INTO metadata VALUES (?,?)', ('schema', 'rss-v1-collection'))
            db.commit()
        elif tables != {'metadata', 'first_featured', 'collection'}:
            raise ValueError('Unexpected subscription state')
        db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        expected = {'source': fingerprint(str(Path(raw_path).resolve())), 'schema': 'rss-v1-collection'}
        if dict(db.execute('SELECT key,value FROM metadata')) != expected:
            raise ValueError('Subscription identity mismatch')
        yield db
        if write:
            db.commit()
    finally:
        db.rollback()
        db.close()


def persist(db, token, decisions, now):
    for guid, decision in decisions.items():
        if decision['decision'] == 'selected':
            if not db.execute('SELECT 1 FROM first_featured WHERE guid=?',(guid,)).fetchone():
                db.execute('INSERT INTO first_featured VALUES (?,?)', (guid, now.isoformat()))
    encoded=json.dumps(decisions,ensure_ascii=False,sort_keys=True)
    old=db.execute('SELECT token,decisions FROM collection WHERE id=1').fetchone()
    if not old or old['token']!=token or old['decisions']!=encoded:
        db.execute('INSERT OR REPLACE INTO collection VALUES (1,?,?)',(token,encoded))


def read(db, token, decisions, now):
    row = db.execute('SELECT token,decisions FROM collection WHERE id=1').fetchone()
    if not row or row['token'] != token or json.loads(row['decisions']) != decisions:
        raise ValueError('Featured collection changed; refresh required')
    first = {}
    for guid, decision in decisions.items():
        if decision['decision'] != 'selected':
            continue
        row = db.execute('SELECT first_featured_at FROM first_featured WHERE guid=?', (guid,)).fetchone()
        if not row or datetime.fromisoformat(row[0]).tzinfo is None or reference_datetime(row[0]) > now:
            raise ValueError('Missing or invalid first featured time')
        first[guid] = row[0]
    return first
