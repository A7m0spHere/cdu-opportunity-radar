"""Standalone worker with a killable one-request subprocess per lease.

The child receives only a sanitized input and transport config; it has no queue
or raw database handle. No API call is ever made by scan/status/ledger or HTTP.
"""
from dataclasses import asdict
import multiprocessing
import time

from .async_contract import AsyncAnalyzer
from .async_queue import ERRORS
from .llm import LLMConfig, LLMError


def _child(sender, config, mock, prescreen, notice, reference_time):
    try:
        from .review_contract import ReviewAnalyzer
        from .automatic_review import AutomaticReviewAnalyzer
        cfg=LLMConfig(**config)
        kind=AutomaticReviewAnalyzer if cfg.analysis_mode=='automatic_review_v3' else ReviewAnalyzer if cfg.analysis_mode=='classification_review_v2' else AsyncAnalyzer
        analyzer=kind(cfg,mock=mock,prescreen=prescreen)
        def event_sink(event):
            # Filter before IPC, too: full provider diagnostics never leave child.
            allowed={'validation_stage','error_detail','schema_error_path','missing_schema_fields','finish_reason',
                     'json_error_detail','content_chars','reasoning_chars','request_format','parse_method'}
            diagnostic={key:value for key,value in event.get('diagnostic',{}).items() if key in allowed}
            sender.send(('usage',{'usage':event.get('usage'),'status_code':event.get('status_code'),'diagnostic':diagnostic}))
        result=analyzer.analyze(notice,reference_time,event_sink)
        sender.send(('result',result))
    except BaseException as exc:
        category=exc.category if isinstance(exc,LLMError) and exc.category in ERRORS else 'internal_error'
        sender.send(('error',category))
    finally:sender.close()


def isolated_run(analyzer, notice, reference_time, on_event, *, target=_child, stop_event=None):
    # spawn avoids inheriting a database/socket, including on Unix. The deadline
    # covers interpreter startup, transport, parsing and evidence validation.
    context=multiprocessing.get_context('spawn')
    reader,sender=context.Pipe(duplex=False)
    process=context.Process(target=target,args=(sender,asdict(analyzer.config),analyzer.mock,
        analyzer.prescreen,notice,reference_time))
    deadline=time.monotonic()+analyzer.config.timeout
    try:
        process.start();sender.close()
        while time.monotonic()<deadline and not (stop_event is not None and stop_event.is_set()):
            if reader.poll(min(0.1,max(0,deadline-time.monotonic()))):
                try:kind,value=reader.recv()
                except EOFError:break
                if kind=='usage':on_event(value)
                elif kind=='result':return value,None
                elif kind=='error':return None,value
            elif not process.is_alive():break
        return None,'worker_lost' if stop_event is not None and stop_event.is_set() else 'timeout' if process.is_alive() else 'worker_lost'
    finally:
        if process.pid is not None:
            if process.is_alive():process.terminate()
            process.join(timeout=2)
            if process.is_alive():process.kill();process.join(timeout=2)
        reader.close();sender.close()


def run_round(queue, analyzer, *, reference_time, runner=isolated_run, notice_ids=None):
    if analyzer.config.timeout+5>=queue.policy.lease_seconds:
        raise ValueError('Lease must exceed total task timeout by more than 5 seconds')
    scan=queue.scan(analyzer,notice_ids=notice_ids);handled=[];format_failures=0;stop_reason=None
    for _ in range(queue.policy.batch_limit):
        claim=queue.claim(analyzer,notice_ids=notice_ids)
        if claim is None:break
        claim['config']=analyzer.config
        diagnostic={}
        def event_sink(event):
            queue.record_event(claim,event)
            diagnostic.update(event.get('diagnostic',{}))
        try:
            result,error=runner(analyzer,claim['notice'],reference_time,event_sink)
        except Exception:
            result,error=None,'internal_error'
        committed=queue.finish(claim,result=result,error=error)
        handled.append({'job_id':claim['id'],'attempt':claim['attempts'],'committed':committed,
                        'error':error,'state':result['state'] if result else None,
                        **({'diagnostic':diagnostic} if error and diagnostic else {})})
        format_failures=format_failures+1 if error in {'invalid_json','invalid_api_json','schema_validation','no_final_text','incomplete_output'} else 0
        if format_failures>=analyzer.config.stop_after_format_failures:
            stop_reason='consecutive_format_failures';break
        # A credential/configuration failure cannot be repaired by spending more.
        if error in {'authentication','forbidden','missing_api_key','http_error'}:break
    return {'scan':scan,'handled':handled,'mode':'offline_mock' if analyzer.mock else 'provider',
            **({'stop_reason':stop_reason} if stop_reason else {})}
