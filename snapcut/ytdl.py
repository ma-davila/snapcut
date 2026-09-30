"""yt-dlp with Snapcut's ffmpeg and JavaScript runtime.

YouTube changes often enough that a yt-dlp a few weeks old stops working, so
the packaged app keeps newer official releases in <data>/yt-dlp (see
ytupdate) and loads the newest of those and the bundled copy.
"""
import importlib.metadata
import re
import sys
import zipfile

from . import tools
from .paths import DATA

UPDATES = DATA / "yt-dlp"


def version_key(version):
    """2026.08.19 < 2026.08.19.123456 (nightly) < 2026.09.01."""
    return tuple(int(p) for p in re.findall(r"\d+", version or "0"))


def zip_version(path):
    """Version of an official yt-dlp zipimport release, or None if it isn't one."""
    try:
        with zipfile.ZipFile(path) as z:
            text = z.read("yt_dlp/version.py").decode()
            if "yt_dlp_ejs/__init__.py" not in z.namelist():
                return None
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    m = re.search(r"^__version__ = '([^']+)'", text, re.M)
    return m and m.group(1)


def _downloaded():
    found = [(zip_version(p), p) for p in UPDATES.glob("yt-dlp-*.zip")]
    return max(((v, p) for v, p in found if v), key=lambda vp: version_key(vp[0]), default=(None, None))


def _load():
    bundled = importlib.metadata.version("yt-dlp")
    version, path = _downloaded()
    if version and version_key(version) > version_key(bundled):
        sys.path.insert(0, str(path))
        try:
            import yt_dlp
            return yt_dlp, "downloaded"
        except Exception as e:  # a broken download must not take the app down
            print(f"yt-dlp {version} from {path.name} doesn't load ({e}); using the bundled one")
            sys.path.remove(str(path))
            for name in [m for m in sys.modules if m.split(".")[0] in ("yt_dlp", "yt_dlp_ejs")]:
                del sys.modules[name]
            path.rename(path.with_suffix(".bad"))
    import yt_dlp
    return yt_dlp, "bundled"


yt_dlp, SOURCE = _load()
VERSION = yt_dlp.version.__version__


def base_opts():
    # Keep yt-dlp's cache (solved YouTube challenges) with the app's data.
    opts = {"cachedir": str(DATA / "cache" / "yt-dlp")}
    if ffmpeg := tools.which("ffmpeg"):
        opts["ffmpeg_location"] = ffmpeg
    if js := tools.js_runtime():
        opts["js_runtimes"] = js
    return opts


def YoutubeDL(opts):
    return yt_dlp.YoutubeDL({**base_opts(), **opts})
