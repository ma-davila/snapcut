"""yt-dlp with Snapcut's ffmpeg and JavaScript runtime."""
import yt_dlp

from . import tools


def base_opts():
    opts = {}
    if ffmpeg := tools.which("ffmpeg"):
        opts["ffmpeg_location"] = ffmpeg
    if js := tools.js_runtime():
        opts["js_runtimes"] = js
    return opts


def YoutubeDL(opts):
    return yt_dlp.YoutubeDL({**base_opts(), **opts})
