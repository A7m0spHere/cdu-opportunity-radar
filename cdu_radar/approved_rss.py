"""Approved snapshot RSS; original technical feed remains untouched."""
from datetime import datetime
from email.utils import format_datetime
import html
import json
import os
from pathlib import Path
import tempfile
from xml.etree import ElementTree as ET

from .publication import record_event
from .rss import xml_text
from .analysis_models import reference_datetime


def render(items):
    root=ET.Element('rss',version='2.0');channel=ET.SubElement(root,'channel')
    def tag(parent,name,value,**attrs):ET.SubElement(parent,name,attrs).text=xml_text(value)
    tag(channel,'title','成都大学 · 人工批准的参与机会')
    tag(channel,'link','https://www.cdu.edu.cn/')
    tag(channel,'description','仅包含有效人工发布审批；未知事项明确标注。')
    tag(channel,'language','zh-cn')
    for row in items:
        a=json.loads(row['approved_payload']);item=ET.SubElement(channel,'item')
        tag(item,'title',a['title']);tag(item,'link',a['source_url'])
        tag(item,'guid',row['guid'],isPermaLink='false')
        tag(item,'pubDate',format_datetime(datetime.fromisoformat(row['published_at'])))
        tag(item,'source',a['source_name'],url=a['source_url'])
        lines=[a['summary'],'来源：'+a['source_name']]
        names={'registration_deadline':'主办方报名截止','internal_deadline':'校内报名/推荐截止','registration_start_date':'报名开始','eligibility':'参与范围（原文）','ai_use_policy':'AI 使用规则（原文）'}
        for name,label in names.items():
            fact=a['facts'][name]
            value=fact['quote'] if name in {'eligibility','ai_use_policy'} and fact else fact['value'] if fact else '未知，需核验；不能据此认定符合或允许'
            lines.append(label+'：'+value)
        lines.append('官方原文：'+a['source_url'])
        lines.append('资格条款不等于所有学生已符合条件；选拔及其他细则以官方原文为准。')
        tag(item,'description','<p>'+html.escape('\n'.join(lines)).replace('\n','<br/>')+'</p>')
    ET.indent(root,space='  ')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def atomic_write(path, content):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,prefix='.approved-',suffix='.tmp',delete=False) as f:
            temp=f.name;f.write(content);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if temp and os.path.exists(temp):os.unlink(temp)


def export_approved(repository, output, *, confirm, now=None):
    """Reserve identity/time durably, then replace XML under a fresh DB write lock.

    If replacement fails or the process dies, retry uses the reserved timestamp.
    A crash after replacement but before DB commit can repeat a stable item, never
    a new GUID/pubDate. Static XML and SQLite cannot form a single atomic transaction.
    """
    db=repository.db;point=reference_datetime(now)
    db.execute('BEGIN IMMEDIATE')
    try:
        plan=repository.feed_plan(point)
        if plan['plan_hash']!=confirm:raise ValueError('Feed confirmation is stale; preview again')
        identities=sorted((r['notice_id'],r['id'],r['notice_version_hash']) for r in plan['items'])
        for r in plan['items']:
            if not r['published_at']:db.execute('UPDATE publication_state SET published_at=? WHERE notice_id=?',(point.isoformat(),r['notice_id']))
        db.commit()
        db.execute('BEGIN IMMEDIATE')
        live=repository.feed_plan(point)
        if sorted((r['notice_id'],r['id'],r['notice_version_hash']) for r in live['items'])!=identities:raise ValueError('Approvals changed before file replacement; preview again')
        atomic_write(output,render(live['items']))
        for r in live['items']:
            if r['publication_status']!='published':
                db.execute("UPDATE publication_state SET publication_status='published',first_exported_at=COALESCE(first_exported_at,?) WHERE notice_id=?",(point.isoformat(),r['notice_id']))
                record_event(db,r['notice_id'],r['id'],'published','system','Approved snapshot exported atomically',{'guid':r['guid'],'published_at':r['published_at']},point.isoformat())
        db.commit()
        return {'output':str(output),'items':len(live['items']),'new_publications':plan['new_publications'],
            'excluded':live['excluded'],'guids':[r['guid'] for r in live['items']]}
    except BaseException:
        db.rollback();raise
