"""Offline, stopped-writer SQLite bundles; restores always use a new directory.

No services are stopped/started here. Queue path/inode metadata and publisher
ownership are rebound only in restored copies, without changing jobs/budgets,
source health, historical decisions or global first-featured timestamps.
"""
from contextlib import closing, contextmanager, ExitStack
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time

from .analysis_models import fingerprint
from .async_queue import JobQueue
from .models import utc_now
from .rss_service import StateLease

FORMAT = 'cdu-offline-backup-v1'
STATE_KIND = 'm4b-auto-rss-state-v1'
FILES = {'raw.sqlite3', 'jobs.sqlite3', 'subscriptions.sqlite3'}
MAX_BYTES = 512 * 1024 * 1024


def safe_path(value):
    path = Path(value).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Symlink paths are not supported')
    return path.resolve()


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def existing_database(value):
    path = safe_path(value)
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Missing or oversized database')
    if any(Path(str(path) + s).is_symlink() for s in ('-journal', '-wal', '-shm')):
        raise ValueError('Unsafe SQLite sidecar')
    return path


@contextmanager
def reader(path):
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=0)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute('PRAGMA journal_mode').fetchone()[0].lower() not in {'delete', 'truncate', 'persist'}:
            raise ValueError('Rollback journaling required')
        if [tuple(r) for r in db.execute('PRAGMA quick_check')] != [('ok',)]:
            raise ValueError('Database integrity check failed')
        yield db


def tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def raw_check(db):
    if not {'notices', 'notice_sources', 'notice_health', 'auto_publications'} <= tables(db):
        raise ValueError('Not an automatic radar database')


def metadata(db):
    return dict(db.execute('SELECT key,value FROM metadata'))


def check_queue(db, original_path, original_identity):
    names = tables(db)
    if not {'metadata', 'jobs', 'attempts'} <= names or not names <= {'metadata', 'jobs', 'attempts', 'input_failures'}:
        raise ValueError('Not an M7 job database')
    data = metadata(db)
    if any(data.get(k) != v for k, v in {'schema': 'm7-queue-v1', 'raw_path': original_path,
                                       'raw_identity': original_identity}.items()):
        raise ValueError('Queue identity mismatch')


def check_subscriptions(db, original_path):
    if tables(db) != {'metadata', 'first_featured', 'collection'} or metadata(db) != {
            'schema': 'rss-v1-collection', 'source': fingerprint(original_path)}:
        raise ValueError('Subscription identity mismatch')


