"""Bounded static rule facts for display; never cache live availability."""
from collections import OrderedDict
from dataclasses import dataclass
import threading

@dataclass(frozen=True)
class DescriptionFacts:
    category: str
    relevance_level: str
    eligibility_status: str
    evidence: tuple
    registration_deadline: str | None
    internal_deadline: str | None
    registration_start_date: str | None
    event_start_date: str | None
    event_end_date: str | None

_VALUES=OrderedDict();_SIZE=0;_LOCK=threading.Lock();_LIMIT=16*1024*1024

def description_facts(analyzer,notice,now):
    """Cache only describe()'s static fields. Rebuild relative dates each day;
    the caller always checks exact deadlines and source health at the live clock.
    """
    global _SIZE
    implementation=getattr(analyzer.analyze,'__func__',analyzer.analyze)
    key=(analyzer.version,implementation,notice,now.date())
    with _LOCK:
        found=_VALUES.get(key)
        if found is not None:_VALUES.move_to_end(key);return found[0]
    result=analyzer.analyze(notice,reference_time=now)
    value=DescriptionFacts(result.category,result.relevance_level,result.eligibility_status,tuple(result.evidence),
        result.registration_deadline,result.internal_deadline,result.registration_start_date,result.event_start_date,result.event_end_date)
    weight=4096+len(notice.content)*8+len(result.evidence)*512
    if weight<=_LIMIT//4:
        with _LOCK:
            if key not in _VALUES:
                while _VALUES and (_SIZE+weight>_LIMIT or len(_VALUES)>=512):
                    _,(_,old)=_VALUES.popitem(last=False);_SIZE-=old
                _VALUES[key]=(value,weight);_SIZE+=weight
    return value
