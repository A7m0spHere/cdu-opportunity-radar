"""Loopback-only RSS endpoint with a background publisher and fail-closed read gate."""
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import logging
import os
from pathlib import Path
import socket
import sqlite3
import threading
import time

from .analysis_models import reference_datetime
from .analysis_store import AnalysisRepository
from .approved_rss import atomic_write, export_approved, render
from .llm_workflow import readonly_repository
from .publication import PublicationRepository, table_exists
from .storage import Store

LOG=logging.getLogger(__name__)
ERRORS=(OSError,ValueError,TypeError,KeyError,sqlite3.Error)


class StateLease:
    """OS-released single-writer lock; no stale PID-file cleanup after a crash."""
    def __init__(self, root, db):
        self.root=Path(root).resolve();db=Path(db).resolve();self.handle=None
        if db.is_relative_to(self.root):raise ValueError('State directory must not contain the source database')
        self.root.mkdir(parents=True,exist_ok=True)
        marker=self.root/'owner.json'
        if not marker.exists() and any(p.name!='publisher.lock' for p in self.root.iterdir()):
            raise ValueError('Refuse to claim a nonempty state directory')
        path=self.root/'publisher.lock'
        if path.is_symlink():raise ValueError('Unsafe state lock')
        self.handle=path.open('a+b')
        try:
            if path.stat().st_size==0:self.handle.write(b'0');self.handle.flush()
            self.handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            identity=hashlib.sha256(os.path.normcase(str(db)).encode()).hexdigest()
            if marker.is_symlink():raise ValueError('Unsafe state marker')
            if marker.exists():
                if json.loads(marker.read_text('utf-8'))!={'kind':'m3a-rss-state-v1','database_identity':identity}:raise ValueError('State directory belongs to another source')
            else:
                if any(p.name!='publisher.lock' for p in self.root.iterdir()):raise ValueError('Refuse to claim a nonempty state directory')
                atomic_write(marker,json.dumps({'kind':'m3a-rss-state-v1','database_identity':identity}).encode())
            if (self.root/'approved.xml').is_symlink():raise ValueError('Unsafe feed cache')
        except BaseException:
            self.close();raise

    def close(self):
        if self.handle:
            self.handle.close();self.handle=None


@dataclass(frozen=True)
class Snapshot:
    body: bytes
    etag: str
    plan_hash: str
    validated_at: float


class FeedController:
    """HTTP reads only; this publisher writes export timestamps, never approvals."""
    def __init__(self, db, state_dir, *, max_age=30, clock=reference_datetime, monotonic=time.monotonic):
        self.db=Path(db).resolve()
        if not self.db.is_file():raise ValueError('Source database does not exist')
        if not 0.1<=max_age<=3600:raise ValueError('Invalid maximum validation age')
        self.lease=StateLease(state_dir,self.db);self.output=self.lease.root/'approved.xml'
        self.max_age=max_age;self.clock=clock;self.monotonic=monotonic
        self.lock=threading.RLock();self.wakeup=threading.Event();self.stop_event=threading.Event()
        self.snapshot=None;self.reason='starting';self.worker=None
        self.closed=False
        self.refreshes=0;self.failures=0

    @contextmanager
    def reader(self):
        if not self.db.is_file():raise ValueError('Source database is unavailable')
        with readonly_repository(self.db) as repo:
            repo.db.execute('PRAGMA busy_timeout=1000')
            repo.db.execute('BEGIN')
            # WAL permits commits while this response holds an older read snapshot.
            # Until there is a shared cross-process protocol, refuse that mode.
            if repo.db.execute('PRAGMA journal_mode').fetchone()[0].lower() not in {'delete','truncate','persist'}:
                raise ValueError('Use local SQLite rollback journaling for the response gate')
            if repo.db.execute('PRAGMA user_version').fetchone()[0]<5 or not table_exists(repo.db,'publication_state'):
                raise ValueError('Initialize publication schema through the existing admin workflow first')
            yield PublicationRepository(repo)

    @contextmanager
    def writer(self):
        # mode=rw refuses to create a replacement DB; no Store/schema migration.
        db=sqlite3.connect(self.db.as_uri()+'?mode=rw',uri=True,timeout=1)
        try:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
            store=Store.__new__(Store);store.db=db
            repo=AnalysisRepository.__new__(AnalysisRepository);repo.db=db;repo.store=store
            yield PublicationRepository(repo)
        finally:db.close()

    def invalidate(self, reason, *, cleanup=True):
        self.snapshot=None;self.reason=reason
        if not cleanup:return
        try:self.output.unlink(missing_ok=True)
        except OSError:pass  # HTTP still returns 503; it never reads this file directly.

    def refresh(self):
        """Scheduled/event-woken publisher; all failure paths remove the ready snapshot."""
        with self.lock:
            if self.stop_event.is_set():return False
            try:
                with self.reader() as repo:plan=repo.feed_plan(self.clock())
                if self.output.is_symlink():raise ValueError('Unsafe feed cache')
                # The service is explicitly authorized to export existing human approvals.
                # No approval/review/label rows are created here and no migration is run.
                with self.writer() as repo:
                    export_approved(repo,self.output,confirm=plan['plan_hash'],now=self.clock())
                with self.reader() as repo:
                    live=repo.feed_plan(self.clock());expected=render(live['items'])
                    if self.output.read_bytes()!=expected:raise ValueError('Source changed during refresh')
                    self.snapshot=Snapshot(expected,'"'+hashlib.sha256(expected).hexdigest()+'"',live['plan_hash'],self.monotonic())
                self.reason='ready';self.refreshes+=1;return True
            except ERRORS as exc:
                self.failures+=1;self.invalidate('database_error' if isinstance(exc,sqlite3.Error) else 'refresh_failed')
                LOG.warning('RSS publication unavailable: %s',self.reason)
                return False

    @contextmanager
    def read_gate(self):
        """Hold the SQLite read snapshot until the response is written.

        A committed revoke/update cannot race an old successful response. Slow
        clients are bounded by the handler's socket timeout. Invalid source/time,
        missing DB, stale validation and failed refresh never get old XML or 304.
        """
        # A stuck refresh must not keep HTTP readers waiting indefinitely.
        if not self.lock.acquire(timeout=0.1):
            self.wakeup.set();yield None;return
        try:
            stack=ExitStack();result=None
            try:
                if self.stop_event.is_set():pass
                elif not self.snapshot or self.monotonic()-self.snapshot.validated_at>self.max_age:
                    self.invalidate('not_ready',cleanup=False);self.wakeup.set()
                else:
                    repo=stack.enter_context(self.reader())
                    plan=repo.feed_plan(self.clock())
                    if plan['plan_hash']!=self.snapshot.plan_hash:
                        self.invalidate('source_changed_or_expired',cleanup=False);self.wakeup.set()
                    else:result=self.snapshot
            except ERRORS:
                self.invalidate('source_unavailable',cleanup=False);self.wakeup.set()
            # Do not catch response/socket errors across yield and yield twice.
            try:yield result
            finally:stack.close()
        finally:self.lock.release()

    def health(self):
        with self.read_gate() as snapshot:
            ready=snapshot is not None
            return {'ready':ready,'status':'ready' if ready else 'unavailable'}

    def start(self, interval=10):
        if not 0.1<=interval<=self.max_age:raise ValueError('Refresh interval must be between 0.1 and max_age')
        if self.worker or self.stop_event.is_set():raise ValueError('Publisher already started or closed')
        self.refresh()
        def work():
            while not self.stop_event.is_set():
                self.wakeup.wait(interval);self.wakeup.clear()
                if not self.stop_event.is_set():self.refresh()
        self.worker=threading.Thread(target=work,name='rss-publisher',daemon=True);self.worker.start()

    def close(self):
        if self.closed:return
        self.stop_event.set();self.wakeup.set()
        if self.worker:self.worker.join(timeout=10)
        if self.worker and self.worker.is_alive():raise RuntimeError('Publisher has not stopped; keep the state lease')
        with self.lock:self.invalidate('stopped')
        self.lease.close()
        self.closed=True


