"""Where Snapcut keeps its files.

Run from a checkout (`uv run snapcut`) everything stays in the repo's data/,
as before. The packaged app uses the user's data folder instead:
~/Library/Application Support/Snapcut on macOS, %LOCALAPPDATA%\\Snapcut on
Windows. SNAPCUT_DATA overrides both.
"""
import os
import sys
from pathlib import Path

APP = "Snapcut"
FROZEN = getattr(sys, "frozen", False)
REPO = Path(__file__).resolve().parent.parent
DEV = not FROZEN and (REPO / "pyproject.toml").exists()


def user_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP.lower()


DATA = Path(os.environ["SNAPCUT_DATA"]).expanduser() if os.environ.get("SNAPCUT_DATA") else (
    REPO / "data" if DEV else user_dir())
LOGS = Path.home() / "Library" / "Logs" / APP if sys.platform == "darwin" else DATA / "logs"


def lock(path):
    """Take an exclusive lock on `path` for as long as the returned file stays
    open. None when another process holds it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f
