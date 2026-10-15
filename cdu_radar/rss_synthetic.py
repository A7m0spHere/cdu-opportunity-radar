"""An explicitly marked, separate synthetic lab. Never operates on user databases."""
from contextlib import contextmanager
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import uuid

from .analysis_models import reference_datetime
from .analysis_store import AnalysisRepository
from .models import Article
from .publication import PublicationRepository, migrate
from .sources import load_sources
from .storage import Store

KIND='m3a-synthetic-only-v1'


@contextmanager
def lab(root):
    root=Path(root).resolve();marker=root/'synthetic-owner.json';path=root/'synthetic.sqlite3'
    if not marker.is_file() or not path.is_file() or marker.is_symlink() or path.is_symlink():raise ValueError('Not an owned synthetic lab')
    meta=json.loads(marker.read_text('utf-8'))
    # Verify in read-only mode before any Store initialization or migration.
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute('SELECT kind,dataset_id FROM m3a_synthetic_meta').fetchone()
        if not row or meta!={'kind':KIND,'dataset_id':row[1]} or row[0]!=KIND:raise ValueError('Synthetic identity mismatch')
    with Store(path) as store:
        yield store,PublicationRepository(AnalysisRepository(store))


def approve_fixture(pub, ident, ttl, now=None):
    point=reference_datetime(now)
    if not 1<=ttl<=86400:raise ValueError('Synthetic TTL must be 1..86400 seconds')
    n=pub.repository.input(ident)
    draft={'title':n.title,'summary':'合成协议测试条目，不代表真实通知或真实发布。',
        'source_url':n.canonical_url,'participation_quote':n.title,'valid_until':(point+timedelta(seconds=ttl)).isoformat(),
        'facts':{k:None for k in ['registration_deadline','registration_start_date','internal_deadline','eligibility','ai_use_policy']}}
    plan=pub.preview(ident,'approved','synthetic-fixture','Synthetic protocol test only',draft,True,True,point)
    with pub.db:
        pub.db.execute('BEGIN IMMEDIATE')
        pub.decide(ident,'approved','synthetic-fixture','Synthetic protocol test only',confirm=plan['confirmation_token'],draft=draft,ack_unknown=True,ack_correction=True,now=point)


def add_fixture(store, pub, ttl=3600, now=None):
    ident=store.db.execute('SELECT COALESCE(MAX(id),0)+1 FROM notices').fetchone()[0]
    title=('合成初始化' if ident==1 else f'合成新增 {ident}')+' · AI 学习竞赛报名'
    dataset=store.db.execute('SELECT dataset_id FROM m3a_synthetic_meta').fetchone()[0]
    url=f'https://computer.cdu.edu.cn/info/1/{900000+ident}.htm?synthetic_lab={dataset}'
    store.save_notice('computer',url,Article(title,None,'本条为合成测试。欢迎报名参与 AI 学习竞赛。'))
    approve_fixture(pub,ident,ttl,now)
    return ident


def init_lab(root, ttl=3600, now=None):
    root=Path(root).resolve()
    if root.exists():raise ValueError('Synthetic lab must use a new directory; no overwrite or backup import')
    if not 1<=ttl<=86400:raise ValueError('Synthetic TTL must be 1..86400 seconds')
    root.mkdir(parents=True)
    meta={'kind':KIND,'dataset_id':str(uuid.uuid4())}
    with Store(root/'synthetic.sqlite3') as store:
        store.sync_sources([s for s in load_sources() if s.id=='computer'])
        pub=PublicationRepository(AnalysisRepository(store));migrate(store.db)
        with store.db:
            store.db.execute('CREATE TABLE m3a_synthetic_meta (kind TEXT NOT NULL,dataset_id TEXT NOT NULL)')
            store.db.execute('INSERT INTO m3a_synthetic_meta VALUES (?,?)',(KIND,meta['dataset_id']))
        ident=add_fixture(store,pub,ttl,now)
    (root/'synthetic-owner.json').write_text(json.dumps(meta,indent=2)+'\n',encoding='utf-8')
    return ident


def synthetic_command(args):
    try:
        if args.synthetic_action=='init':ident=init_lab(args.directory,args.ttl)
        else:
            with lab(args.directory) as (store,pub):
                if args.synthetic_action=='add':ident=add_fixture(store,pub,args.ttl)
                else:
                    ident=args.notice_id;n=pub.repository.input(ident)
                    if args.synthetic_action=='approve':approve_fixture(pub,ident,args.ttl)
                    elif args.synthetic_action=='update':store.save_notice('computer',n.canonical_url,Article(n.title,n.published_at,n.content+'\n合成原文更新：需要重新审核。'))
                    else:
                        plan=pub.preview(ident,'revoked','synthetic-fixture','Synthetic revoke test')
                        with store.db:
                            store.db.execute('BEGIN IMMEDIATE')
                            pub.decide(ident,'revoked','synthetic-fixture','Synthetic revoke test',confirm=plan['confirmation_token'])
        print(json.dumps({'synthetic_only':True,'notice_id':ident,'directory':str(args.directory)},ensure_ascii=False));return 0
    except (OSError,ValueError,sqlite3.Error):
        print('Synthetic operation rejected; verify the isolated lab and arguments');return 2
