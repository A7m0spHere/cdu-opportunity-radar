"""Parser-versioned cooling for unusable list candidates, never source success."""
from functools import lru_cache
from importlib.resources import files
import hashlib
import time

def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS discovery_failures (
        source_id TEXT NOT NULL,url TEXT NOT NULL,parser_version TEXT NOT NULL,
        failures INTEGER NOT NULL,next_at REAL NOT NULL,error TEXT NOT NULL,
        PRIMARY KEY(source_id,url,parser_version))''')
    db.commit()

@lru_cache(maxsize=1)
def parser_version():
    return hashlib.sha256(files('cdu_radar').joinpath('adapters.py').read_bytes()).hexdigest()

def cooling(db,source,url,now=None):
    row=db.execute('SELECT next_at FROM discovery_failures WHERE source_id=? AND url=? AND parser_version=?',
                   (source,url,parser_version())).fetchone()
    return bool(row and row[0]>(time.time() if now is None else now))

def failed(db,source,url,error,now=None):
    now=time.time() if now is None else now;version=parser_version()
    old=db.execute('SELECT failures FROM discovery_failures WHERE source_id=? AND url=? AND parser_version=?',(source,url,version)).fetchone()
    count=(old[0] if old else 0)+1
    delay=86400 if error.endswith(':expired_stub') else 21600 if error.startswith('html_parse') else min(21600,3600*2**min(count-1,3))
    with db:db.execute('INSERT OR REPLACE INTO discovery_failures VALUES(?,?,?,?,?,?)',(source,url,version,count,now+delay,error))

def succeeded(db,source,url):
    with db:db.execute('DELETE FROM discovery_failures WHERE source_id=? AND url=?',(source,url))
