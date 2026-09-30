"""User settings that survive restarts, in <data>/settings.json."""
import json
import os
import threading

from .paths import DATA

FILE = DATA / "settings.json"
DEFAULTS = {"auto": True}
# Environment overrides (for development): SNAPCUT_AUTO=0 keeps background cutting off.
FORCED = {"auto": os.environ["SNAPCUT_AUTO"] != "0"} if "SNAPCUT_AUTO" in os.environ else {}

_lock = threading.Lock()


def _saved():
    try:
        return json.loads(FILE.read_text())
    except (OSError, ValueError):
        return {}


def load():
    saved = _saved()
    return {**{k: saved.get(k, v) for k, v in DEFAULTS.items()}, **FORCED}


def update(**changes):
    with _lock:
        saved = _saved()
        saved.update({k: v for k, v in changes.items() if k in DEFAULTS})
        FILE.parent.mkdir(parents=True, exist_ok=True)
        FILE.write_text(json.dumps(saved))
        return load()
