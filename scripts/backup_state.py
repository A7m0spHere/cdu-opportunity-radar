"""Offline backup/restore preparation. Never connects to a server or starts one."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cdu_radar.backup_state import backup, restore


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    save = actions.add_parser('backup')
    save.add_argument('--raw-db', type=Path, required=True)
    save.add_argument('--state-dir', type=Path, required=True)
    save.add_argument('--analysis-db', type=Path)
    save.add_argument('--output', type=Path, required=True)
    save.add_argument('--revision')
    save.add_argument('--quiesced', action='store_true', help='Confirm all collection/model/publisher writers are stopped')
    load = actions.add_parser('restore')
    load.add_argument('--bundle', type=Path, required=True)
    load.add_argument('--output', type=Path, required=True, help='NEW directory only')
    args = parser.parse_args(argv)
    try:
        if args.action == 'backup':
            result = backup(args.raw_db, args.state_dir, args.output, analysis_db=args.analysis_db,
                            revision=args.revision, quiesced=args.quiesced)
        else:
            result = restore(args.bundle, args.output)
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        print('Offline backup operation refused; verify stopped writers, paths, schema and checksums.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
