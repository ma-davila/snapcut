"""yt-dlp with Snapcut's ffmpeg and JavaScript runtime."""
import yt_dlp

from . import tools
from .paths import DATA


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
