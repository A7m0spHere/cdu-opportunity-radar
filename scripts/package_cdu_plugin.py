"""Build a reviewable source ZIP only; no install, upload or AstrBot import."""
import argparse
import ast
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ['__init__.py','main.py','engine.py','metadata.yaml','_conf_schema.json','README.md']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/astrbot_plugin_cdu_radar-0.1.0.zip')
    args = parser.parse_args()
    folder = ROOT/'astrbot_plugin_cdu_radar'
    for name in FILES:
        path = folder/name
        if not path.is_file() or path.is_symlink():
            raise ValueError('Expected regular source file: '+name)
        if name.endswith('.py'):
            ast.parse(path.read_text('utf-8'))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation protects older review/release artifacts.
    with zipfile.ZipFile(args.output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in FILES:
            archive.write(folder/name,name)
    print('Source ZIP:',args.output.resolve())
    print('SHA-256:',hashlib.sha256(args.output.read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
