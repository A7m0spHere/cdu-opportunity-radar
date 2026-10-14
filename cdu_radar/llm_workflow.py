"""M2-B append-only usage ledger, bounded execution and cache-only comparison."""
from contextlib import contextmanager
import sqlite3

from .analysis_models import reference_datetime
from .analysis_service import analyze_one, validate_output
from .analysis_store import AnalysisRepository
from .evaluation import evaluate
from .llm import LLMError
from .llm_schema import PROMPT_VERSION, SCHEMA_VERSION
from .rule_analysis import RuleAnalyzer, refresh_time
from .storage import Store


class LLMLedger:
    def __init__(self, repository):
        self.db = repository.db
        version = max(3, self.db.execute("PRAGMA user_version").fetchone()[0])
        self.db.executescript("""BEGIN;
            CREATE TABLE IF NOT EXISTS llm_requests (
              request_uid TEXT PRIMARY KEY, notice_id INTEGER NOT NULL REFERENCES notices(id),
              input_hash TEXT NOT NULL, analyzer_version TEXT NOT NULL, model TEXT NOT NULL,
              prompt_version TEXT NOT NULL, schema_version TEXT NOT NULL, started_at TEXT NOT NULL,
              attempt INTEGER NOT NULL, status_code INTEGER, error_category TEXT,
              prompt_tokens INTEGER, completion_tokens INTEGER, total_tokens INTEGER,
              cached_tokens INTEGER, estimated_cost REAL, currency TEXT, elapsed_ms INTEGER NOT NULL);
            """ + f"PRAGMA user_version={version}; COMMIT;")

    def record(self, notice, analyzer, event):
        usage = event.get("usage") or {}
        with self.db:
            self.db.execute("""INSERT INTO llm_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(request_uid) DO UPDATE SET status_code=excluded.status_code,
                  error_category=excluded.error_category,prompt_tokens=excluded.prompt_tokens,
                  completion_tokens=excluded.completion_tokens,total_tokens=excluded.total_tokens,
                  cached_tokens=excluded.cached_tokens,estimated_cost=excluded.estimated_cost,
                  currency=excluded.currency,elapsed_ms=excluded.elapsed_ms""",
                (event["request_uid"], notice.notice_id, notice.input_hash, analyzer.version, analyzer.model_name,
                 analyzer.prompt_version, analyzer.schema_version, event["started_at"], event["attempt"], event["status_code"],
                 event["error_category"], usage.get("prompt_tokens"), usage.get("completion_tokens"),
                 usage.get("total_tokens"), usage.get("cached_tokens"), event["estimated_cost"], event["currency"], event["elapsed_ms"]))


