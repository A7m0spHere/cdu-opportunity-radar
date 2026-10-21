"""Local RSS-to-draft contract. No model, bot API, scheduler or send operation."""
from contextlib import contextmanager
from datetime import date
import html
import json
from pathlib import Path
import re
import sqlite3
from string import Template
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx

from .analysis_models import fingerprint
from .classification import DOMAINS, TOPICS
from .privacy import sanitize
from .review_api import canonical_path, split_query, RSS_ROUTES
from .rss_v1 import NS, MAX_BYTES, MAX_ITEMS

V2 = 'urn:cdu-radar:rss:v2'
DEFAULT_TEMPLATE = ('### $title\n\n$summary\n\n'
    '- 发布单位：$source\n- 来源日期：$source_date\n- 报名状态：$registration\n'
    '- 参与资格：未知，请核对原文\n- 官方原文：<$url>\n- 草稿类型：$change\n')
FIELDS = {'title','summary','source','source_date','registration','url','change'}


class FeedError(ValueError):
    def __init__(self, status):
        self.status = status
        super().__init__(status)


def loopback_url(value):
    if not isinstance(value, str) or len(value) > 2200:
        raise ValueError('Invalid fixed feed URL')
    parsed = urlsplit(value)
    if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.username
            or parsed.password or parsed.fragment or not parsed.port or parsed.port > 65535):
        raise ValueError('This offline consumer requires literal http://127.0.0.1:PORT')
    if parsed.path not in RSS_ROUTES:
        raise ValueError('RSS v2 route required')
    options = {'sources': []}
    filters = split_query(parsed.path + ('?' + parsed.query if parsed.query else ''), options, [])
    if filters['source'] != 'all' or filters['category'] != 'all':
        raise ValueError('Source/category filters require a future catalog-aware adapter')
    canonical = canonical_path(filters)
    return f'http://127.0.0.1:{parsed.port}' + canonical, filters


def config(data):
    if not isinstance(data, dict) or set(data) != {'schema_version','subscriptions'} or data['schema_version'] != 1:
        raise ValueError('Invalid consumer config')
    rows = data['subscriptions']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10:
        raise ValueError('Configure 1..10 fixed subscriptions')
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) - {'name','url','template'} or not {'name','url'} <= set(row):
            raise ValueError('Invalid subscription')
        name = row['name']
        if not isinstance(name, str) or not 1 <= len(name) <= 80 or '\n' in name:
            raise ValueError('Invalid direction name')
        url, filters = loopback_url(row['url'])
        template = row.get('template', DEFAULT_TEMPLATE)
        if not isinstance(template, str) or not 1 <= len(template) <= 8000:
            raise ValueError('Invalid draft template')
        parser = Template(template)
        if not parser.is_valid() or set(parser.get_identifiers()) != FIELDS:
            raise ValueError('Template must retain all official-source and uncertainty fields')
        result.append(dict(name=name,url=url,filters=filters,template=template,
                           identity=fingerprint(['rss-consumer-v1',url])))
    if len({r['identity'] for r in result}) != len(result):
        raise ValueError('Duplicate subscription URL')
    return result


def official_url(value):
    p = urlsplit(value)
    if (p.scheme not in {'http','https'} or not p.hostname or not
            (p.hostname == 'cdu.edu.cn' or p.hostname.endswith('.cdu.edu.cn'))
            or p.username or p.password or p.port not in {None,80,443}
            or any(c in value for c in '\r\n<>')):
        raise FeedError('invalid_official_url')
    return value


