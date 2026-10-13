"""Append-only analysis/label/review data; no writes to raw notices."""
from dataclasses import asdict,replace
import json

from .analysis_models import AnalysisInput,InputLink,ManualLabel,NoticeAnalysis,REVIEWS,choice,fingerprint
from .models import utc_now
from .privacy import sanitize

SCHEMA="""
CREATE TABLE IF NOT EXISTS notice_analyses (
 id INTEGER PRIMARY KEY, notice_id INTEGER NOT NULL REFERENCES notices(id),
 content_hash TEXT NOT NULL,input_hash TEXT NOT NULL,analyzer_version TEXT NOT NULL,
 analyzed_at TEXT NOT NULL,model_name TEXT,payload TEXT NOT NULL,
 UNIQUE(notice_id,input_hash,analyzer_version));
CREATE TABLE IF NOT EXISTS manual_labels (
 id INTEGER PRIMARY KEY,notice_id INTEGER NOT NULL REFERENCES notices(id),
 content_hash TEXT NOT NULL,input_hash TEXT NOT NULL,label_origin TEXT NOT NULL CHECK(label_origin IN ('human','synthetic')),
 labeler TEXT NOT NULL,labeled_at TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analysis_reviews (
 id INTEGER PRIMARY KEY,notice_id INTEGER NOT NULL REFERENCES notices(id),input_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),reason TEXT NOT NULL,
 reviewer TEXT NOT NULL,reviewed_at TEXT NOT NULL);
"""


class AnalysisRepository:
    def __init__(self,store):
        self.store=store;self.db=store.db
        # executescript contains an explicit transaction; existing raw tables are untouched.
        version=max(2,self.db.execute("PRAGMA user_version").fetchone()[0])
        self.db.executescript("BEGIN;\n"+SCHEMA+f"\nPRAGMA user_version={version};\nCOMMIT;")

    def input(self,notice_id):
        row=self.db.execute("SELECT * FROM notices WHERE id=?",(notice_id,)).fetchone()
        if row is None:raise ValueError("Notice does not exist")
        origins=tuple(sorted({r["college"]+" / "+str(r["column_name"] or "") for r in self.store.origins(notice_id)}))
        publisher=self.db.execute("SELECT name FROM units WHERE id=?",(row["content_source_id"],)).fetchone()
        content_source=publisher[0] if publisher else row["content_domain"]
        raw_attachments=[dict(r) for r in self.store.links(notice_id,"attachment")]
        raw_links=[dict(r) for r in self.store.links(notice_id,"body")]
        attachments=tuple(InputLink(sanitize(r["name"]),r["url"]) for r in raw_attachments)
        body_links=tuple(InputLink(sanitize(r["name"]),r["url"]) for r in raw_links)
        raw={"notice_id":notice_id,"content_hash":row["content_hash"],"title":row["title"],"content":row["content"],
             "canonical_url":row["canonical_url"],"sources":origins,"published_at":row["published_at"],
             "content_source":content_source,
             "attachments":raw_attachments,"body_links":raw_links,"sanitizer_version":"privacy-v2"}
        return AnalysisInput(notice_id,row["content_hash"],fingerprint(raw),sanitize(row["title"],remove_identity_lines=False),sanitize(row["content"]),
            row["canonical_url"],origins,row["published_at"],attachments,body_links,content_source)

    def ids(self):return [r[0] for r in self.db.execute("SELECT id FROM notices ORDER BY id")]

    def cached(self,notice,version):
        row=self.db.execute("SELECT payload FROM notice_analyses WHERE notice_id=? AND input_hash=? AND analyzer_version=?",
                            (notice.notice_id,notice.input_hash,version)).fetchone()
        return NoticeAnalysis.from_dict(json.loads(row[0])) if row else None

    def save(self,result):
        if result.review_status!="pending" or result.reviewed_at is not None or result.review_reason is not None:
            raise ValueError("Automatic analyzer cannot create human approval")
        with self.db:
            self.db.execute("""INSERT INTO notice_analyses
                (notice_id,content_hash,input_hash,analyzer_version,analyzed_at,model_name,payload) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(notice_id,input_hash,analyzer_version) DO UPDATE SET
                analyzed_at=excluded.analyzed_at,model_name=excluded.model_name,payload=excluded.payload""",
                (result.notice_id,result.content_hash,result.input_hash,result.analyzer_version,result.analyzed_at,result.model_name,
                 json.dumps(result.to_dict(),ensure_ascii=False)))

    def apply_review(self,result):
        row=self.db.execute("SELECT * FROM analysis_reviews WHERE notice_id=? AND input_hash=? ORDER BY id DESC LIMIT 1",
                            (result.notice_id,result.input_hash)).fetchone()
        return replace(result,review_status=row["status"],review_reason=row["reason"],reviewed_at=row["reviewed_at"]) if row else result

    def review(self,notice_id,status,reason,reviewer):
        choice(status,REVIEWS,"review status")
        if not reason.strip() or not reviewer.strip():raise ValueError("Review reason and reviewer are required")
        notice=self.input(notice_id)
        with self.db:
            self.db.execute("INSERT INTO analysis_reviews (notice_id,input_hash,status,reason,reviewer,reviewed_at) VALUES (?,?,?,?,?,?)",
                            (notice_id,notice.input_hash,status,reason,reviewer,utc_now()))
        return {"notice_id":notice_id,"input_hash":notice.input_hash,"review_status":status}

    def add_label(self,label):
        if not isinstance(label,ManualLabel):raise ValueError("Expected validated manual label")
        payload=json.dumps(asdict(label),ensure_ascii=False,sort_keys=True)
        previous=self.db.execute("SELECT payload FROM manual_labels WHERE notice_id=? AND input_hash=? AND label_origin=? ORDER BY id DESC LIMIT 1",
            (label.notice_id,label.input_hash,label.label_origin)).fetchone()
        if previous and previous[0]==payload:return False
        self.db.execute("INSERT INTO manual_labels (notice_id,content_hash,input_hash,label_origin,labeler,labeled_at,payload) VALUES (?,?,?,?,?,?,?)",
                        (label.notice_id,label.content_hash,label.input_hash,label.label_origin,label.labeler,utc_now(),payload))
        return True

    def latest_label(self,notice,origin="human"):
        row=self.db.execute("SELECT payload FROM manual_labels WHERE notice_id=? AND input_hash=? AND label_origin=? ORDER BY id DESC LIMIT 1",
                            (notice.notice_id,notice.input_hash,origin)).fetchone()
        return ManualLabel(**json.loads(row[0])) if row else None
