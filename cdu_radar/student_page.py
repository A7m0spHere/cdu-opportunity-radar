"""Small same-origin page and public projection of the gated RSS snapshot."""
from importlib.resources import files

CATEGORIES=('学科竞赛','科研与创新实践','AI / 计算机相关活动','校园活动','其他通知')
ASSETS={
    '/':('index.html','text/html; charset=utf-8'),
    '/app.css':('app.css','text/css; charset=utf-8'),
    '/app.js':('app.js','text/javascript; charset=utf-8'),
    '/favicon.svg':('favicon.svg','image/svg+xml'),
}


def assets():
    root=files('cdu_radar').joinpath('web')
    return {route:(root.joinpath(name).read_bytes(),mime) for route,(name,mime) in ASSETS.items()}


def public_snapshot(items, *, refreshed_at, data_mode):
    entries=[]
    for item in items:
        entry={key:item[key] for key in (
            'guid','title','source_name','source_url','source_published_date','summary',
            'category','registration_status','listing','source_checked_at')}
        entry['first_aggregated_at']=item['first_published_at']
        entries.append(entry)
    # Unknown original dates use first aggregation only for ordering, never as
    # a claimed source publication date. The UI labels both separately.
    entries.sort(key=lambda r:(r['source_published_date'] or r['first_aggregated_at'][:10],
        r['first_aggregated_at'],r['guid']),reverse=True)
    return {'schema_version':1,'items':entries,'categories':list(CATEGORIES),
        'refreshed_at':refreshed_at,'data_mode':data_mode,
        'counts':{'all':len(entries),**{scope:sum(i['listing']==scope for i in entries) for scope in ('current','history','unknown')}}}


def public_snapshot_v2(items, *, refreshed_at, data_mode, filter_options):
    payload=public_snapshot(items,refreshed_at=refreshed_at,data_mode=data_mode)
    by_guid={i['guid']:i for i in items}
    for entry in payload['items']:
        item=by_guid[entry['guid']]
        entry.update({key:item[key] for key in ('publisher_id','discovery_source_ids','classification')})
    payload.update(schema_version=2,taxonomy_version=1,filter_options=filter_options)
    return payload