def parse_feed(body, subscription):
    if len(body) > MAX_BYTES or re.search(br'<!\s*(DOCTYPE|ENTITY)', body, re.I):
        raise FeedError('invalid_xml')
    try:
        root = ET.fromstring(body)
        if root.tag != 'rss' or root.get('version') != '2.0' or len(root.findall('channel')) != 1:
            raise ValueError()
        channel = root.find('channel')
        def meta(node, name, namespace=NS):
            return node.findtext('{' + namespace + '}' + name)
        if meta(channel,'canonical_path') != urlsplit(subscription['url']).path + ('?' + urlsplit(subscription['url']).query if urlsplit(subscription['url']).query else ''):
            raise FeedError('filter_mismatch')
        if meta(channel,'truncated') != 'false':
            raise FeedError('truncated_feed')
        if subscription['filters']['selection'] == 'featured':
            if meta(channel,'analysis_state') != 'ready':
                raise FeedError('featured_incomplete')
            coverage = json.loads(meta(channel,'coverage'))
            if coverage['total'] != coverage['complete'] or coverage['pending'] or coverage['failed']:
                raise FeedError('featured_incomplete')
        nodes = channel.findall('item')
        if len(nodes) > MAX_ITEMS or int(meta(channel,'returned')) != len(nodes) or int(meta(channel,'total')) != len(nodes):
            raise ValueError()
        rows = []
        for node in nodes:
            guid, title, link = (node.findtext(k) for k in ['guid','title','link'])
            source = node.findtext('source')
            if not guid or len(guid)>1000 or not title or not source:
                raise ValueError()
            if node.find('source').get('url') != link:
                raise ValueError()
            row = dict(guid=guid,title=sanitize(title,False),url=official_url(link),source=sanitize(source,False),
                summary=sanitize(html.unescape(node.findtext('description') or '')),
                source_date=meta(node,'source_published_date'),registration=meta(node,'registration_status'))
            if row['registration'] not in {'possible','closed','unknown'} or not row['source_date'] or not row['summary']:
                raise ValueError()
            if row['source_date'] != 'unknown':
                date.fromisoformat(row['source_date'])
            for field, namespace in [('type',V2),('domains',V2),('topics',V2),('listing',NS)]:
                value = meta(node,field,namespace)
                if value is None:raise ValueError()
                row[field] = value.split(',') if field in {'domains','topics'} and value else [] if field in {'domains','topics'} else value
            f = subscription['filters']
            if f['audience']=='undergraduate' and meta(node,'undergraduate_state',V2)!='relevant':
                raise FeedError('filter_mismatch')
            for key, actual in [('type',row['type']),('status',row['registration']),('view',row['listing'])]:
                if f[key] not in {'all',actual}:raise FeedError('filter_mismatch')
            for key, field in [('domain','domains'),('topic','topics')]:
                if f[key] == 'unclassified' and row[field] or f[key] not in {'all','unclassified'} and not set(f[key].split(',')) <= set(row[field]):
                    raise FeedError('filter_mismatch')
            day = row['source_date'] if row['source_date'] != 'unknown' else None
            if (f['published']=='known' and day is None or f['published']=='unknown' and day is not None
                    or f['from'] and (day is None or day<f['from']) or f['to'] and (day is None or day>f['to'])):
                raise FeedError('filter_mismatch')
            row['content_hash'] = fingerprint(row)
            rows.append(row)
        if len({r['guid'] for r in rows}) != len(rows):raise ValueError()
        return rows
    except FeedError:
        raise
    except (ET.ParseError, ValueError, TypeError, KeyError, AttributeError) as exc:
        raise FeedError('invalid_xml') from exc


def fetch(subscription, client):
    try:
        started=time.monotonic()
        with client.stream('GET',subscription['url'],headers={'Accept':'application/rss+xml'}) as response:
            if response.status_code != 200:
                raise FeedError({503:'unavailable',429:'rate_limited'}.get(response.status_code,'http_error'))
            body = bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic()-started>15:raise FeedError('timeout')
                body.extend(chunk)
                if len(body)>MAX_BYTES:raise FeedError('response_too_large')
        return parse_feed(bytes(body),subscription)
    except httpx.TimeoutException as exc:raise FeedError('timeout') from exc
    except httpx.HTTPError as exc:raise FeedError('connection_failed') from exc


