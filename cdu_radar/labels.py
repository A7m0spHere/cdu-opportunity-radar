"""CSV worksheet with distinct manual fields, immutable snapshot identities."""
import csv
from dataclasses import asdict
import io
import os
from pathlib import Path
import tempfile

from .analysis_models import ManualLabel,reference_datetime
from .analysis_service import analyze_one
from .privacy import spreadsheet_text
from .rule_analysis import RuleAnalyzer

MANUAL=["worth_recommending","student_opportunity","relevance_level","ai_use_policy","eligibility_status","deadline_explicit",
        "registration_deadline","internal_deadline","opportunity_status","category","needs_further_check","notes"]
BOOLS={"worth_recommending","student_opportunity","deadline_explicit","needs_further_check"}
COLUMNS=["notice_id","content_hash","input_hash","canonical_url","title","sources","content_source","published_at","content_excerpt","attachment_count",
         "reference_date","reference_time","predicted_reference_time","predicted_analyzer_version","predicted_decision","predicted_category","predicted_relevance","predicted_eligibility","predicted_ai_policy",
         "predicted_status","label_status",*MANUAL]


def atomic_text(path,text,encoding="utf-8-sig"):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);temp=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False,mode="w",encoding=encoding,newline="") as handle:
            temp=handle.name;handle.write(text)
        os.replace(temp,path)
    finally:
        if temp and os.path.exists(temp):os.unlink(temp)


def export_labels(repository,path,*,analyzer=None,reference_time=None):
    analyzer=analyzer or RuleAnalyzer();now=reference_datetime(reference_time)
    output=io.StringIO(newline="");writer=csv.DictWriter(output,fieldnames=COLUMNS);writer.writeheader()
    for notice_id in repository.ids():
        notice=repository.input(notice_id);prediction,_=analyze_one(repository,notice_id,analyzer,now)
        label=repository.latest_label(notice)
        row={"notice_id":notice_id,"content_hash":notice.content_hash,"input_hash":notice.input_hash,"canonical_url":notice.canonical_url,
            "title":spreadsheet_text(notice.title),"sources":spreadsheet_text("；".join(notice.sources)),"published_at":notice.published_at or "",
            "content_source":spreadsheet_text(notice.content_source or "待核实"),
            "content_excerpt":spreadsheet_text(notice.content[:800]),"attachment_count":len(notice.attachments),"reference_date":now.date().isoformat(),
            "reference_time":now.isoformat(),
            "predicted_reference_time":prediction.reference_time,"predicted_analyzer_version":prediction.analyzer_version,
            "predicted_decision":prediction.candidate_decision,"predicted_category":prediction.category,"predicted_relevance":prediction.relevance_level,
            "predicted_eligibility":prediction.eligibility_status,"predicted_ai_policy":prediction.ai_use_policy,"predicted_status":prediction.opportunity_status,
            "label_status":"human" if label else "unlabeled"}
        for field in MANUAL:
            value=getattr(label,field) if label else None
            row[field]="" if value is None else str(value).lower() if isinstance(value,bool) else spreadsheet_text(value)
        if label and label.reference_date:row["reference_date"]=label.reference_date
        if label:row["reference_time"]=label.reference_time or ""
        writer.writerow(row)
    atomic_text(path,output.getvalue())
    return len(repository.ids())


def import_labels(repository,path,*,labeler,allow_synthetic=False):
    parsed=[];skipped=0;seen=set()
    with Path(path).open(encoding="utf-8-sig",newline="") as handle:
        reader=csv.DictReader(handle)
        if reader.fieldnames!=COLUMNS:raise ValueError("Label CSV columns must match the exported worksheet")
        for number,row in enumerate(reader,start=2):
            try:
                if None in row or any(v is None for v in row.values()):raise ValueError("Malformed CSV row")
                status=row["label_status"].strip()
                if status=="unlabeled":
                    if any(row[k].strip() for k in MANUAL):raise ValueError("Manual values require explicit label_status=human")
                    skipped+=1;continue
                if status not in {"human","synthetic"} or status=="synthetic" and not allow_synthetic:raise ValueError("Predictions cannot be imported as human truth")
                try:notice_id=int(row["notice_id"])
                except ValueError as exc:raise ValueError("Invalid notice id") from exc
                if notice_id in seen:raise ValueError("Duplicate notice id in label worksheet")
                seen.add(notice_id);notice=repository.input(notice_id)
                if (row["content_hash"],row["input_hash"],row["canonical_url"])!=(notice.content_hash,notice.input_hash,notice.canonical_url):
                    raise ValueError("Stale or mismatched notice snapshot; export a new worksheet")
                values={}
                for field in MANUAL:
                    value=row[field].strip()
                    if field in BOOLS:
                        value=value.lower()
                        if value not in {"","true","false"}:raise ValueError(f"{field} expects true/false or blank")
                        values[field]=None if value=="" else value=="true"
                    elif field=="notes":values[field]=value.removeprefix("'")
                    else:values[field]=value or None
                parsed.append(ManualLabel(notice_id,notice.content_hash,notice.input_hash,status,labeler,reference_date=row["reference_date"],reference_time=row["reference_time"] or None,**values))
            except (ValueError,TypeError) as exc:
                # Include only row number and schema error, not worksheet text/notes.
                raise ValueError(f"Label row {number}: {exc}") from exc
    inserted=0
    with repository.db:
        repository.db.execute("BEGIN IMMEDIATE")
        for label in parsed:
            if repository.input(label.notice_id).input_hash!=label.input_hash:raise ValueError("Notice changed during label import")
            inserted+=repository.add_label(label)
    return {"imported":inserted,"unchanged":len(parsed)-inserted,"unlabeled_skipped":skipped}
