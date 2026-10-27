"""Supervised classification rounds with bounded recovery and private health."""
from datetime import datetime, timezone
import logging
import time

LOG=logging.getLogger(__name__)
AUTH_ERRORS={'authentication','forbidden','missing_api_key'}

def supervise(operation, stop, publish, *, clock=time.time, wait=None, delays=(60,300,900)):
    wait=wait or stop.wait
    health=dict(state='starting',rounds=0,handled=0,failures=0,failed_tasks=0,last_errors=[],last_success_at=None,last_progress_at=None,reason=None,next_retry_at=None)
    strikes=0;blocked=False
    def report():
        health['heartbeat_at']=datetime.fromtimestamp(clock(),timezone.utc).isoformat()
        try:publish(dict(health))
        except (OSError,ValueError,TypeError):LOG.warning('Classification health report unavailable')
    report()
    try:
        while not stop.is_set():
            if blocked:
                report()
                if wait(30):break
                continue
            health.update(state='running',next_retry_at=None);report()
            try:
                result=operation();handled=result.get('handled',[])
                health['rounds']+=1;health['handled']+=len(handled)
                if handled:health['last_progress_at']=clock()
                if any(r.get('committed') and r.get('state') in {'succeeded','partial'} for r in handled):health['last_success_at']=clock()
                errors={r.get('error') for r in handled if r.get('error')}
                failed=[r for r in handled if r.get('error')]
                health['failed_tasks']+=len(failed)
                if failed:
                    safe={'validation_stage','error_detail','schema_error_path','missing_schema_fields','finish_reason'}
                    health['last_errors']=[dict(error=r['error'],diagnostic={k:v for k,v in r.get('diagnostic',{}).items() if k in safe}) for r in failed[-3:]]
                if errors&AUTH_ERRORS:
                    blocked=True;health.update(state='blocked',reason=sorted(errors&AUTH_ERRORS)[0]);report();continue
                reason=result.get('stop_reason') or ('http_error' if 'http_error' in errors else None)
                if reason:
                    strikes+=1;delay=delays[min(strikes-1,len(delays)-1)]
                    health['failures']+=1;health.update(state='backoff',reason=reason,next_retry_at=clock()+delay)
                    LOG.warning('Classification recovering after %s; retry in %s seconds',reason,delay)
                else:
                    strikes=0;delay=60;health.update(state='idle',reason=None,next_retry_at=clock()+delay)
            except Exception as exc:
                strikes+=1;delay=delays[min(strikes-1,len(delays)-1)]
                health['failures']+=1;health.update(state='backoff',reason=type(exc).__name__,next_retry_at=clock()+delay)
                LOG.warning('Classification round failed (%s); recovery scheduled',type(exc).__name__)
            report()
            if wait(delay):break
    finally:
        health.update(state='stopped',next_retry_at=None);report()