@contextmanager
def state_database(path):
    path = Path(path).absolute()
    if path.is_symlink() or any(str(p).lower().endswith(('-wal','-shm','-journal')) for p in (path,path.resolve())):
        raise ValueError('Independent private state path required')
    if path.exists():
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as check:
            if {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")} != {'consumer_subscriptions','consumer_items','consumer_runs'}:
                raise ValueError('Existing database is not consumer state')
    path.parent.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(path,timeout=3)
    try:
        db.executescript('''CREATE TABLE IF NOT EXISTS consumer_subscriptions (identity TEXT PRIMARY KEY,url TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS consumer_items (subscription TEXT NOT NULL,guid TEXT NOT NULL,hash TEXT NOT NULL,PRIMARY KEY(subscription,guid));
            CREATE TABLE IF NOT EXISTS consumer_runs (id INTEGER PRIMARY KEY,payload TEXT NOT NULL);''')
        yield db
    finally:db.close()


def markdown(report):
    text = '# 校园信息简报草稿\n\n仅供审查；QQ 实际投递 0 条。\n\n'
    for channel in report['subscriptions']:
        text += '## ' + escape_markdown(channel['name']) + '\n\n'
        text += '消费状态：' + channel['status'] + '；本轮草稿 ' + str(len(channel['drafts'])) + ' 条。\n\n'
        if channel['status'] not in {'ready','empty','initialized'}:
            text += '本轮未取得可用 Feed，不能据此判断没有重要信息；未使用旧缓存生成报道。\n\n'
        elif channel['status']=='initialized':
            text += '首次订阅仅建立水位，不补发既有条目。\n\n'
        text += '\n'.join(d['markdown'] for d in channel['drafts']) + '\n'
    return text


def escape_markdown(value):
    return re.sub(r'([\\`*_{}\[\]()<>#+!|])',r'\\\1',value)


def poll(subscriptions, state_path, *, client=None):
    results = []
    own = client is None
    client = client or httpx.Client(timeout=10,follow_redirects=False,trust_env=False,
                                   limits=httpx.Limits(max_connections=1,max_keepalive_connections=1))
    try:
        # Fetch first, then serialize state transitions. Two pollers cannot emit the same change.
        fetched = []
        for s in subscriptions:
            try:fetched.append((s,fetch(s,client),None))
            except FeedError as exc:fetched.append((s,[],exc.status))
        with state_database(state_path) as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                for s, rows, error in fetched:
                    output = dict(name=s['name'],identity=s['identity'],url=s['url'],status=error or 'ready',drafts=[])
                    if error:results.append(output);continue
                    initialized = bool(db.execute('SELECT 1 FROM consumer_subscriptions WHERE identity=?',(s['identity'],)).fetchone())
                    if not initialized:
                        db.execute('INSERT INTO consumer_subscriptions VALUES (?,?)',(s['identity'],s['url']))
                        output['status']='initialized'
                    elif not rows:output['status']='empty'
                    for row in rows:
                        old = db.execute('SELECT hash FROM consumer_items WHERE subscription=? AND guid=?',(s['identity'],row['guid'])).fetchone()
                        if initialized and (not old or old[0]!=row['content_hash']):
                            change = 'updated' if old else 'new'
                            values = {k:escape_markdown(row[k]) for k in ['title','summary','source']}
                            values.update(source_date=row['source_date'] if row['source_date']!='unknown' else '未知',
                                registration={'possible':'可能仍可参与，需核验','closed':'明确已截止','unknown':'未知，需核验'}[row['registration']],
                                url=row['url'],change='原文更新' if old else '新增')
                            output['drafts'].append(dict(guid=row['guid'],change=change,markdown=Template(s['template']).substitute(values)))
                        db.execute('INSERT OR REPLACE INTO consumer_items VALUES (?,?,?)',(s['identity'],row['guid'],row['content_hash']))
                    results.append(output)
                report = dict(schema_version=1,created_at=time.time(),subscriptions=results,qq_deliveries=0,model_requests=0)
                cursor = db.execute('INSERT INTO consumer_runs(payload) VALUES (?)',(json.dumps(report,ensure_ascii=False),))
                report['run_id']=cursor.lastrowid
                db.execute('UPDATE consumer_runs SET payload=? WHERE id=?',(json.dumps(report,ensure_ascii=False),report['run_id']))
                db.commit()
                return report
            except BaseException:db.rollback();raise
    finally:
        if own:client.close()


def export_run(state_path, directory, run_id=None):
    # A failed filesystem export can be replayed without fetching or advancing the watermarks.
    with sqlite3.connect(Path(state_path).resolve(strict=True).as_uri()+'?mode=ro',uri=True) as db:
        row = db.execute('SELECT payload FROM consumer_runs WHERE id=?',(run_id,)).fetchone() if run_id else db.execute('SELECT payload FROM consumer_runs ORDER BY id DESC LIMIT 1').fetchone()
    if not row:raise ValueError('No stored consumer run')
    report = json.loads(row[0]);directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=False)
    (directory/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (directory/'briefing.md').write_text(markdown(report),encoding='utf-8')
    return report
