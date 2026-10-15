"""Local student-page preview of M4-A real parser outputs; no outside requests.

Uses a NEW private directory and an explicitly frozen, visibly labelled offline
reference time. Normal collection/serving continues to use the regular CLI.
"""
import argparse
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from scripts.validate_auto_publish import run
from cdu_radar.analysis_models import reference_datetime
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.rss_service import make_server


def preview(source, directory, as_of, port):
    run(source,directory,as_of)
    return serve(directory,as_of,port)


def serve(directory, as_of, port):
    """Resume an already prepared private replay; never fetch or change dates."""
    import json
    directory=Path(directory)
    report=json.loads((directory/'result.json').read_text('utf-8'))
    if report.get('mode')!='offline_stored_parser_output_replay' or report['reference_time']!=reference_datetime(as_of).isoformat():
        raise ValueError('Use a prepared offline replay with the same reference time')
    point=reference_datetime(as_of)
    controller=AutoFeedController(directory/'replay.sqlite3',directory/'page-state',
        clock=lambda:point,data_mode='offline_replay')
    server=None
    try:
        server=make_server(controller,'127.0.0.1',port)
        controller.start(10)
        print(f'Offline real-sample preview: http://127.0.0.1:{server.server_address[1]}/ (Ctrl+C to stop)',flush=True)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:return 0
    finally:
        if server:server.server_close()
        controller.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-db',required=True,type=Path)
    parser.add_argument('--directory',required=True,type=Path,help='NEW private preview directory')
    parser.add_argument('--as-of',required=True,help='Explicit offline reference time')
    parser.add_argument('--port',type=int,default=8766)
    args=parser.parse_args()
    raise SystemExit(preview(args.source_db,args.directory,args.as_of,args.port))
