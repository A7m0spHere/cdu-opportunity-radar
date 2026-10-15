"""Automatic RSS on the M3-A lease, rollback-lock response gate and 503 policy."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
from xml.etree import ElementTree as ET

from .auto_publish import AutoRepository, NS, render
from .approved_rss import atomic_write
from .rss_service import FeedController, Snapshot, ERRORS, LOG, make_server


class AutoFeedController(FeedController):
    paths=('/feed.xml','/current.xml','/history.xml')
    retired_paths=('/approved.xml',)
    state_kind='m4b-auto-rss-state-v1'

    def __init__(self, db, state_dir, **kwargs):
        super().__init__(db,state_dir,**kwargs)
        self.output=self.lease.root/'feed.xml'
        if self.output.is_symlink():
            self.lease.close()
            raise ValueError('Unsafe automatic feed cache')

    @contextmanager
    def reader(self):
        if not self.db.is_file():raise ValueError('Source database unavailable')
        db=sqlite3.connect(self.db.as_uri()+'?mode=ro',uri=True,timeout=1)
        try:
            db.row_factory=sqlite3.Row;db.execute('BEGIN')
            if db.execute('PRAGMA journal_mode').fetchone()[0].lower() not in {'delete','truncate','persist'}:
                raise ValueError('Rollback journaling required for response gate')
            yield AutoRepository(db)
        finally:db.close()

    @contextmanager
    def writer(self):
        db=sqlite3.connect(self.db.as_uri()+'?mode=rw',uri=True,timeout=1)
        try:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
            yield AutoRepository(db)
        finally:db.close()

    def refresh(self):
        with self.lock:
            if self.stop_event.is_set():return False
            try:
                with self.reader() as repo:repo.feed_plan(self.clock())
                if self.output.is_symlink():raise ValueError('Unsafe feed cache')
                with self.writer() as repo:
                    # Reserve first publication times durably before file I/O;
                    # retries after disk failures cannot change identity/time.
                    repo.db.execute('BEGIN IMMEDIATE')
                    repo.reserve(self.clock());repo.db.commit()
                    repo.db.execute('BEGIN IMMEDIATE')
                    plan=repo.feed_plan(self.clock())
                    atomic_write(self.output,render(plan['items']))
                    repo.db.commit()
                with self.reader() as repo:
                    live=repo.feed_plan(self.clock());body=render(live['items'])
                    if self.output.read_bytes()!=body:raise ValueError('Source changed during refresh')
                    self.snapshot=Snapshot(body,'"'+hashlib.sha256(body).hexdigest()+'"',live['plan_hash'],self.monotonic())
                self.reason='ready';self.refreshes+=1;return True
            except ERRORS as exc:
                self.failures+=1
                self.invalidate('database_error' if isinstance(exc,sqlite3.Error) else 'refresh_failed')
                LOG.warning('Automatic RSS unavailable: %s',self.reason)
                return False

    def response_snapshot(self, snapshot, path):
        if path=='/feed.xml':return snapshot
        scope='current' if path=='/current.xml' else 'history'
        root=ET.fromstring(snapshot.body);channel=root.find('channel')
        for item in channel.findall('item'):
            if item.findtext('{'+NS+'}listing')!=scope:channel.remove(item)
        body=ET.tostring(root,encoding='utf-8',xml_declaration=True)
        return Snapshot(body,'"'+hashlib.sha256(body).hexdigest()+'"',snapshot.plan_hash,snapshot.validated_at)


def serve_command(args):
    import ipaddress
    import math
    controller=None;server=None;collector=None
    from threading import Event, Thread
    stop=Event()
    try:
        if not ipaddress.ip_address(args.bind).is_loopback:raise ValueError('Only loopback binding is enabled')
        if not math.isfinite(args.collect_interval) or args.collect_interval and args.collect_interval<60:
            raise ValueError('Collection interval must be zero or at least 60 seconds')
        if not 0.1<=args.interval<=args.max_age:raise ValueError('Invalid publisher refresh interval')
        controller=AutoFeedController(args.db,args.state_dir,max_age=args.max_age)
        server=make_server(controller,args.bind,args.port)
        controller.start(args.interval)
        if args.collect_interval:
            from .collect import collect
            from .http import Fetcher
            from .sources import load_sources
            from .storage import Store
            catalog=load_sources()
            sources=[s for s in catalog if s.status=='verified' and s.parser_supported and s.adapter and (not args.source or s.id in args.source)]
            if args.source and set(args.source)-{s.id for s in sources}:raise ValueError('Only verified implemented sources are allowed')
            def work():
                while not stop.is_set():
                    try:
                        # mode=rw refuses to recreate a deleted live source DB.
                        with controller.writer() as repo:
                            store=Store.__new__(Store);store.db=repo.db
                            store.sync_sources(catalog)
                            with Fetcher(timeout=12,retries=0,interval=2.5,sleep=stop.wait,
                                    trust_env=not args.no_env_proxy,event_sink=store.request_event) as fetcher:
                                collect(store,fetcher,sources,limit=args.limit,max_pages=1,stop_event=stop)
                    except ERRORS:
                        LOG.warning('Automatic collection failed; source health gate remains active')
                    controller.wakeup.set()
                    if stop.wait(args.collect_interval):break
            collector=Thread(target=work,name='official-collector',daemon=True);collector.start()
        print(json.dumps({'rss_path':'/feed.xml','current_path':'/current.xml','history_path':'/history.xml',
            'port':server.server_address[1],'local_only':True,'paid_llm_calls':0}),flush=True)
        server.serve_forever(poll_interval=0.2)
        return 0
    except KeyboardInterrupt:return 0
    except ERRORS as exc:
        LOG.error('Automatic RSS could not start (%s)',type(exc).__name__);return 2
    finally:
        stop.set()
        if server:server.server_close()
        if collector:collector.join(timeout=30)
        if controller:controller.close()
