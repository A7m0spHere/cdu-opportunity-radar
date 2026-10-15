"""Fixed source/target consumer. No AstrBot imports, commands, tools or web API."""
import asyncio
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
import hashlib
from html.parser import HTMLParser
import ipaddress
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx

MAX_BYTES = 512 * 1024
MAX_ITEMS = 200
MAX_HISTORY = 50000  # Fail closed instead of pruning dedup tombstones.
SUBMIT_TIMEOUT_SECONDS = 8
SESSION = re.compile(r'[A-Za-z0-9_-]{1,64}:(?:GroupMessage|FriendMessage):[1-9][0-9]{4,19}')


def valid_target(value):
    return isinstance(value, str) and SESSION.fullmatch(value) is not None


@dataclass(frozen=True)
class Settings:
    enabled: bool = False
    feed_url: str = 'http://127.0.0.1:8765/approved.xml'
    target_umo: str = ''
    poll_interval_seconds: int = 30
    max_send_attempts: int = 2  # Persistent total budget, including failures.
    retry_limit: int = 0
    retry_delay_seconds: int = 60

    @classmethod
    def parse(cls, values):
        defaults = cls()
        data = {k: values.get(k, getattr(defaults, k)) for k in cls.__dataclass_fields__}
        if type(data['enabled']) is not bool:
            raise ValueError('enabled must be boolean')
        for key, low, high in [('poll_interval_seconds',15,3600), ('max_send_attempts',1,1000),
                               ('retry_limit',0,2), ('retry_delay_seconds',30,86400)]:
            if type(data[key]) is not int or not low <= data[key] <= high:
                raise ValueError('Invalid bounded setting: ' + key)
        url = data['feed_url']
        if not isinstance(url, str) or any(c.isspace() for c in url):
            raise ValueError('Invalid fixed RSS URL')
        parts = urlsplit(url)
        try:
            allowed = ipaddress.ip_address(parts.hostname).is_loopback
            port = parts.port
        except (ValueError, TypeError):
            allowed = False
        if (not allowed or parts.scheme != 'http' or not port or parts.username is not None
                or parts.password is not None or parts.path != '/approved.xml'
                or parts.query or parts.fragment):
            raise ValueError('Use an explicit HTTP loopback /approved.xml URL without credentials')
        if not isinstance(data['target_umo'], str) or (data['enabled'] and not valid_target(data['target_umo'])):
            raise ValueError('An enabled plugin requires one complete QQ session identifier')
        return cls(**data)

    @property
    def binding(self):
        return hashlib.sha256((self.feed_url + '\n' + self.target_umo).encode()).hexdigest()


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {'script','style'}:
            self.hidden += 1
        if tag in {'br','p','div','li'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script','style'}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, text):
        if not self.hidden:
            self.parts.append(text)


@dataclass(frozen=True)
class Item:
    guid: str
    digest: str
    message: str
    timestamp: float


def parse_feed(body):
    if not isinstance(body, bytes) or len(body) > MAX_BYTES:
        raise ValueError('Invalid RSS size')
    text = body.decode('utf-8-sig')
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.I):
        raise ValueError('DTD/entity declarations are forbidden')
    root = ET.fromstring(text)
    if root.tag != 'rss' or root.get('version') != '2.0' or len(root.findall('channel')) != 1:
        raise ValueError('Expected RSS 2.0 with one channel')
    raw = root.findall('./channel/item')
    if len(raw) > MAX_ITEMS:
        raise ValueError('Too many RSS items')
    result = []; seen = set()
    for node in raw:
        fields = {}
        for key, limit in [('guid',256), ('title',240), ('link',2048), ('description',20000), ('pubDate',128)]:
            nodes = node.findall(key)
            if len(nodes) != 1 or list(nodes[0]):
                raise ValueError('Expected one text field: ' + key)
            value = (nodes[0].text or '').strip()
            if not value or len(value) > limit or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value):
                raise ValueError('Invalid RSS field: ' + key)
            fields[key] = value
        if fields['guid'] in seen:
            raise ValueError('Duplicate GUID in one response')
        seen.add(fields['guid'])
        link = urlsplit(fields['link'])
        if link.scheme != 'https' or not link.hostname or link.username is not None or link.password is not None:
            raise ValueError('Expected HTTPS original link without credentials')
        date = parsedate_to_datetime(fields['pubDate'])
        if date.tzinfo is None:
            raise ValueError('pubDate must include timezone')
        plain = PlainText(); plain.feed(fields['description']); plain.close()
        description = '\n'.join(s.strip() for s in ''.join(plain.parts).splitlines() if s.strip())
        message = fields['title'] + '\n' + description + '\n官方原文：' + fields['link']
        if len(message) > 4000:
            raise ValueError('Message too long; do not silently remove unknown facts')
        digest = hashlib.sha256(message.encode()).hexdigest()
        result.append(Item(fields['guid'], digest, message, date.timestamp()))
    return sorted(result, key=lambda x: (x.timestamp, x.guid))


