"""Private sidecar job store. Raw SQLite is always opened mode=ro.

Short IMMEDIATE transactions serialize claims and budget reservations. Model work
runs outside transactions; expiring lease tokens fence late workers.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid

from .analysis_models import fingerprint
from .llm import LLMError, usage_values
from .llm_workflow import readonly_repository

SCHEMA = '''
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 id INTEGER PRIMARY KEY, notice_id INTEGER NOT NULL, input_hash TEXT NOT NULL,
 content_hash TEXT NOT NULL, pipeline_version TEXT NOT NULL, model TEXT NOT NULL,
 prompt_version TEXT NOT NULL, schema_version TEXT NOT NULL, rule_version TEXT NOT NULL,
 mode TEXT NOT NULL, state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
 next_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT,
 lease_until REAL, error TEXT, result TEXT,
 UNIQUE(notice_id,input_hash,pipeline_version));
CREATE INDEX IF NOT EXISTS jobs_due ON jobs(pipeline_version,state,next_at,id);
CREATE TABLE IF NOT EXISTS attempts (
 id TEXT PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id), ordinal INTEGER NOT NULL,
 reserved_at REAL NOT NULL, reserved_tokens INTEGER NOT NULL, billable INTEGER NOT NULL,
 dispatch_started INTEGER NOT NULL DEFAULT 0,status_code INTEGER,
 finished_at REAL, state TEXT NOT NULL, error TEXT, prompt_tokens INTEGER,
 completion_tokens INTEGER,total_tokens INTEGER,estimated_cost REAL,currency TEXT,
 UNIQUE(job_id,ordinal));
CREATE TABLE IF NOT EXISTS input_failures (
 notice_id INTEGER NOT NULL,pipeline_version TEXT NOT NULL,error TEXT NOT NULL,
 observed_at REAL NOT NULL,resolved_at REAL,
 PRIMARY KEY(notice_id,pipeline_version));
'''
RETRYABLE = {'timeout','connection','rate_limited','server_error','worker_lost'}
ERRORS = RETRYABLE | {'authentication','forbidden','http_error','response_too_large',
    'invalid_api_json','invalid_api_response','missing_choices','missing_message',
    'model_refusal','unexpected_tool_call','incomplete_output','no_final_text',
    'invalid_json','schema_validation','missing_api_key','empty_body','input_too_long',
    'internal_error','untrusted_instruction','prescreen_type','prescreen_topic','source_changed',
    'source_unavailable','usage_exceeded_reservation','budget_exhausted'}
TERMINAL = {'succeeded','partial','failed','skipped','stale'}


@dataclass(frozen=True)
class QueuePolicy:
    batch_limit: int = 3
    scan_limit: int = 100
    max_attempts: int = 3
    backoff_seconds: float = 30
    lease_seconds: float = 180
    request_limit: int = 20
    token_limit: int = 200000
    window_seconds: float = 86400

    def __post_init__(self):
        for name, ceiling in [('batch_limit',100),('scan_limit',1000),('max_attempts',5),
                              ('request_limit',100000),('token_limit',1000000000)]:
            v=getattr(self,name)
            if type(v) is not int or not 1<=v<=ceiling:raise ValueError('Invalid queue limit')
        for name, lower, upper in [('backoff_seconds',1,86400),('lease_seconds',5,3600),('window_seconds',60,2678400)]:
            v=getattr(self,name)
            if type(v) not in {int,float} or not math.isfinite(v) or not lower<=v<=upper:raise ValueError('Invalid queue duration')


@contextmanager
def source_snapshot(path):
    with readonly_repository(Path(path)) as repo:
        repo.db.execute('BEGIN')
        yield repo


class JobQueue:
    def __init__(self, path, raw_path, *, policy=None, clock=time.time, read_only=False):
        self.path, self.raw_path = Path(path).absolute(), Path(raw_path).resolve(strict=True)
        if not self.raw_path.is_file():raise ValueError('Raw database does not exist')
        if self.path.is_symlink() or self.path.parent.is_symlink():raise ValueError('State database symlinks are refused')
        self.path=self.path.resolve()
        raw_names={str(self.raw_path).casefold()+s for s in ['', '-wal','-shm','-journal']}
        state_names={str(self.path).casefold()+s for s in ['', '-wal','-shm','-journal']}
        if raw_names & state_names or self.path.exists() and self.path.samefile(self.raw_path):
            raise ValueError('State database must be independent of raw database and sidecars')
        self.policy, self.clock = policy or QueuePolicy(), clock
        if not read_only:self.path.parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.path.as_uri()+'?mode=ro' if read_only else self.path,
                               uri=read_only,timeout=0 if read_only else 10,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        # Refuse accidental use of an existing application database before any DDL.
        try:
            tables={r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if tables and not tables<={'metadata','jobs','attempts','input_failures'}:
                raise ValueError('State database is not an M7 queue')
            if not read_only:self.db.executescript(SCHEMA)
            with self.transaction(read_only=read_only):
                for key,value in {'schema':'m7-queue-v1','raw_path':str(self.raw_path),
                                  'raw_identity':self._raw_identity()}.items():
                    row=self.db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
                    if row and row[0]!=value or read_only and not row:raise ValueError('Queue source identity/schema changed; use a separate queue')
                    if not read_only:self.db.execute('INSERT OR IGNORE INTO metadata VALUES (?,?)',(key,value))
        except BaseException:
            self.db.close();raise

    def _raw_identity(self):
        # Detect replacement at the same path; changes to records keep this identity.
        st=self.raw_path.stat()
        return fingerprint([str(self.raw_path),st.st_dev,st.st_ino])

    def ensure_source(self):
        known=self.db.execute("SELECT value FROM metadata WHERE key='raw_identity'").fetchone()[0]
        if self._raw_identity()!=known:raise ValueError('Raw database was replaced')

    def close(self):self.db.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()

    @contextmanager
    def transaction(self, read_only=False):
        self.db.execute('BEGIN' if read_only else 'BEGIN IMMEDIATE')
        try:yield
        except BaseException:
            self.db.rollback();raise
        else:self.db.commit()

    def scan(self, analyzer):
        self.ensure_source()
        # A cursor per pipeline means a new prompt/config starts its own bounded sweep.
        key='cursor:'+analyzer.version
        cursor=self.db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
        cursor=int(cursor[0]) if cursor else 0
        with source_snapshot(self.raw_path) as repo:
            ids=[r[0] for r in repo.db.execute('SELECT id FROM notices WHERE id>? ORDER BY id LIMIT ?',
                                             (cursor,self.policy.scan_limit))]
            notices=[];invalid=[]
            for ident in ids:
                try:notices.append(repo.input(ident))
                except (ValueError,TypeError):invalid.append(ident)
        now=self.clock();inserted=0
        with self.transaction():
            for ident in invalid:
                self.db.execute('''INSERT INTO input_failures VALUES (?,?,'invalid_input',?,NULL)
                    ON CONFLICT(notice_id,pipeline_version) DO UPDATE SET
                    error=excluded.error,observed_at=excluded.observed_at,resolved_at=NULL''',
                    (ident,analyzer.version,now))
                self.db.execute("UPDATE jobs SET state='stale',error='source_changed',updated_at=?,lease_token=NULL WHERE notice_id=? AND state!='stale'",(now,ident))
            for notice in notices:
                self.db.execute('UPDATE input_failures SET resolved_at=? WHERE notice_id=? AND resolved_at IS NULL',(now,notice.notice_id))
                self.db.execute("UPDATE jobs SET state='stale',error='source_changed',updated_at=?,lease_token=NULL WHERE notice_id=? AND input_hash!=? AND state!='stale'",
                                (now,notice.notice_id,notice.input_hash))
                reason=analyzer.skip_reason(notice)
                cur=self.db.execute('''INSERT OR IGNORE INTO jobs
                    (notice_id,input_hash,content_hash,pipeline_version,model,prompt_version,schema_version,rule_version,
                     mode,state,created_at,updated_at,next_at,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (notice.notice_id,notice.input_hash,notice.content_hash,analyzer.version,analyzer.model_name,
                     analyzer.prompt_version,analyzer.schema_version,analyzer.rule_version,
                     'offline_mock' if analyzer.mock else 'provider','skipped' if reason else 'queued',now,now,now,reason))
                inserted+=cur.rowcount
                existing=self.db.execute('SELECT * FROM jobs WHERE notice_id=? AND input_hash=? AND pipeline_version=?',
                    (notice.notice_id,notice.input_hash,analyzer.version)).fetchone()
                # Exact reverted input may reuse a revalidated proposal, never a human approval.
                if existing['state']=='stale':
                    restored=existing['result']
                    state='skipped' if reason else 'queued' if existing['attempts']<self.policy.max_attempts else 'failed'
                    if existing['result']:
                        try:
                            from .async_contract import validate
                            supplied,_=analyzer.prepare(notice)
                            from .analysis_models import reference_datetime
                            checked=validate(json.loads(existing['result'])['proposal'],notice,supplied,analyzer,reference_datetime(None))
                            state=checked['state']
                            restored=json.dumps(checked,ensure_ascii=False)
                        except (ValueError,KeyError,TypeError):pass
                    self.db.execute('UPDATE jobs SET state=?,error=?,updated_at=?,next_at=?,result=? WHERE id=?',
                        (state,reason,now,now,restored,existing['id']))
            next_cursor=ids[-1] if len(ids)==self.policy.scan_limit else 0
            self.db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(next_cursor)))
            self.db.execute("INSERT INTO metadata VALUES ('active_pipeline',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(analyzer.version,))
        return {'scanned':len(ids),'invalid_inputs':len(invalid),'enqueued':inserted,'cursor':next_cursor,'model_requests':0}

    def _fail(self, job, error, now):
        retry=error in RETRYABLE and job['attempts']<self.policy.max_attempts
        due=now+min(self.policy.backoff_seconds*2**max(0,job['attempts']-1),86400)
        self.db.execute('''UPDATE jobs SET state=?,next_at=?,error=?,updated_at=?,lease_token=NULL,lease_until=NULL WHERE id=?''',
            ('retry_wait' if retry else 'failed',due,error,now,job['id']))

    def recover(self, now):
        for job in self.db.execute("SELECT * FROM jobs WHERE state='running' AND lease_until<=?",(now,)).fetchall():
            self.db.execute("UPDATE attempts SET state='unknown',error='worker_lost',finished_at=? WHERE job_id=? AND ordinal=? AND state='reserved'",(now,job['id'],job['attempts']))
            self._fail(job,'worker_lost',now)

    def claim(self, analyzer):
        now=self.clock()
        with self.transaction():
            self.recover(now)
            job=self.db.execute("SELECT * FROM jobs WHERE pipeline_version=? AND state IN ('queued','retry_wait','budget_blocked') AND next_at<=? ORDER BY next_at,id LIMIT 1",
                                (analyzer.version,now)).fetchone()
            if not job:return None
            if job['attempts']>=self.policy.max_attempts:
                self.db.execute("UPDATE jobs SET state='failed',updated_at=? WHERE id=?",(now,job['id']))
                return None
            self.ensure_source()
            try:
                with source_snapshot(self.raw_path) as repo:notice=repo.input(job['notice_id'])
            except (ValueError,TypeError,sqlite3.Error,OSError):
                self.db.execute("UPDATE jobs SET state='stale',error='source_unavailable',updated_at=? WHERE id=?",(now,job['id']))
                return None
            if notice.input_hash!=job['input_hash']:
                self.db.execute("UPDATE jobs SET state='stale',error='source_changed',updated_at=? WHERE id=?",(now,job['id']))
                return None
            reserved=0 if analyzer.mock else analyzer.reservation(notice)
            recent=self.db.execute('''SELECT COUNT(*) AS requests,COALESCE(SUM(MAX(reserved_tokens,COALESCE(total_tokens,0))),0) AS tokens,
                MIN(reserved_at) AS oldest,SUM(CASE WHEN total_tokens>reserved_tokens THEN 1 ELSE 0 END) AS overrun
                FROM attempts WHERE billable=1 AND reserved_at>?''',(now-self.policy.window_seconds,)).fetchone()
            if not analyzer.mock and (recent['requests']>=self.policy.request_limit or recent['tokens']+reserved>self.policy.token_limit or recent['overrun']):
                due=(recent['oldest']+self.policy.window_seconds+0.001) if recent['oldest'] is not None else now+self.policy.window_seconds
                self.db.execute("UPDATE jobs SET state='budget_blocked',next_at=?,error=?,updated_at=? WHERE id=?",
                    (due,'usage_exceeded_reservation' if recent['overrun'] else 'budget_exhausted',now,job['id']))
                return None
            token=uuid.uuid4().hex;attempt_id=uuid.uuid4().hex;ordinal=job['attempts']+1
            self.db.execute("UPDATE jobs SET state='running',attempts=?,lease_token=?,lease_until=?,error=NULL,updated_at=? WHERE id=?",
                            (ordinal,token,now+self.policy.lease_seconds,now,job['id']))
            self.db.execute('''INSERT INTO attempts (id,job_id,ordinal,reserved_at,reserved_tokens,billable,state)
                VALUES (?,?,?,?,?,?,'reserved')''',(attempt_id,job['id'],ordinal,now,reserved,int(not analyzer.mock)))
            claimed=dict(self.db.execute('SELECT * FROM jobs WHERE id=?',(job['id'],)).fetchone())
            claimed.update(attempt_id=attempt_id,notice=notice)
            return claimed

    def record_event(self, claim, event):
        # Never persist payloads, response text, prompts, URLs or provider diagnostics.
        usage=usage_values({'usage':event.get('usage')})
        status=event.get('status_code')
        status=status if type(status) is int and 100<=status<=599 else None
        cfg=claim.get('config');cost=None;currency=None
        if usage and cfg and cfg.input_price_per_million is not None and cfg.output_price_per_million is not None:
            cost=(usage['prompt_tokens']*cfg.input_price_per_million+usage['completion_tokens']*cfg.output_price_per_million)/1000000
            currency=cfg.cost_currency
        with self.transaction():
            self.db.execute('''UPDATE attempts SET dispatch_started=1,status_code=COALESCE(?,status_code),
                prompt_tokens=COALESCE(?,prompt_tokens),completion_tokens=COALESCE(?,completion_tokens),
                total_tokens=COALESCE(?,total_tokens),estimated_cost=COALESCE(?,estimated_cost),currency=COALESCE(?,currency) WHERE id=?''',
                (status,usage['prompt_tokens'] if usage else None,usage['completion_tokens'] if usage else None,
                 usage['total_tokens'] if usage else None,cost,currency,claim['attempt_id']))

    def finish(self, claim, *, result=None, error=None):
        now=self.clock();error=error if error in ERRORS else 'internal_error' if error else None
        with self.transaction():
            self.db.execute("UPDATE attempts SET state=?,finished_at=?,error=? WHERE id=? AND state='reserved'",
                ('unknown' if error in {'timeout','worker_lost'} else 'completed',now,error,claim['attempt_id']))
            job=self.db.execute('SELECT * FROM jobs WHERE id=?',(claim['id'],)).fetchone()
            if job['state']!='running' or job['lease_token']!=claim['lease_token'] or job['lease_until']<=now:return False
            try:
                self.ensure_source()
                with source_snapshot(self.raw_path) as repo:current=repo.input(job['notice_id'])
                changed=current.input_hash!=job['input_hash']
            except (ValueError,TypeError,sqlite3.Error,OSError):changed=True
            if changed:
                self.db.execute("UPDATE jobs SET state='stale',error='source_changed',updated_at=?,lease_token=NULL,lease_until=NULL WHERE id=?",(now,job['id']))
            elif error:self._fail(job,error,now)
            else:
                if not isinstance(result,dict) or result.get('state') not in {'partial','succeeded'}:raise ValueError('Invalid worker result')
                self.db.execute('''UPDATE jobs SET state=?,result=?,updated_at=?,lease_token=NULL,lease_until=NULL WHERE id=?''',
                                (result['state'],json.dumps(result,ensure_ascii=False),now,job['id']))
            return True

    def report(self, *, analyzer=None, include_results=False):
        self.ensure_source()
        jobs=[];input_failures=[]
        current_ids=set()
        active=self.db.execute("SELECT value FROM metadata WHERE key='active_pipeline'").fetchone()
        version=analyzer.version if analyzer else active[0] if active else None
        with source_snapshot(self.raw_path) as repo:
            for row in self.db.execute('SELECT * FROM jobs ORDER BY id'):
                item={k:row[k] for k in ['id','notice_id','input_hash','content_hash','pipeline_version','model','prompt_version',
                    'schema_version','rule_version','mode','state','attempts','error','created_at','updated_at','next_at']}
                try:current=repo.input(row['notice_id']).input_hash==row['input_hash']
                except (ValueError,TypeError):current=False
                item['stored_state']=row['state']
                if not current:item['state']='stale'
                elif version and row['pipeline_version']!=version:item['state']='outdated'
                elif row['state']=='running' and row['lease_until']<=self.clock():item['state']='lease_expired'
                if include_results and item['state'] in {'succeeded','partial'}:item['result']=json.loads(row['result'])
                if current and (not version or row['pipeline_version']==version):current_ids.add(row['notice_id'])
                jobs.append(item)
            # Older private queues remain readable without a read-time migration.
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='input_failures'").fetchone():
                for row in self.db.execute('SELECT * FROM input_failures ORDER BY notice_id,pipeline_version'):
                    item=dict(row)
                    try:repo.input(row['notice_id']);item['state']='resolved'
                    except (ValueError,TypeError):item['state']='failed'
                    input_failures.append(item)
                    if item['state']=='failed' and (not version or row['pipeline_version']==version):current_ids.add(row['notice_id'])
            unqueued=[r[0] for r in repo.db.execute('SELECT id FROM notices ORDER BY id') if r[0] not in current_ids]
        return {'schema_version':1,'scope':'private_analysis_only','publication_permission':False,
            'input_failures':input_failures,'input_failure_count':sum(f['state']=='failed' and (not version or f['pipeline_version']==version) for f in input_failures),
            'not_enqueued_count':len(unqueued),'not_enqueued_ids':unqueued[:100],
            'jobs':jobs,'counts':{state:sum(j['state']==state for j in jobs) for state in sorted({j['state'] for j in jobs})}}

    def ledger(self):
        rows=[dict(r) for r in self.db.execute('SELECT * FROM attempts ORDER BY reserved_at,id')]
        live=[r for r in rows if r['billable']];known=[r for r in live if r['total_tokens'] is not None]
        priced=[r for r in live if r['estimated_cost'] is not None];currencies={r['currency'] for r in priced}
        complete=bool(live) and len(priced)==len(live) and len(currencies)==1
        return {'schema_version':1,'attempts':rows,'mock_attempts':len(rows)-len(live),
            'request_reservations':len(live),'known_usage_requests':len(known),'usage_complete':len(known)==len(live),
            'dispatch_attempts':sum(r['dispatch_started'] for r in live),
            'known_response_requests':sum(r['status_code'] is not None for r in live),
            'reservation_overruns':sum(r['total_tokens'] is not None and r['total_tokens']>r['reserved_tokens'] for r in live),
            'total_tokens_known':sum(r['total_tokens'] for r in known),'reserved_tokens':sum(r['reserved_tokens'] for r in live),
            'estimated_cost':sum(r['estimated_cost'] for r in priced) if complete else None,
            'currency':next(iter(currencies)) if complete else None,
            'cost_note':'Configured rate estimate, not invoice; unknown usage/cost is not zero'}
