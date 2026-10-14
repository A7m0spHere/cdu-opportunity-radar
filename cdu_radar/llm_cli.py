"""Thin CLI adapter, preserving M0/M1/M2-A commands."""
import json
import sqlite3
import sys

from .analysis_models import reference_datetime
from .analysis_store import AnalysisRepository
from .labels import atomic_text
from .llm import LLMAnalyzer, LLMConfig
from .llm_workflow import (LLMLedger, compare, preview_batch, readonly_repository,
                           run_batch, select_ids)
from .storage import Store


def llm_command(args):
    try:
        if not args.db.is_file(): raise ValueError("Database does not exist; collect/import labels first")
        outputs = [args.output] + ([args.errors_output] if getattr(args, "errors_output", None) else [])
        if len({p.resolve() for p in outputs}) != len(outputs): raise ValueError("Report paths must differ")
        for path in outputs:
            if path.resolve() == args.db.resolve() or path.suffix.lower() != ".json": raise ValueError("LLM report must be a separate JSON file")
            if path.exists() and not args.overwrite: raise ValueError("Report exists; choose another output or --overwrite")
        config = LLMConfig.from_env(); analyzer = LLMAnalyzer(config)
        point = reference_datetime(args.as_of)
        if args.llm_action == "compare":
            with readonly_repository(args.db) as repository:
                report = compare(repository, analyzer, notice_ids=args.notice_id,
                                 reference_time=point if args.as_of else None)
            atomic_text(args.output, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if args.errors_output:
                errors = {"rules": report["rules"]["error_samples"], "llm": report["llm"]["error_samples"],
                          "corrected_rule_false_negatives": report["corrected_rule_false_negatives"],
                          "regressed_rule_correct_cases": report["regressed_rule_correct_cases"]}
                atomic_text(args.errors_output, json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"output": str(args.output), "comparison_complete": report["comparison_complete"],
                "rules": {k: report["rules"][k] for k in ["valid_labels", "precision", "recall", "f1", "confusion_matrix"]},
                "llm": {k: report["llm"][k] for k in ["valid_labels", "precision", "recall", "f1", "confusion_matrix", "failed_count", "not_analyzed_count"]},
                "usage": report["usage"]}, ensure_ascii=False, indent=2))
            return 0 if report["comparison_complete"] and not report["llm"]["failed_count"] else 1
        limit = args.limit or config.batch_limit
        if not args.send:
            with readonly_repository(args.db) as repository:
                ids = select_ids(repository, analyzer, notice_ids=args.notice_id, limit=limit)
                report = preview_batch(repository, analyzer, ids)
        else:
            # Validate key/config before creating tables or touching cached states.
            import os
            if not os.environ.get(config.key_env, "").strip(): raise ValueError("Configured API key environment variable is empty")
            with Store(args.db) as store:
                repository = AnalysisRepository(store); ledger = LLMLedger(repository)
                analyzer.event_sink = ledger.record
                ids = select_ids(repository, analyzer, notice_ids=args.notice_id, limit=limit)
                report = run_batch(repository, analyzer, ids, reference_time=point)
        atomic_text(args.output, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({key: value for key, value in report.items() if key not in {"previews", "analyses"}}, ensure_ascii=False, indent=2))
        return 1 if report.get("failed") or any(p["error_category"] for p in report.get("previews", [])) else 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        # Our exceptions contain category/config names only, never raw provider text.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