class HttpFetcher:
    def __init__(self):
        self.client = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=8,
                                       limits=httpx.Limits(max_connections=1))

    async def __call__(self, url):
        async with self.client.stream('GET', url, headers={'Accept':'application/rss+xml','Accept-Encoding':'identity'}) as response:
            if response.status_code != 200:
                return response.status_code, b''
            if response.headers.get('Content-Encoding','identity').lower() != 'identity':
                raise ValueError('Compressed responses are outside the bounded local RSS contract')
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_BYTES:
                    raise ValueError('RSS response too large')
            return 200, bytes(content)

    async def close(self):
        await self.client.aclose()


class Ledger:
    """Own SQLite only. Never resets a corrupt/mismatched database."""
    def __init__(self, root, binding):
        self.db = None; self.lock_file = None
        root = Path(root)
        if root.is_symlink():
            raise ValueError('Unsafe plugin state directory')
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'state.sqlite3'; lock = root / 'state.lock'
        if path.is_symlink() or lock.is_symlink():
            raise ValueError('Unsafe state files')
        self.lock_file = lock.open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if lock.stat().st_size == 0:
                    self.lock_file.write(b'0'); self.lock_file.flush()
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            new = not path.exists()
            self.db = sqlite3.connect(path.resolve().as_uri() + ('?mode=rwc' if new else '?mode=rw'), uri=True, timeout=1)
            self.db.row_factory = sqlite3.Row
            # Budget reservations must not inherit a weaker SQLite build default.
            self.db.execute('PRAGMA synchronous=FULL')
            if self.db.execute('PRAGMA synchronous').fetchone()[0] != 2:
                raise ValueError('Full durability is required for submission reservations')
            if new:
                self.db.executescript('''BEGIN;
                    CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                    CREATE TABLE entries (guid TEXT PRIMARY KEY,digest TEXT NOT NULL,status TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,next_retry REAL NOT NULL DEFAULT 0,reason TEXT NOT NULL DEFAULT '');
                    PRAGMA user_version=1; COMMIT;''')
                with self.db:
                    self.db.executemany('INSERT INTO meta VALUES (?,?)', [('binding',binding),('initialized','0'),('attempts','0')])
            if self.db.execute('PRAGMA user_version').fetchone()[0] != 1 or self.value('binding') != binding:
                raise ValueError('Unknown schema or source/target changed; keep history and review manually')
            recorded = self.value('attempts')
            entries = self.db.execute('SELECT * FROM entries').fetchall()
            if (self.value('initialized') not in {'0','1'} or not recorded.isdecimal()
                    or int(recorded) != sum(r['attempts'] for r in entries)
                    or (self.value('initialized') == '0' and entries)
                    or any(r['status'] not in {'baseline','pending','inflight','submitted','failed','uncertain','withdrawn','withheld'}
                           or type(r['attempts']) is not int or r['attempts'] < 0
                           or not isinstance(r['digest'],str) or re.fullmatch('[a-f0-9]{64}',r['digest']) is None
                           for r in entries)):
                raise ValueError('Inconsistent private ledger; do not reset or replay')
            with self.db:
                self.db.execute("UPDATE entries SET status='uncertain',reason='interrupted' WHERE status='inflight'")
        except BaseException:
            self.close(); raise

    def value(self, key):
        row = self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        if row is None:
            raise ValueError('Missing ledger metadata')
        return row[0]

    def observe(self, items):
        # Only called after a fully valid HTTP 200. No TTL pruning of tombstones.
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            baseline = self.value('initialized') == '0'
            old = {r['guid']:r for r in self.db.execute('SELECT * FROM entries')}
            if len(old) + sum(i.guid not in old for i in items) > MAX_HISTORY:
                raise ValueError('History capacity reached; do not prune and replay')
            active = {i.guid for i in items}
            for item in items:
                if item.guid not in old:
                    self.db.execute('INSERT INTO entries (guid,digest,status) VALUES (?,?,?)',
                                    (item.guid,item.digest,'baseline' if baseline else 'pending'))
                elif old[item.guid]['status'] in {'pending','failed'} and old[item.guid]['digest'] != item.digest:
                    self.db.execute("UPDATE entries SET status='withheld',reason='changed_before_submission' WHERE guid=?",(item.guid,))
            for guid, row in old.items():
                if guid not in active and row['status'] in {'pending','failed'}:
                    self.db.execute("UPDATE entries SET status='withdrawn',reason='absent_from_current_feed' WHERE guid=?",(guid,))
            if baseline and items:
                self.db.execute("UPDATE meta SET value='1' WHERE key='initialized'")
            return baseline

    def claim(self, item, settings, now):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM entries WHERE guid=?',(item.guid,)).fetchone()
            attempts = int(self.value('attempts'))
            if (row is None or row['status'] not in {'pending','failed'} or row['digest'] != item.digest
                    or row['attempts'] >= 1 + settings.retry_limit or row['next_retry'] > now
                    or attempts >= settings.max_send_attempts):
                return False
            self.db.execute("UPDATE entries SET status='inflight',attempts=attempts+1,reason='' WHERE guid=?",(item.guid,))
            self.db.execute("UPDATE meta SET value=? WHERE key='attempts'",(str(attempts+1),))
            return True  # Reservation committed before any external send.

    def finish(self, guid, status, reason='', retry_at=0):
        with self.db:
            self.db.execute('UPDATE entries SET status=?,reason=?,next_retry=? WHERE guid=?',
                            (status,reason,retry_at,guid))

    def close(self):
        if self.db is not None:
            self.db.close(); self.db = None
        if self.lock_file is not None:
            self.lock_file.close(); self.lock_file = None


