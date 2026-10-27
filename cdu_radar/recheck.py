"""Persistent, bounded original-page checks independent of list-page discovery."""
from dataclasses import replace
from datetime import timedelta, timezone
import math
from urllib.parse import urlsplit

from .adapters import ARTICLE_PATH, article_adapter
from .analysis_models import reference_datetime
from .http import RequestBudgetExceeded
from .models import ParseError, RadarError
from .sources import content_source, load_sources
from .auto_publish import trusted_publisher

INTERVAL = timedelta(hours=24)
CLAIM_TTL = timedelta(minutes=15)


def stamp(at):
    return reference_datetime(at).astimezone(timezone.utc).isoformat()


def initialize(db):
    db.execute('''CREATE TABLE IF NOT EXISTS notice_rechecks (
        notice_id INTEGER PRIMARY KEY REFERENCES notices(id),
        state TEXT NOT NULL CHECK(state IN ('pending','running','ready','failed')),
        last_attempt_at TEXT, last_success_at TEXT, next_check_at TEXT NOT NULL,
        failures INTEGER NOT NULL DEFAULT 0, last_error TEXT)''')
    db.execute('''CREATE TABLE IF NOT EXISTS collection_cursor (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), next_source TEXT NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS notice_rechecks_due ON notice_rechecks(next_check_at,notice_id)')
    # Existing health is the only success evidence when upgrading an old DB.
    rows = db.execute('''SELECT n.id,h.status,h.checked_at FROM notices n
        LEFT JOIN notice_health h ON h.notice_id=n.id
        LEFT JOIN notice_rechecks q ON q.notice_id=n.id WHERE q.notice_id IS NULL''').fetchall()
    for row in rows:
        success = row['checked_at'] if row['status'] == 'valid' else None
        due = reference_datetime(success) + INTERVAL if success else reference_datetime()
        db.execute('INSERT INTO notice_rechecks (notice_id,state,last_success_at,next_check_at) VALUES (?,\'pending\',?,?)',
                   (row['id'], stamp(success) if success else None, stamp(due)))
    db.commit()


def succeeded(db, ident, at):
    db.execute('''INSERT INTO notice_rechecks VALUES (?,'ready',?,?,?,0,NULL)
        ON CONFLICT(notice_id) DO UPDATE SET state='ready',last_attempt_at=excluded.last_attempt_at,
        last_success_at=excluded.last_success_at,next_check_at=excluded.next_check_at,failures=0,last_error=NULL''',
        (ident, stamp(at), stamp(at), stamp(reference_datetime(at) + INTERVAL)))


def unsuccessful(db, ident, at, reason):
    old = db.execute('SELECT failures FROM notice_rechecks WHERE notice_id=?', (ident,)).fetchone()
    failures = (old[0] if old else 0) + 1
    due = reference_datetime(at) + timedelta(hours=min(24, 2 ** min(failures - 1, 5)))
    db.execute('''INSERT INTO notice_rechecks (notice_id,state,last_attempt_at,next_check_at,failures,last_error)
        VALUES (?,'failed',?,?,?,?) ON CONFLICT(notice_id) DO UPDATE SET state='failed',
        last_attempt_at=excluded.last_attempt_at,next_check_at=excluded.next_check_at,
        failures=excluded.failures,last_error=excluded.last_error''', (ident, stamp(at), stamp(due), failures, reason))


def recheck(store, fetcher, sources, *, limit=3, stop_event=None, clock=reference_datetime):
    if limit <= 0:
        raise ValueError('recheck limit must be positive')
    catalog = [s for s in load_sources() if s.status=='verified' and s.parser_supported and s.adapter]
    current_ids = {s.id for s in catalog}
    allowed = {s.id:s for s in sources if s.id in current_ids and s.status=='verified' and s.parser_supported and s.adapter}
    results = []
    # Short claims are committed before I/O; crashes retry after at most 15 min.
    for _ in range(limit):
        if stop_event is not None and stop_event.is_set():
            break
        at = clock()
        placeholders = ','.join('?' for _ in allowed)
        if not placeholders:
            break
        with store.db:
            store.db.execute('BEGIN IMMEDIATE')
            row = store.db.execute(f'''SELECT n.*,q.next_check_at FROM notice_rechecks q
                JOIN notices n ON n.id=q.notice_id
                LEFT JOIN notice_health h ON h.notice_id=n.id
                WHERE q.next_check_at<=? AND COALESCE(h.status,'')!='withdrawn'
                AND EXISTS (SELECT 1 FROM notice_sources ns WHERE ns.notice_id=n.id AND ns.source_id IN ({placeholders}))
                ORDER BY q.next_check_at,n.id LIMIT 1''', (stamp(at), *allowed)).fetchone()
            if row is None:
                break
            store.db.execute('''UPDATE notice_rechecks SET state='running',last_attempt_at=?,next_check_at=?
                WHERE notice_id=?''', (stamp(at), stamp(at+CLAIM_TTL), row['id']))
        url = row['canonical_url']
        origin = store.db.execute(f'''SELECT source_id FROM notice_sources WHERE notice_id=?
            AND source_id IN ({placeholders}) ORDER BY source_id LIMIT 1''', (row['id'], *allowed)).fetchone()[0]
        fetcher.source_id = origin
        try:
            if not ARTICLE_PATH.fullmatch(urlsplit(url).path):
                raise ParseError('Unsupported recheck article path')
            unit, _ = content_source(url)
            if not trusted_publisher(store.db, row, catalog) or row['content_source_id'] != unit:
                raise ParseError('Stored original publisher mismatch')
            detail, final = fetcher.fetch(url)
            # Changing identity through a redirect needs manual source reconciliation.
            if final != url:
                raise ParseError('Original URL changed during recheck')
            final_unit, _ = content_source(final)
            article = article_adapter(final).parse_article(detail, final)
            article = replace(article, content_source_id=final_unit)
            checked = clock()
            # No list timestamp fallback: the old discovery date must not mask an update.
            result = store.save_notice(origin, url, article, seen_at=checked.isoformat(), discovered=False)
            results.append({'notice_id':row['id'], 'result':result})
        except RequestBudgetExceeded:
            with store.db:
                store.db.execute("UPDATE notice_rechecks SET state='pending',next_check_at=? WHERE notice_id=?",
                                 (stamp(clock()+timedelta(minutes=5)), row['id']))
            break
        except RadarError as exc:
            store.fail_notice(url, exc, at=clock().isoformat())
            results.append({'notice_id':row['id'], 'result':'failed', 'error_category':exc.category})
    return results


def collect_cycle(store, fetcher, sources, *, limit=3, recheck_limit=3, request_budget=48, stop_event=None, cycle_interval=None):
    """Share one wire budget; an overdue backlog gets first access to it."""
    from .collect import collect
    if request_budget < 6 or request_budget > 500:
        raise ValueError('request budget must be between 6 and 500')
    if recheck_limit <= 0 or limit <= 0:
        raise ValueError('article limits must be positive')
    sources = sorted(sources, key=lambda source:source.id)
    cursor = store.db.execute('SELECT next_source FROM collection_cursor WHERE singleton=1').fetchone()
    if cursor:
        offset = next((i for i,s in enumerate(sources) if s.id>=cursor[0]), 0)
        sources = sources[offset:]+sources[:offset]
    start = fetcher.request_count
    original_limit = fetcher.request_limit
    ceiling = min(original_limit, start+request_budget) if original_limit is not None else start+request_budget
    due=store.db.execute('SELECT count(*) FROM notice_rechecks WHERE next_check_at<=?',(stamp(reference_datetime()),)).fetchone()[0]
    target=recheck_limit
    if cycle_interval is not None:
        if not math.isfinite(cycle_interval) or cycle_interval<60:raise ValueError('Invalid collection interval')
        # Capacity for the full inventory each day plus 50% headroom. Actual
        # wire calls (including robots) remain inside the unchanged total cap.
        inventory=store.db.execute("SELECT count(*) FROM notice_rechecks q LEFT JOIN notice_health h ON h.notice_id=q.notice_id WHERE COALESCE(h.status,'')!='withdrawn'").fetchone()[0]
        target=min(request_budget,max(target,math.ceil(inventory*cycle_interval/INTERVAL.total_seconds()*1.5),due))
    checks=[];rechecks_first=due>recheck_limit or cycle_interval is not None
    # Backlog gets first access to two thirds of the existing wire budget.
    # Short, committed claims remain independent of slow HTTP operations.
    try:
        if rechecks_first:
            fetcher.request_limit=min(ceiling,start+2*request_budget//3)
            checks=recheck(store,fetcher,sources,limit=target,stop_event=stop_event)
            fetcher.request_limit=ceiling
        else:fetcher.request_limit=min(ceiling,start+request_budget//2)
        discovery=collect(store,fetcher,sources,limit=limit,max_pages=1,
                          stop_event=stop_event,skip_known=True,tolerate_failures=3)
        if discovery:
            last = next(i for i,s in enumerate(sources) if s.id==discovery[-1]['source'])
            progress = sum(discovery[-1][key] for key in ('inserted','updated','unchanged','failed'))
            next_index = last if discovery[-1].get('error_category')=='budget' and not progress else last+1
            with store.db:
                store.db.execute('INSERT OR REPLACE INTO collection_cursor VALUES (1,?)',
                                 (sources[next_index%len(sources)].id,))
        fetcher.request_limit = ceiling
        if not rechecks_first:checks = recheck(store, fetcher, sources, limit=target, stop_event=stop_event)
        return {'discovery':discovery, 'rechecks':checks, 'requests':fetcher.request_count-start,'recheck_target':target,'due_before':due}
    finally:fetcher.request_limit=original_limit
