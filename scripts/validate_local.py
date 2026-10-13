"""Read-only structural checks; prints counts rather than personal article text."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from xml.etree import ElementTree as ET

import feedparser


def validate(database, feed, baseline=None):
    db=sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    rows=db.execute('SELECT * FROM notices').fetchall()
    assert len({r['canonical_url'] for r in rows})==len(rows)
    for row in rows:
        assert row['title'] and row['content']
        assert row['content_hash']==hashlib.sha256(row['content'].encode()).hexdigest()
        assert row['content_source_id'] and row['content_domain']
    reader=feedparser.parse(feed.read_bytes())
    assert not reader.bozo and reader.version=='rss20'
    items=ET.parse(feed).findall('./channel/item')
    guids=[item.findtext('guid') for item in items]
    assert len(guids)==len(set(guids))
    assert set(guids)<= {r['canonical_url'] for r in rows}
    preserved=0
    if baseline:
        old=sqlite3.connect(baseline.resolve().as_uri()+'?mode=ro',uri=True)
        old.row_factory=sqlite3.Row
        by_url={r['canonical_url']:r for r in rows}
        for prior in old.execute('SELECT * FROM notices'):
            new=by_url[prior['canonical_url']]
            for key in ['id','canonical_url','discovered_at','content_hash']:
                assert new[key]==prior[key],key
            assert prior['canonical_url'] in guids
            preserved+=1
        old.close()
    report={'notices':len(rows),'feed_items':len(items),'rss20':True,'preserved_m0_records':preserved,
            'body_links':db.execute("SELECT count(*) FROM notice_links WHERE kind='body'").fetchone()[0],
            'attachments':db.execute("SELECT count(*) FROM notice_links WHERE kind='attachment'").fetchone()[0],
            'multi_discovery_articles':db.execute('SELECT count(*) FROM (SELECT notice_id FROM notice_sources GROUP BY notice_id HAVING count(*)>1)').fetchone()[0]}
    db.close()
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--db',type=Path,default=Path('data/m1.sqlite3'))
    p.add_argument('--feed',type=Path,default=Path('data/m1-feed.xml'))
    p.add_argument('--baseline',type=Path)
    args=p.parse_args()
    print(json.dumps(validate(args.db,args.feed,args.baseline),ensure_ascii=False))
