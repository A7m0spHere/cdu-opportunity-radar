"""Automatic RSS on the M3-A lease, rollback-lock response gate and 503 policy."""
from contextlib import contextmanager, ExitStack
from collections import Counter
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from xml.etree import ElementTree as ET

from .auto_publish import AutoRepository, NS, render
from .approved_rss import atomic_write
from .rss_service import FeedController, Snapshot, ERRORS, LOG, make_server
from .student_page import assets, public_snapshot, public_snapshot_v2
from .taxonomy import API_V2, filter_payload
from .featured_api import API_V3, project, split_query
from .featured import FeaturedPolicy, decisions, ValidationCache
from . import rss_v1, subscription_store
from . import review_api
from .models import InvalidFilter


class AutoFeedController(FeedController):
    paths=('/feed.xml','/current.xml','/history.xml','/api/notices',API_V2,API_V3,review_api.API_V4,*rss_v1.ROUTES,*review_api.RSS_ROUTES)
    retired_paths=('/approved.xml',)
    state_kind='m4b-auto-rss-state-v1'
    public_contract='student-notices-v1'

    def __init__(self, db, state_dir, *, data_mode='live', analysis_state=None, analysis_analyzer=None, featured_policy=None, **kwargs):
        if data_mode not in {'live','offline_replay'}:raise ValueError('Invalid data mode')
        if analysis_analyzer and analysis_analyzer.mock and data_mode!='offline_replay':
            raise ValueError('Mock analysis requires explicit offline replay')
        self.data_mode=data_mode
        self.analysis_state=analysis_state
        self.analysis_analyzer=analysis_analyzer
        self.featured_policy=featured_policy or FeaturedPolicy()
        self.static_assets=assets()
        super().__init__(db,state_dir,**kwargs)
        self._refresh_lock=threading.Lock()
        self._metrics_lock=threading.Lock()
        self._reads=threading.local()
        self.gate_failures=Counter()
        self.validation_cache=ValidationCache()
        self.subscription_path=self.lease.root/'subscriptions.sqlite3'
        self.subscription_ready=False
        self.subscription_budget=rss_v1.RequestBudget(self.monotonic)
        self.output=self.lease.root/'feed.xml'
        if self.output.is_symlink():
            self.lease.close()
            raise ValueError('Unsafe automatic feed cache')

    @contextmanager
    def reader(self):
        if not self.db.is_file():raise ValueError('Source database unavailable')
        current=getattr(self._reads,'raw',None)
        if current is not None:
            yield current;return
        db=sqlite3.connect(self.db.as_uri()+'?mode=ro',uri=True,timeout=5)
        try:
            db.row_factory=sqlite3.Row;db.execute('BEGIN')
            if db.execute('PRAGMA journal_mode').fetchone()[0].lower() not in {'delete','truncate','persist'}:
                raise ValueError('Rollback journaling required for response gate')
            repo=AutoRepository(db);self._reads.raw=repo
            yield repo
        finally:
            self._reads.raw=None;db.close()

    @contextmanager
    def analysis_reader(self):
        # The v2 featured route nests reviewed_gate and selection_gate. A second
        # connection would deadlock behind a writer waiting for the first read.
        from .async_queue import JobQueue
        current=getattr(self._reads,'analysis',None)
        if current is not None:
            yield current;return
        with JobQueue(self.analysis_state,self.db,read_only=True,read_timeout=.25) as queue:
            queue.db.execute('BEGIN')
            self._reads.analysis=queue
            try:yield queue
            finally:
                self._reads.analysis=None;queue.db.rollback()

    @contextmanager
    def writer(self):
        db=sqlite3.connect(self.db.as_uri()+'?mode=rw',uri=True,timeout=10)
        try:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
            yield AutoRepository(db)
        finally:db.close()

    def refresh(self):
        with self._refresh_lock:
            if self.stop_event.is_set():return False
            try:
                # Check existence and rollback journaling before any writes.
                # reserve() revalidates all source data under the write lock.
                with self.reader() as repo:
                    needs_reservation=repo.db.execute('SELECT 1 FROM notices n LEFT JOIN auto_publications a ON a.notice_id=n.id WHERE a.notice_id IS NULL LIMIT 1').fetchone() is not None
                if self.output.is_symlink():raise ValueError('Unsafe feed cache')
                if needs_reservation:
                    with self.writer() as repo:
                        # Reserve before file I/O, only when new originals need it.
                        repo.db.execute('BEGIN IMMEDIATE');repo.reserve(self.clock());repo.db.commit()
                with self.reader() as repo:
                    plan=repo.feed_plan(self.clock())
                    atomic_write(self.output,render(plan['items']))
                with self.reader() as repo:
                    live=repo.feed_plan(self.clock());body=render(live['items'])
                    if self.output.read_bytes()!=body:raise ValueError('Source changed during refresh')
                    data=public_snapshot(live['items'],refreshed_at=self.clock().isoformat(),data_mode=self.data_mode)
                    data_v2=public_snapshot_v2(live['items'],refreshed_at=data['refreshed_at'],data_mode=self.data_mode,filter_options=live['filter_options'])
                    candidate=Snapshot(body,'"'+hashlib.sha256(body).hexdigest()+'"',live['plan_hash'],self.monotonic(),data,data_v2)
                # Optional analysis/storage faults must not take down all-original RSS.
                subscription_ready=False
                if self.analysis_state is not None and self.analysis_analyzer is not None:
                    try:
                        with self.selection_gate(candidate) as (token,selected):
                            with subscription_store.connection(self.subscription_path,self.db,write=True) as state:
                                subscription_store.persist(state,token,selected,self.clock())
                        subscription_ready=True
                    except ERRORS:
                        LOG.warning('Featured subscription unavailable')
                with self.lock:
                    self.snapshot=candidate;self.subscription_ready=subscription_ready
                    self.reason='ready';self.refreshes+=1
                self.report_private_health()
                return True
            except ERRORS as exc:
                detail=getattr(exc,'sqlite_errorname',type(exc).__name__)
                with self.lock:
                    self.failures+=1
                    # A busy writer cannot make a validated snapshot untrue.
                    # read_gate still checks its age and live source plan.
                    busy=isinstance(exc,sqlite3.OperationalError) and (getattr(exc,'sqlite_errorcode',0)&255) in {sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED}
                    if not busy or self.snapshot is None or self.monotonic()-self.snapshot.validated_at>self.max_age:
                        self.invalidate('database_error' if isinstance(exc,sqlite3.Error) else 'refresh_failed')
                self.gate_failure('refresh_'+detail)
                LOG.warning('Automatic RSS refresh failed: %s',detail)
                self.report_private_health()
                return False

    def gate_failure(self,reason):
        with self._metrics_lock:self.gate_failures[reason]+=1
        LOG.info('Automatic RSS gate: %s',reason)

    @contextmanager
    def read_gate(self):
        # Only the immutable snapshot pointer uses the process lock. Each
        # response still holds its own rollback-journal read transaction until
        # the last byte, preserving revoke/update ordering across processes.
        if not self.lock.acquire(timeout=.1):
            self.gate_failure('snapshot_lock_busy');yield None;return
        try:snapshot=self.snapshot
        finally:self.lock.release()
        reason=None
        if self.stop_event.is_set():reason='stopped'
        elif snapshot is None or self.monotonic()-snapshot.validated_at>self.max_age:reason='validation_expired'
        if reason:
            with self.lock:
                if self.snapshot is snapshot:self.invalidate(reason,cleanup=False)
            self.gate_failure(reason);self.wakeup.set();yield None;return
        stack=ExitStack()
        try:
            try:
                repo=stack.enter_context(self.reader())
                if repo.feed_plan(self.clock())['plan_hash']!=snapshot.plan_hash:reason='source_changed_or_expired'
            except ERRORS:reason='source_unavailable'
            if reason:
                with self.lock:
                    if self.snapshot is snapshot:self.invalidate(reason,cleanup=False)
                self.gate_failure(reason);self.wakeup.set()
            # Caller exceptions are outside the validation exception handler.
            yield None if reason else snapshot
        finally:stack.close()

    def private_health(self):
        with self._metrics_lock:counts=dict(self.gate_failures)
        return dict(ready=self.snapshot is not None,reason=self.reason,refreshes=self.refreshes,
                    failures=self.failures,gate_failures=counts)

    def report_private_health(self):
        try:atomic_write(self.lease.root/'publisher-health.json',json.dumps(self.private_health()).encode())
        except OSError:LOG.warning('Publisher health report unavailable')

    def route_path(self,path):
        if path.partition('?')[0] in {review_api.API_V4,*review_api.RSS_ROUTES}:
            return path.partition('?')[0]
        if rss_v1.route_path(path) in rss_v1.ROUTES:
            return rss_v1.route_path(path)
        for route in (API_V2,API_V3):
            if path.startswith(route+'?'):return route
        return path

    def allow_request(self,path,peer):
        return self.route_path(path) not in {*rss_v1.ROUTES,*review_api.RSS_ROUTES} or self.subscription_budget.allow()

    @contextmanager
    def reviewed_gate(self, snapshot, path):
        from contextlib import ExitStack
        from .analysis_store import AnalysisRepository
        from .storage import Store
        from .featured import analysis_reader, analysis_for
        review_api.split_query(path,snapshot.data_v2['filter_options'],snapshot.data_v2['categories'])
        with ExitStack() as stack:
            raw=stack.enter_context(self.reader())
            if raw.feed_plan(self.clock())['plan_hash']!=snapshot.plan_hash:raise ValueError('Source changed during classification')
            rows={r['canonical_url']:dict(r) for r in raw.db.execute('SELECT * FROM notices')}
            store=Store.__new__(Store);store.db=raw.db
            repo=AnalysisRepository.__new__(AnalysisRepository);repo.store=store;repo.db=raw.db
            analyses={};selected={};queue=None;fault=False
            if self.analysis_state is not None and self.analysis_analyzer is not None:
                if self.analysis_analyzer.mock and self.data_mode!='offline_replay':raise ValueError('Mock analysis requires offline replay')
                try:queue=stack.enter_context(self.analysis_reader())
                except ERRORS:fault=True
            try:
                for item in snapshot.data_v2['items']:
                    if queue is None:notice,analysis,state=None,None,'unavailable' if fault else 'not_analyzed'
                    else:notice,analysis,state=analysis_for(queue,repo,self.analysis_analyzer,rows[item['source_url']]['id'],self.clock(),validation_cache=self.validation_cache)
                    analyses[item['guid']]=(analysis,state)
                    selected[item['guid']]=self.featured_policy.decide(notice,analysis,state)
            except ERRORS:
                analyses={i['guid']:(None,'unavailable') for i in snapshot.data_v2['items']}
                selected={i['guid']:self.featured_policy.decide(None,None,'unavailable') for i in snapshot.data_v2['items']}
            yield review_api.project(snapshot.data_v2,path,rows,analyses,selected,self.featured_policy)

    @contextmanager
    def selection_gate(self,snapshot):
        """Hold raw and private queue rollback snapshots through persistence/response."""
        from .analysis_models import fingerprint
        from .analysis_store import AnalysisRepository
        from .storage import Store
        from .featured import analysis_reader, analysis_for
        if self.analysis_state is None or self.analysis_analyzer is None:
            raise ValueError('Featured analysis not configured')
        if self.analysis_analyzer.mock and self.data_mode!='offline_replay':
            raise ValueError('Mock analysis requires offline replay')
        with self.reader() as raw:
            plan=raw.feed_plan(self.clock())
            if plan['plan_hash']!=snapshot.plan_hash:
                raise ValueError('Source changed during selection')
            store=Store.__new__(Store);store.db=raw.db
            repo=AnalysisRepository.__new__(AnalysisRepository);repo.store=store;repo.db=raw.db
            with self.analysis_reader() as queue:
                if queue.db.execute('PRAGMA journal_mode').fetchone()[0].lower() not in {'delete','truncate','persist'}:
                    raise ValueError('Analysis rollback journaling required for subscriptions')
                selected={};signatures=[]
                for item in plan['items']:
                    ident=item['notice_id']
                    notice,analysis,state=analysis_for(queue,repo,self.analysis_analyzer,ident,self.clock(),validation_cache=self.validation_cache)
                    selected[item['guid']]=self.featured_policy.decide(notice,analysis,state)
                    # Reanalysis and pipeline/lease changes invalidate persisted collections,
                    # even when their public decision happens to be unchanged.
                    signatures.extend(tuple(r) for r in queue.db.execute(
                        'SELECT id,input_hash,pipeline_version,state,mode,result,lease_until,updated_at FROM jobs WHERE notice_id=? ORDER BY id',(ident,)))
                token=fingerprint([snapshot.plan_hash,self.data_mode,self.analysis_analyzer.version,
                    self.featured_policy.version,signatures,selected])
                yield token,selected

    @contextmanager
    def response_gate(self,snapshot,path):
        try:
            with self._response_gate(snapshot,path) as response:yield response
        except (ConnectionError,TimeoutError,InvalidFilter):raise
        except ERRORS:
            self.gate_failure('response_validation_or_storage');raise

    @contextmanager
    def _response_gate(self,snapshot,path):
        route=self.route_path(path)
        if route in {review_api.API_V4,*review_api.RSS_ROUTES}:
            with self.reviewed_gate(snapshot,path) as payload:
                if route==review_api.API_V4:
                    remaining=max(0,self.max_age-(self.monotonic()-snapshot.validated_at))
                    body=json.dumps(dict(payload,valid_for_ms=int(remaining*1000),poll_after_ms=5000),ensure_ascii=False,separators=(',',':')).encode('utf-8')
                    yield Snapshot(body,'',snapshot.plan_hash,snapshot.validated_at)
                elif route==review_api.RSS_ROUTES[0]:
                    with self.reader() as repo:
                        revisions={r['canonical_url']:r['content_hash'] for r in repo.db.execute('SELECT canonical_url,content_hash FROM notices')}
                    body=review_api.render_rss(payload,path,source_revisions=revisions)
                    yield Snapshot(body,'"'+hashlib.sha256(body).hexdigest()+'"',snapshot.plan_hash,snapshot.validated_at)
                else:
                    if not self.subscription_ready:raise ValueError('Featured subscription not initialized')
                    with self.selection_gate(snapshot) as (token,selected):
                        with subscription_store.connection(self.subscription_path,self.db) as state:
                            first=subscription_store.read(state,token,selected,self.clock())
                            with self.reader() as repo:
                                revisions={r['canonical_url']:r['content_hash'] for r in repo.db.execute('SELECT canonical_url,content_hash FROM notices')}
                            body=review_api.render_rss(payload,path,first,source_revisions=revisions)
                            yield Snapshot(body,'"'+hashlib.sha256(body).hexdigest()+'"',snapshot.plan_hash,snapshot.validated_at)
            return
        if route not in rss_v1.ROUTES:
            yield self.response_snapshot(snapshot,path)
            return
        # Validate all parameters before opening the optional analysis sidecar.
        rss_v1.filtered(snapshot.data_v2,path)
        if route==rss_v1.ROUTES[0]:
            body=rss_v1.render(snapshot.data_v2,path)
            yield Snapshot(body,'"'+hashlib.sha256(body).hexdigest()+'"',snapshot.plan_hash,snapshot.validated_at)
            return
        try:
            if not self.subscription_ready:
                raise ValueError('Featured subscription not initialized')
            with self.selection_gate(snapshot) as (token,selected):
                with subscription_store.connection(self.subscription_path,self.db) as state:
                    first=subscription_store.read(state,token,selected,self.clock())
                    body=rss_v1.render(snapshot.data_v2,path,collection=selected,first_featured=first)
                    yield Snapshot(body,'"'+hashlib.sha256(body).hexdigest()+'"',snapshot.plan_hash,snapshot.validated_at)
        except (ConnectionError,TimeoutError):raise
        except ERRORS:
            self.wakeup.set()
            raise

    def featured_payload(self,snapshot,path):
        # Parse before reading private state, then project only quality-gated IDs.
        v2_path,_=split_query(path)
        cohort=filter_payload(snapshot.data_v2,v2_path)['items']
        from .analysis_store import AnalysisRepository
        from .storage import Store
        with self.reader() as raw:
            ids={r['canonical_url']:r['id'] for r in raw.db.execute('SELECT id,canonical_url FROM notices')}
            inputs=[{'notice_id':ids[i['source_url']],'guid':i['guid']} for i in cohort]
            store=Store.__new__(Store);store.db=raw.db
            repo=AnalysisRepository.__new__(AnalysisRepository);repo.store=store;repo.db=raw.db
            decided=decisions(repo,inputs,state_db=self.analysis_state,raw_db=self.db,
                analyzer=self.analysis_analyzer,policy=self.featured_policy,now=self.clock())
        by_guid={i['guid']:decided[i['notice_id']] for i in inputs}
        return project(snapshot.data_v2,path,by_guid,self.featured_policy)

    def response_snapshot(self, snapshot, path):
        if self.route_path(path)==API_V3:
            payload=self.featured_payload(snapshot,path)
            remaining=max(0,self.max_age-(self.monotonic()-snapshot.validated_at))
            body=json.dumps(dict(payload,valid_for_ms=int(remaining*1000),poll_after_ms=5000),ensure_ascii=False,separators=(',',':')).encode('utf-8')
            return Snapshot(body,'',snapshot.plan_hash,snapshot.validated_at)
        if path=='/api/notices' or self.route_path(path)==API_V2:
            remaining=max(0,self.max_age-(self.monotonic()-snapshot.validated_at))
            data=filter_payload(snapshot.data_v2,path) if self.route_path(path)==API_V2 else snapshot.data
            payload=dict(data,valid_for_ms=int(remaining*1000),poll_after_ms=5000)
            body=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf-8')
            # JSON carries a per-response lifetime, so it never returns 304.
            return Snapshot(body,'',snapshot.plan_hash,snapshot.validated_at)
        if path=='/feed.xml':return snapshot
        scope='current' if path=='/current.xml' else 'history'
        root=ET.fromstring(snapshot.body);channel=root.find('channel')
        for item in channel.findall('item'):
            if item.findtext('{'+NS+'}listing')!=scope:channel.remove(item)
        body=ET.tostring(root,encoding='utf-8',xml_declaration=True)
        return Snapshot(body,'"'+hashlib.sha256(body).hexdigest()+'"',snapshot.plan_hash,snapshot.validated_at)

    def response_content_type(self, path):
        return 'application/json; charset=utf-8' if path in {'/api/notices',API_V2,API_V3,review_api.API_V4} else super().response_content_type(path)


