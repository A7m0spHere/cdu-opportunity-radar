from pathlib import Path
import socket

import pytest

from cdu_radar.sources import load_sources
from cdu_radar.storage import Store


@pytest.fixture(autouse=True)
def forbid_real_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("pytest must not connect to the network")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.fixture
def sample():
    return lambda name: (Path(__file__).parent / "fixtures" / name).read_bytes()


@pytest.fixture
def sources():
    return [s for s in load_sources() if s.id in {"computer", "business"}]


@pytest.fixture
def store(tmp_path, sources):
    with Store(tmp_path / "test.sqlite3") as instance:
        instance.sync_sources(sources)
        yield instance
