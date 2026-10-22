"""Bounded, deterministic RSS projections of quality-gated originals."""
from datetime import datetime
from email.utils import format_datetime
import html
import json
import threading
import time
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from .models import InvalidFilter
from .rss import xml_text
from .taxonomy import API_V2, FILTER_KEYS, filter_payload, query_values

ROUTES = ('/rss/v1/all.xml', '/rss/v1/featured.xml')
NS = 'urn:cdu-radar:rss:v1'
ET.register_namespace('subscription', NS)
MAX_ITEMS = 100
MAX_BYTES = 1024 * 1024


class RequestBudget:
    """One global token bucket; filter combinations cannot multiply the budget."""
    def __init__(self, clock=time.monotonic, *, burst=60, per_second=1):
        self.clock, self.burst, self.per_second = clock, burst, per_second
        self.tokens, self.last = float(burst), clock()
        self.lock = threading.Lock()

    def allow(self):
        with self.lock:
            now = self.clock()
            self.tokens = min(self.burst, self.tokens + max(0, now - self.last) * self.per_second)
            self.last = now
            if self.tokens < 1:
                return False
            self.tokens -= 1
            return True


def route_path(path):
    return path.partition('?')[0]


def filter_path(path):
    base, mark, query = path.partition('?')
    if base not in ROUTES:
        raise InvalidFilter('Invalid RSS route')
    # Use the existing taxonomy encoding, enum and combination contract.
    translated = API_V2 + ('?' + query if mark else '')
    if len(path) > 2048:
        raise InvalidFilter('RSS path too long')
    query_values(translated)
    return translated


def canonical_path(selection, filters):
    if selection not in {'all', 'featured'}:
        raise InvalidFilter('Invalid RSS selection')
    values = [(k, filters[k]) for k in FILTER_KEYS if filters.get(k) not in {None, '', 'all'}]
    # Topics are an unordered AND set. All defaults are omitted.
    values = [(k, ','.join(sorted(v.split(','))) if k == 'topic' else v) for k, v in values]
    return '/rss/v1/' + selection + '.xml' + ('?' + urlencode(values) if values else '')


def filtered(payload, path):
    return filter_payload(payload, filter_path(path))


def render(payload, path, *, collection=None, first_featured=None):
    from .featured import scope_stats

    data = filtered(payload, path)
    featured = route_path(path) == ROUTES[1]
    entries = data['items']
    stats = None
    if featured:
        cohort = [dict(i, featured=collection[i['guid']]) for i in entries]
        stats = scope_stats(cohort)
        # An uninitialized analysis set is unavailable, never a healthy empty feed.
        if stats['coverage']['total'] and not stats['coverage']['complete']:
            raise ValueError('Featured analysis not ready')
        entries = [i for i in cohort if i['featured']['decision'] == 'selected']
    stamp = (lambda i: first_featured[i['guid']]) if featured else (lambda i: i['first_aggregated_at'])
    entries = sorted(entries, key=lambda i: (stamp(i), i['guid']), reverse=True)
    total = len(entries)
    entries = entries[:MAX_ITEMS]
    root = ET.Element('rss', version='2.0')
    channel = ET.SubElement(root, 'channel')

    def tag(parent, name, value, **attrs):
        ET.SubElement(parent, name, attrs).text = xml_text(str(value))

    def meta(parent, name, value):
        tag(parent, '{' + NS + '}' + name, value)

    tag(channel, 'title', '成都大学 · ' + ('重要精选' if featured else '全部合格信息'))
    tag(channel, 'link', 'https://www.cdu.edu.cn/')
    description = '可信官方原文；摘要为原文节选，资格和报名未知时请核对官方原文。'
    if featured:
        description += '精选不等于当前可报名；' + ('分析覆盖不完整，未入选不代表不重要。' if stats['state'] != 'ready' else '当前分析集合已完成。')
    tag(channel, 'description', description)
    tag(channel, 'language', 'zh-cn')
    meta(channel, 'canonical_path', canonical_path('featured' if featured else 'all', data['applied_filters']))
    meta(channel, 'data_mode', payload['data_mode'])
    meta(channel, 'total', total)
    meta(channel, 'returned', len(entries))
    meta(channel, 'truncated', str(total > len(entries)).lower())
    if featured:
        meta(channel, 'analysis_state', stats['state'])
        meta(channel, 'coverage', json.dumps(stats['coverage'], sort_keys=True, separators=(',', ':')))
        # Empty cohorts still carry the policy and pipeline in the gated collection token.
        if collection:
            meta(channel, 'policy_version', next(iter(collection.values()))['policy_version'])
    for row in entries:
        item = ET.SubElement(channel, 'item')
        for key, value in [('title', row['title']), ('link', row['source_url']), ('category', row['category'])]:
            tag(item, key, value)
        tag(item, 'guid', row['guid'], isPermaLink='false')
        tag(item, 'pubDate', format_datetime(datetime.fromisoformat(stamp(row))))
        tag(item, 'source', row['source_name'], url=row['source_url'])
        for key in ('source_published_date', 'registration_status', 'listing', 'first_aggregated_at'):
            meta(item, key, row[key] or 'unknown')
        lines = [row['summary'], '来源发布日期：' + (row['source_published_date'] or '未知'),
                 '报名状态：' + {'possible': '可能仍可参与', 'closed': '明确已截止', 'unknown': '未知，需核验'}[row['registration_status']],
                 '摘要为官方原文节选；参与资格、附件要求及完整信息以官方原文为准。']
        if featured:
            meta(item, 'first_featured_at', stamp(row))
            lines += ['首次入选精选：' + stamp(row), 'RSS pubDate 为首次入选精选时间，不是来源发布日期。',
                      ('重要性为智能建议，经原文引用校验，不代表官方重要性认定或当前可报名。'
                       if 'model_evidence' in row['featured']['reason_codes'] else
                       '重要性为智能建议，经原文规则复核，不代表官方重要性认定或当前可报名。')]
            lines += ['原文入选依据（' + e['label'] + '）：' + e['quote'] for e in row['featured']['evidence']]
            unknowns = {'eligibility_unknown': '参与资格未知', 'registration_unknown': '报名状态未知', 'attachments_unread': '附件正文未读取'}
            lines += [unknowns[u] for u in row['featured']['uncertainties']]
        else:
            lines += ['RSS pubDate 为首次聚合发布登记时间，不是来源发布日期。']
        tag(item, 'description', '<p>' + html.escape('\n'.join(lines)).replace('\n', '<br/>') + '</p>')
    ET.indent(root, space='  ')
    body = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    if len(body) > MAX_BYTES:
        raise ValueError('RSS response exceeds byte limit')
    return body
