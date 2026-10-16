"""Run with python -I after installing the wheel; no network or live data."""
from importlib.resources import files
from pathlib import Path
import os
import subprocess
import sys
import sysconfig
import tempfile

import cdu_radar
from cdu_radar.sources import load_sources
from cdu_radar.student_page import assets


def main():
    source = Path(__file__).resolve().parents[1] / 'cdu_radar'
    if Path(cdu_radar.__file__).resolve().is_relative_to(source):
        raise AssertionError('Smoke test must import the installed wheel')
    assert all(body for body, _ in assets().values())
    for name in ('sources.json','units.json','rules.json'):
        assert files('cdu_radar').joinpath(name).read_bytes()
    assert load_sources()
    with tempfile.TemporaryDirectory() as directory:
        for command in ([], ['serve-rss']):
            result = subprocess.run([sys.executable,'-I','-m','cdu_radar',*command,'--help'],
                                    cwd=directory,check=True,capture_output=True,text=True)
            assert 'serve-rss' in result.stdout
            if command:
                assert '--recheck-limit' in result.stdout and '--request-budget' in result.stdout
        executable = Path(sysconfig.get_path('scripts')) / ('cdu-radar.exe' if os.name=='nt' else 'cdu-radar')
        subprocess.run([str(executable),'--help'],cwd=directory,check=True,capture_output=True)
    print('Installed wheel: CLI, catalogs and all web assets passed; no collection or model calls.')


if __name__=='__main__':
    main()
