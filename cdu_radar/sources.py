import json
from importlib.resources import files

from .models import Source


def load_sources() -> list[Source]:
    rows = json.loads(files("cdu_radar").joinpath("sources.json").read_text(encoding="utf-8"))
    return [Source(**row) for row in rows]
