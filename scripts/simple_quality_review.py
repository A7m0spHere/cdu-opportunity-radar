"""Create an offline simple review page or convert the user's exported answers."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cdu_radar.simple_review import build_page, convert_answers


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    for name in ('page', 'convert'):
        command = actions.add_parser(name)
        command.add_argument('--directory', type=Path, required=True)
        command.add_argument('--output', type=Path, required=True)
        if name == 'page':
            command.add_argument('--batch-size', type=int, default=10)
            command.add_argument('--synthetic', action='store_true', help='Software testing only; exports synthetic labels')
        else:
            command.add_argument('--input', type=Path, required=True)
            command.add_argument('--allow-synthetic', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = (build_page(args.directory, args.output, batch_size=args.batch_size, synthetic=args.synthetic)
                  if args.action == 'page' else convert_answers(args.directory, args.input, args.output,
                                                              allow_synthetic=args.allow_synthetic))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, KeyError, OSError):
        print('Review file is invalid, belongs to another package, or the output already exists. No source or label database was changed.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
