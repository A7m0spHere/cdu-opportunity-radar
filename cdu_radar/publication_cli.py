"""Trusted local admin CLI. Read-only previews; writes require exact confirmation."""
import json
from pathlib import Path
import sqlite3
import sys
from xml.etree import ElementTree as ET

from .analysis_models import reference_datetime
from .analysis_store import AnalysisRepository
from .llm_workflow import readonly_repository
from .publication import PublicationRepository, migrate
from .approved_rss import export_approved
from .storage import Store


def register_review(actions):
    for name in ['show','history','template','approve','reject','defer','revoke']:
        p=actions.add_parser(name,help='Local publication '+name)
        p.add_argument('notice_id',type=int)
        p.add_argument('--db',type=Path,default=Path('data/m2a.sqlite3'))
        if name in {'approve','reject','defer','revoke'}:
            p.add_argument('--reviewer',required=True);p.add_argument('--reason',required=True)
            p.add_argument('--confirm',help='Exact confirmation_token from preview; omission performs a read-only preview')
        if name=='approve':
            p.add_argument('--draft',type=Path,required=True)
            p.add_argument('--ack-unknown',action='store_true')
            p.add_argument('--ack-correction',action='store_true')


def publication_command(args):
    try:
        if not args.db.is_file():raise ValueError('Database does not exist')
        now=reference_datetime(getattr(args,'as_of',None))
        action=getattr(args,'review_action',None)
        if args.command=='export-approved-rss':
            resolved=args.output.resolve()
            if resolved==args.db.resolve() or resolved.exists() and resolved.samefile(args.db) or resolved.suffix.lower()!='.xml' or resolved.name in {'feed.xml','m1-feed.xml'}:
                raise ValueError('Use a separate approved .xml output; never overwrite the original feed or database')
            if resolved.exists():
                if ET.parse(resolved).findtext('./channel/title')!='成都大学 · 人工批准的参与机会':raise ValueError('Output is not an approved feed; refuse to overwrite it')
        with readonly_repository(args.db) as repo:
            pub=PublicationRepository(repo)
            if args.command=='export-approved-rss':
                plan=pub.feed_plan(now)
                if not args.confirm:
                    print(json.dumps({'preview':True,'output':str(args.output),'items':len(plan['items']),'new_publications':plan['new_publications'],
                        'plan_hash':plan['plan_hash'],'excluded':plan['excluded'],'guids':[r['guid'] for r in plan['items']],
                        'entries':[{'notice_id':r['notice_id'],'guid':r['guid'],'first_publication_time':r['published_at'],
                            'approved_content':json.loads(r['approved_payload'])} for r in plan['items']]},ensure_ascii=False,indent=2));return 0
                if args.confirm!=plan['plan_hash']:raise ValueError('Feed confirmation does not match preview')
            elif action=='list':
                print(json.dumps(pub.queue(now,args.all),ensure_ascii=False,indent=2));return 0
            elif action in {'show','history','template'}:
                result=pub.show(args.notice_id,now) if action=='show' else pub.history(args.notice_id) if action=='history' else pub.template(args.notice_id)
                print(json.dumps(result,ensure_ascii=False,indent=2));return 0
            else:
                status={'approve':'approved','reject':'rejected','defer':'needs_revision','revoke':'revoked'}[action]
                draft=json.loads(args.draft.read_text('utf-8-sig')) if action=='approve' else None
                options={'draft':draft,'ack_unknown':getattr(args,'ack_unknown',False),'ack_correction':getattr(args,'ack_correction',False),'now':now}
                plan=pub.preview(args.notice_id,status,args.reviewer,args.reason,**options)
                if not args.confirm:
                    print(json.dumps(plan,ensure_ascii=False,indent=2));return 0
                current=pub.current(args.notice_id)
                if args.confirm!=plan['confirmation_token'] and not(current and current['confirmation_token']==args.confirm):raise ValueError('Confirmation does not match preview')
        with Store(args.db) as store:
            # Adds only publication tables; never imports legacy approvals or labels.
            migrate(store.db)
            repo=AnalysisRepository.__new__(AnalysisRepository);repo.store=store;repo.db=store.db
            pub=PublicationRepository(repo)
            if args.command=='export-approved-rss':result=export_approved(pub,args.output,confirm=args.confirm,now=now)
            else:
                with store.db:
                    store.db.execute('BEGIN IMMEDIATE')
                    result=pub.decide(args.notice_id,status,args.reviewer,args.reason,confirm=args.confirm,**options)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (OSError,ValueError,sqlite3.Error,ET.ParseError) as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr);return 2