class Consumer:
    def __init__(self, settings, directory, fetch, send, authorized=lambda: True, clock=time.time):
        Settings.parse(vars(settings))
        if not settings.enabled or not valid_target(settings.target_umo):
            raise ValueError('Explicit enable and exact target required')
        self.settings = settings
        self.fetch = fetch; self.send = send; self.authorized = authorized; self.clock = clock
        self.ledger = Ledger(directory,settings.binding)
        self.poll_lock = asyncio.Lock()
        self.task = None; self.stopping = False; self.closed = False
        self.last_status = 'ready'

    def allowed(self):
        return (not self.stopping and self.settings.enabled and valid_target(self.settings.target_umo)
                and self.authorized() is True)

    async def poll_once(self):
        async with self.poll_lock:
            if not self.allowed():
                self.last_status = 'disabled_or_configuration_changed'; return
            try:
                status, body = await self.fetch(self.settings.feed_url)
                if status != 200:
                    self.last_status = 'http_unavailable'; return
                items = parse_feed(body)
            except (httpx.HTTPError, OSError, ValueError, ET.ParseError, UnicodeError):
                self.last_status = 'fetch_or_parse_error'; return
            if not self.allowed():
                self.last_status = 'disabled_or_configuration_changed'; return
            if self.ledger.observe(items):
                self.last_status = 'history_initialized' if items else 'awaiting_nonempty_history'; return
            self.last_status = 'polled'
            # One submission per poll bounds bursts and does not advance unsent items.
            for item in items:
                if not self.allowed():
                    self.last_status = 'disabled_or_configuration_changed'; return
                if not self.ledger.claim(item,self.settings,self.clock()):
                    continue
                try:
                    if not self.allowed():
                        self.ledger.finish(item.guid,'withheld','authorization_changed'); return
                    started = time.monotonic()
                    deadline = asyncio.timeout(SUBMIT_TIMEOUT_SECONDS)
                    async with deadline:
                        result = await self.send(self.settings.target_umo,item.message)
                except asyncio.CancelledError:
                    self.ledger.finish(item.guid,'uncertain','cancelled_during_submission'); raise
                except Exception:
                    self.ledger.finish(item.guid,'uncertain','submission_outcome_unknown')
                    self.last_status = 'uncertain'; return
                # A lower SDK layer can swallow cancellation and return True.
                # Do not turn an interrupted/late call into a confirmed submission.
                if (deadline.expired() or not self.allowed() or asyncio.current_task().cancelling()
                        or time.monotonic()-started >= SUBMIT_TIMEOUT_SECONDS):
                    self.ledger.finish(item.guid,'uncertain','interrupted_or_late_result')
                    self.last_status = 'uncertain'; return
                if result is True:
                    self.ledger.finish(item.guid,'submitted')
                    self.last_status = 'submitted_to_platform'
                elif result is False:
                    self.ledger.finish(item.guid,'failed','platform_not_found',self.clock()+self.settings.retry_delay_seconds)
                    self.last_status = 'explicit_failure'
                else:
                    self.ledger.finish(item.guid,'uncertain','unexpected_platform_result')
                    self.last_status = 'uncertain'
                return

    def start(self):
        if self.task is not None or self.closed:
            raise ValueError('Already started or closed')

        async def run():
            try:
                while self.allowed():
                    await self.poll_once()
                    if not self.allowed():
                        break
                    await asyncio.sleep(self.settings.poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never rebuild/reset the ledger or repeat sends after state errors.
                self.last_status = 'state_or_worker_error'
            finally:
                self.stopping = True

        self.task = asyncio.create_task(run(),name='cdu-radar-fixed-rss')

    async def close(self):
        if self.closed:
            return
        self.stopping = True
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        # Wait for an explicit test/manual poll as well; do not abandon its sender.
        async with self.poll_lock:
            self.ledger.close()
            self.closed = True