def serve_command(args):
    import ipaddress
    import math
    controller=None;server=None;collector=None;analysis_worker=None
    from threading import Event, Thread
    stop=Event()
    try:
        if not ipaddress.ip_address(args.bind).is_loopback:raise ValueError('Only loopback binding is enabled')
        if not math.isfinite(args.collect_interval) or args.collect_interval and args.collect_interval<60:
            raise ValueError('Collection interval must be zero or at least 60 seconds')
        if not 0.1<=args.interval<=args.max_age:raise ValueError('Invalid publisher refresh interval')
        analysis_analyzer=None
        analysis_state=getattr(args,'analysis_state',None);analysis_config=getattr(args,'analysis_config',None)
        if bool(analysis_state)!=bool(analysis_config):raise ValueError('Supply analysis state and config together')
        if analysis_config:
            from .async_cli import configuration
            analysis_analyzer,_=configuration(analysis_config,False)
        if getattr(args,'analysis_worker',False) and not analysis_config:
            raise ValueError('Managed analysis worker requires state and config')
        policy=FeaturedPolicy(model_threshold=getattr(args,'featured_model_threshold','normal'),rule_threshold=getattr(args,'featured_rule_threshold',3))
        controller=AutoFeedController(args.db,args.state_dir,max_age=args.max_age,analysis_state=analysis_state,
            analysis_analyzer=analysis_analyzer,featured_policy=policy)
        server=make_server(controller,args.bind,args.port)
        controller.start(args.interval)
        if getattr(args,'analysis_worker',False):
            from .async_queue import JobQueue
            from .async_worker import run_round,isolated_run
            from .analysis_models import reference_datetime
            _,queue_policy=configuration(analysis_config,False)
            def review_work():
                from .managed_worker import supervise
                def operation():
                    with JobQueue(analysis_state,args.db,policy=queue_policy) as queue:
                        result=run_round(queue,analysis_analyzer,reference_time=reference_datetime(),
                            notice_ids=getattr(args,'analysis_notice_id',None),
                            runner=lambda a,n,t,e:isolated_run(a,n,t,e,stop_event=stop))
                    controller.wakeup.set();return result
                supervise(operation,stop,lambda health:atomic_write(controller.lease.root/'analysis-worker.json',json.dumps(health).encode()))
            analysis_worker=Thread(target=review_work,name='classification-worker',daemon=True);analysis_worker.start()
        if args.collect_interval:
            from .recheck import collect_cycle, initialize as initialize_rechecks
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
                            initialize_rechecks(store.db)
                            store.sync_sources(catalog)
                            with Fetcher(timeout=12,retries=0,interval=2.5,sleep=stop.wait,
                                    trust_env=not args.no_env_proxy,event_sink=store.request_event,stop_event=stop) as fetcher:
                                collect_cycle(store,fetcher,sources,limit=args.limit,
                                    recheck_limit=getattr(args,'recheck_limit',3),
                                    request_budget=getattr(args,'request_budget',48),stop_event=stop,
                                    cycle_interval=args.collect_interval)
                    except ERRORS as exc:
                        LOG.warning('Automatic collection failed: %s',getattr(exc,'sqlite_errorname',type(exc).__name__))
                    controller.wakeup.set()
                    if stop.wait(args.collect_interval):break
            collector=Thread(target=work,name='official-collector',daemon=True);collector.start()
        print(json.dumps({'rss_path':'/feed.xml','current_path':'/current.xml','history_path':'/history.xml',
            'rss_v1_all_path':rss_v1.ROUTES[0],'rss_v1_featured_path':rss_v1.ROUTES[1],
            'page_path':'/','api_path':'/api/notices','api_v2_path':API_V2,'api_v3_path':API_V3,'port':server.server_address[1],'local_only':True,'paid_llm_calls':0}),flush=True)
        server.serve_forever(poll_interval=0.2)
        return 0
    except KeyboardInterrupt:return 0
    except ERRORS as exc:
        LOG.error('Automatic RSS could not start (%s)',type(exc).__name__);return 2
    finally:
        stop.set()
        if server:server.server_close()
        if collector:collector.join(timeout=30)
        if analysis_worker is not None:analysis_worker.join(timeout=5)
        if controller:controller.close()