def etag_matches(value, tag):
    return any(t.strip()=='*' or t.strip().removeprefix('W/')==tag for t in (value or '').split(','))


def make_server(controller, host='127.0.0.1', port=8765):
    if not ipaddress.ip_address(host).is_loopback:raise ValueError('Only an explicit loopback address is allowed')
    if not 0<=port<=65535:raise ValueError('Invalid port')
    class Handler(BaseHTTPRequestHandler):
        server_version='CDUOpportunityRSS';sys_version=''
        def setup(self):
            super().setup();self.connection.settimeout(5)
        def log_message(self,*args):pass  # Never log query strings, headers or request bodies.
        def reply(self, status, body, content_type='text/plain; charset=utf-8', etag=None):
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Cache-Control','no-store, max-age=0');self.send_header('X-Content-Type-Options','nosniff')
            if etag:self.send_header('ETag',etag)
            if status==503:self.send_header('Retry-After','1')
            if status==405:self.send_header('Allow','GET, HEAD')
            self.send_header('Content-Length',str(len(body)));self.end_headers()
            if self.command!='HEAD' and status!=304:self.wfile.write(body);self.wfile.flush()
        def do_GET(self):
            if self.path not in {'/approved.xml','/health'}:
                self.reply(404,b'Not found\n');return
            try:
                if self.path=='/health':
                    state=controller.health();self.reply(200 if state['ready'] else 503,json.dumps(state).encode(),'application/json');return
                with controller.read_gate() as snapshot:
                    if snapshot is None:self.reply(503,b'RSS temporarily unavailable\n')
                    elif etag_matches(self.headers.get('If-None-Match'),snapshot.etag):self.reply(304,b'',etag=snapshot.etag)
                    else:self.reply(200,snapshot.body,'application/rss+xml; charset=utf-8',snapshot.etag)
            except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
        do_HEAD=do_GET
        def forbidden(self):self.reply(405,b'Read-only endpoint\n')
        do_POST=do_PUT=do_PATCH=do_DELETE=do_OPTIONS=forbidden
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        address_family=socket.AF_INET6 if ipaddress.ip_address(host).version==6 else socket.AF_INET
    return Server((host,port),Handler)


def serve_command(args):
    controller=None;server=None
    try:
        # Validate the binding before claiming a state directory or publishing anything.
        if not ipaddress.ip_address(args.bind).is_loopback:raise ValueError('Public/LAN binding is disabled in M3-A')
        controller=FeedController(args.db,args.state_dir,max_age=args.max_age)
        server=make_server(controller,args.bind,args.port);controller.start(args.interval)
        print(json.dumps({'bind':args.bind,'port':server.server_address[1],'rss_path':'/approved.xml','local_only':True}),flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:return 0
    except ERRORS as exc:
        # Fixed messages only: SQLite exceptions may include private filesystem details.
        LOG.error('RSS service could not start (%s)',type(exc).__name__);return 2
    finally:
        if server:server.server_close()
        if controller:controller.close()