def backup(raw_db, state_dir, output, *, analysis_db=None, quiesced=False, revision=None):
    if quiesced is not True:
        raise ValueError('Stop all writers and explicitly confirm quiescence')
    raw, state, destination = existing_database(raw_db), safe_path(state_dir), safe_path(output)
    if destination.exists() or destination.is_relative_to(state):
        raise ValueError('Use a NEW independent backup directory')
    owner = state / 'owner.json'
    if not safe_path(owner).is_file():
        raise ValueError('Existing publisher ownership required')
    sources = {'raw.sqlite3': raw}
    if analysis_db is not None:
        sources['jobs.sqlite3'] = existing_database(analysis_db)
    subscription = state / 'subscriptions.sqlite3'
    if subscription.exists() or subscription.is_symlink():
        sources['subscriptions.sqlite3'] = existing_database(subscription)
    if len(set(sources.values())) != len(sources):
        raise ValueError('Database roles must be independent')
    stat = raw.stat()
    identity = fingerprint([str(raw), stat.st_dev, stat.st_ino])
    with ExitStack() as stack:
        # Refuses an active publisher; this never kills or stops a service.
        stack.enter_context(closing(StateLease(state, raw, kind=STATE_KIND)))
        databases = {name: stack.enter_context(reader(path)) for name, path in sources.items()}
        raw_check(databases['raw.sqlite3'])
        if 'jobs.sqlite3' in databases:
            check_queue(databases['jobs.sqlite3'], str(raw), identity)
        if 'subscriptions.sqlite3' in databases:
            check_subscriptions(databases['subscriptions.sqlite3'], str(raw))
        before = {name: sha256(path) for name, path in sources.items()}
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        entries = []
        deadline = time.monotonic() + 30

        def progress(*_):
            if time.monotonic() > deadline:
                raise TimeoutError('Backup time budget exhausted')

        for name, db in databases.items():
            target = destination / name
            with closing(sqlite3.connect(target)) as copy:
                db.backup(copy, pages=128, progress=progress, sleep=.01)
                if copy.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise ValueError('Snapshot integrity check failed')
            entries.append({'name': name, 'bytes': target.stat().st_size, 'sha256': sha256(target)})
        if before != {name: sha256(path) for name, path in sources.items()}:
            raise ValueError('Source changed during backup')
        manifest = {'format': FORMAT, 'created_at': utc_now(), 'revision': revision,
                    'original_raw_path': str(raw), 'original_raw_identity': identity,
                    'writers_confirmed_quiesced': True, 'files': entries,
                    'source_database_hashes_unchanged': True}
        with (destination / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
    return {'directory': str(destination), 'databases': len(entries), 'source_unchanged': True}


def verified_bundle(directory):
    root = safe_path(directory)
    manifest_path = safe_path(root / 'manifest.json')
    if not manifest_path.is_file() or manifest_path.stat().st_size > 65536:
        raise ValueError('Missing or oversized manifest')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('format') != FORMAT or manifest.get('writers_confirmed_quiesced') is not True:
        raise ValueError('Invalid backup manifest')
    entries = manifest.get('files')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 3:
        raise ValueError('Invalid database collection')
    names = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {'name', 'bytes', 'sha256'}:
            raise ValueError('Invalid file record')
        name = entry['name']
        if name not in FILES or name in names or type(entry['bytes']) is not int:
            raise ValueError('Unexpected or duplicate bundle file')
        names.append(name)
        path = existing_database(root / name)
        if path.stat().st_size != entry['bytes'] or sha256(path) != entry['sha256']:
            raise ValueError('Snapshot checksum mismatch')
    if 'raw.sqlite3' not in names or {p.name for p in root.iterdir()} != {*names, 'manifest.json'}:
        raise ValueError('Incomplete or unexpected backup files')
    for key in ('original_raw_path', 'original_raw_identity'):
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            raise ValueError('Missing source identity')
    with ExitStack() as stack:
        databases = {name: stack.enter_context(reader(root / name)) for name in names}
        raw_check(databases['raw.sqlite3'])
        if 'jobs.sqlite3' in databases:
            check_queue(databases['jobs.sqlite3'], manifest['original_raw_path'], manifest['original_raw_identity'])
        if 'subscriptions.sqlite3' in databases:
            check_subscriptions(databases['subscriptions.sqlite3'], manifest['original_raw_path'])
    return root, manifest


def restore(bundle, output):
    source, manifest = verified_bundle(bundle)
    destination = safe_path(output)
    if destination.exists() or destination.is_relative_to(source):
        raise ValueError('Restore requires a NEW directory; existing data is never replaced')
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    raw = destination / 'raw.sqlite3'
    shutil.copyfile(source / 'raw.sqlite3', raw)
    # Fresh ownership/OS lock; old publisher.lock and cached XML are never copied.
    state = destination / 'http-state'
    with closing(StateLease(state, raw, kind=STATE_KIND)):
        pass
    names = {entry['name'] for entry in manifest['files']}
    if 'jobs.sqlite3' in names:
        jobs = destination / 'jobs.sqlite3'
        shutil.copyfile(source / 'jobs.sqlite3', jobs)
        stat = raw.stat()
        with closing(sqlite3.connect(jobs)) as db:
            with db:
                db.execute('UPDATE metadata SET value=? WHERE key=?', (str(raw), 'raw_path'))
                db.execute('UPDATE metadata SET value=? WHERE key=?',
                           (fingerprint([str(raw), stat.st_dev, stat.st_ino]), 'raw_identity'))
        with JobQueue(jobs, raw, read_only=True) as queue:
            queue.ensure_source()
    if 'subscriptions.sqlite3' in names:
        target = state / 'subscriptions.sqlite3'
        shutil.copyfile(source / 'subscriptions.sqlite3', target)
        with closing(sqlite3.connect(target)) as db:
            with db:
                db.execute('UPDATE metadata SET value=? WHERE key=?', (fingerprint(str(raw)), 'source'))
                db.execute('DELETE FROM collection')  # Publisher must revalidate before serving.
    # Validate restored metadata without touching source state or resetting health.
    with reader(raw) as db:
        raw_check(db)
    if sha256(raw) != next(e['sha256'] for e in manifest['files'] if e['name'] == 'raw.sqlite3'):
        raise ValueError('Restored raw database changed')
    report = {'format': FORMAT, 'created_at': utc_now(), 'raw_db': str(raw), 'state_dir': str(state),
              'analysis_db': str(destination / 'jobs.sqlite3') if 'jobs.sqlite3' in names else None,
              'path_identity_rebound_in_copies_only': True, 'cached_collection_invalidated': True,
              'services_started': False, 'raw_bytes_unchanged': True}
    with (destination / 'restore.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report