@contextmanager
def readonly_repository(path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    store = Store.__new__(Store); store.db = db
    repository = AnalysisRepository.__new__(AnalysisRepository); repository.store = store; repository.db = db
    try: yield repository
    finally: db.close()


def select_ids(repository, analyzer, *, notice_ids=None, limit=3, only_unanalyzed=True):
    if type(limit) is not int or not 0 < limit <= analyzer.config.batch_limit: raise ValueError("Limit exceeds configured batch limit")
    ids = repository.ids() if notice_ids is None else list(notice_ids)
    if len(ids) != len(set(ids)) or not set(ids) <= set(repository.ids()): raise ValueError("Unknown or duplicate notice id")
    if notice_ids is not None and len(ids) > limit: raise ValueError("Specified notice ids exceed limit")
    selected = []
    for notice_id in ids:
        if only_unanalyzed and notice_ids is None:
            notice = repository.input(notice_id)
            try:
                cached = repository.cached(notice, analyzer.version)
                if cached and cached.analysis_state == "success":
                    validate_output(notice, cached, analyzer); analyzer.validate_cached(notice, cached)
                    continue
            except (ValueError, TypeError): pass
        selected.append(notice_id)
        if len(selected) == limit: break
    return selected


def preview_batch(repository, analyzer, ids):
    output = []
    for notice_id in ids:
        notice = repository.input(notice_id)
        try:
            _, messages = analyzer.prepare(notice)
            output.append({"notice_id": notice_id, "messages": messages,
                           "input_chars": sum(len(m["content"]) for m in messages), "error_category": None})
        except LLMError as exc: output.append({"notice_id": notice_id, "messages": None, "error_category": exc.category})
    return {"dry_run": True, "api_requests": 0, "analyzer_version": analyzer.version,
            "model": analyzer.model_name, "previews": output}


def run_batch(repository, analyzer, ids, *, reference_time=None):
    if len(ids) > analyzer.config.batch_limit: raise ValueError("Batch limit exceeded")
    results = []; cache_hits = 0; stopped = False
    for notice_id in ids:
        result, cached = analyze_one(repository, notice_id, analyzer, reference_time)
        results.append(result); cache_hits += cached
        # Authentication/configuration cannot be fixed by spending requests on other notices.
        row = repository.db.execute("SELECT error_category FROM llm_requests WHERE notice_id=? AND analyzer_version=? ORDER BY started_at DESC,attempt DESC LIMIT 1",
                                    (notice_id, analyzer.version)).fetchone()
        if result.analysis_state == "failed" and (row and row[0] in {"authentication", "forbidden", "http_error"}):
            stopped = True; break
    return {"dry_run": False, "model": analyzer.model_name, "analyzer_version": analyzer.version,
            "selected": len(ids), "processed": len(results), "cache_hits": cache_hits,
            "successful": sum(r.analysis_state == "success" for r in results),
            "failed": sum(r.analysis_state == "failed" for r in results), "stopped_early": stopped,
            "unprocessed_ids": ids[len(results):], "analyses": [r.to_dict() for r in results]}


def request_totals(repository, analyzer, ids, *, all_versions=False):
    exists = repository.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_requests'").fetchone()
    query = "SELECT * FROM llm_requests" if all_versions else "SELECT * FROM llm_requests WHERE analyzer_version=?"
    params = () if all_versions else (analyzer.version,)
    rows = [] if not exists else [dict(r) for r in repository.db.execute(query, params) if r["notice_id"] in ids]
    known = [r for r in rows if r["total_tokens"] is not None]
    priced = [r for r in rows if r["estimated_cost"] is not None]
    currencies = {r["currency"] for r in priced}
    complete_cost = bool(rows) and len(priced) == len(rows) and len(currencies) == 1
    return {"api_requests": len(rows), "known_usage_requests": len(known), "usage_complete": len(known) == len(rows),
            "prompt_tokens_known": sum(r["prompt_tokens"] for r in known), "completion_tokens_known": sum(r["completion_tokens"] for r in known),
            "total_tokens_known": sum(r["total_tokens"] for r in known),
            "estimated_cost": sum(r["estimated_cost"] for r in priced) if complete_cost else None,
            "priced_requests": len(priced), "cost_currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "cost_note": "Configured token-rate estimate; not an invoice" if complete_cost else "Price/usage incomplete; unknown cost is not zero",
            "request_errors": {category: sum(r["error_category"] == category for r in rows)
                               for category in sorted({r["error_category"] for r in rows if r["error_category"]})}}


def compare(repository, analyzer, *, notice_ids=None, reference_time=None):
    ids = repository.ids() if notice_ids is None else list(notice_ids)
    def rules_reader(notice_id, point):
        notice = repository.input(notice_id)
        return RuleAnalyzer().analyze(notice, reference_time=point)
    def llm_reader(notice_id, point):
        notice = repository.input(notice_id)
        try:
            result = repository.cached(notice, analyzer.version)
            if result is None: return None
            if result.analysis_state == "success":
                validate_output(notice, result, analyzer); analyzer.validate_cached(notice, result)
            return refresh_time(result, point)
        except (ValueError, TypeError): return None
    rules = evaluate(repository, analyzer=RuleAnalyzer(), reference_time=reference_time,
                     notice_ids=ids, prediction_reader=rules_reader)
    llm = evaluate(repository, analyzer=analyzer, reference_time=reference_time,
                   notice_ids=ids, prediction_reader=llm_reader)
    rule_by_id = {r["notice_id"]: r for r in rules["predictions"]}
    corrected = []; regressed = []
    for prediction in llm["predictions"]:
        if prediction["state"] != "success": continue
        original = rule_by_id[prediction["notice_id"]]
        rule_positive = original["decision"] != "exclude"
        llm_positive = prediction["decision"] != "exclude"
        truth = prediction["truth"]
        if truth and not rule_positive and llm_positive: corrected.append(prediction["notice_id"])
        if rule_positive == truth and llm_positive != truth: regressed.append(prediction["notice_id"])
    return {"model": analyzer.model_name, "prompt_version": analyzer.prompt_version, "schema_version": analyzer.schema_version,
            "analyzer_version": analyzer.version, "comparison_complete": llm["metrics_complete"],
            "truth_definition": "human worth_recommending=true; retain+uncertain (including failed pending-review results) are retained candidates",
            "rules": rules, "llm": llm, "corrected_rule_false_negatives": corrected,
            "regressed_rule_correct_cases": regressed, "usage": request_totals(repository, analyzer, set(ids)),
            "usage_all_versions": request_totals(repository, analyzer, set(ids), all_versions=True),
            "limitations": ["36-label development comparison is not production acceptance; no prompt tuning on this evaluation set",
                            "Unset human dimension labels mean unmeasurable errors, not zero errors"]}
