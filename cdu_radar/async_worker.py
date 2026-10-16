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
        analyzer=AsyncAnalyzer(LLMConfig(**config),mock=mock,prescreen=prescreen)
        def event_sink(event):
            # Filter before IPC, too: full provider diagnostics never leave child.
            sender.send(('usage',{'usage':event.get('usage'),'status_code':event.get('status_code')}))
        result=analyzer.analyze(notice,reference_time,event_sink)
        sender.send(('result',result))
    except BaseException as exc:
        category=exc.category if isinstance(exc,LLMError) and exc.category in ERRORS else 'internal_error'
        sender.send(('error',category))
    finally:sender.close()


def isolated_run(analyzer, notice, reference_time, on_event, *, target=_child):
    # spawn avoids inheriting a database/socket, including on Unix. The deadline
    # covers interpreter startup, transport, parsing and evidence validation.
    context=multiprocessing.get_context('spawn')
    reader,sender=context.Pipe(duplex=False)
    process=context.Process(target=target,args=(sender,asdict(analyzer.config),analyzer.mock,
        analyzer.prescreen,notice,reference_time))
    deadline=time.monotonic()+analyzer.config.timeout
    try:
        process.start();sender.close()
        while time.monotonic()<deadline:
            if reader.poll(min(0.1,max(0,deadline-time.monotonic()))):
                try:kind,value=reader.recv()
                except EOFError:break
                if kind=='usage':on_event(value)
                elif kind=='result':return value,None
                elif kind=='error':return None,value
            elif not process.is_alive():break
        return None,'timeout' if process.is_alive() else 'worker_lost'
    finally:
        if process.pid is not None:
            if process.is_alive():process.terminate()
            process.join(timeout=2)
            if process.is_alive():process.kill();process.join(timeout=2)
        reader.close();sender.close()


def run_round(queue, analyzer, *, reference_time, runner=isolated_run):
    if analyzer.config.timeout+5>=queue.policy.lease_seconds:
        raise ValueError('Lease must exceed total task timeout by more than 5 seconds')
    scan=queue.scan(analyzer);handled=[]
    for _ in range(queue.policy.batch_limit):
        claim=queue.claim(analyzer)
        if claim is None:break
        claim['config']=analyzer.config
        try:
            result,error=runner(analyzer,claim['notice'],reference_time,lambda e:queue.record_event(claim,e))
        except Exception:
            result,error=None,'internal_error'
        committed=queue.finish(claim,result=result,error=error)
        handled.append({'job_id':claim['id'],'attempt':claim['attempts'],'committed':committed,
                        'error':error,'state':result['state'] if result else None})
        # A credential/configuration failure cannot be repaired by spending more.
        if error in {'authentication','forbidden','missing_api_key','http_error'}:break
    return {'scan':scan,'handled':handled,'mode':'offline_mock' if analyzer.mock else 'provider'}
