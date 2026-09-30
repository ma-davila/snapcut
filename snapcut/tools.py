"""Helper programs: ffmpeg, ffprobe and a JavaScript runtime for yt-dlp.

Looked up in SNAPCUT_BIN (a list of folders, set by the desktop app), then
next to the packaged server, then on the PATH.
"""
import functools
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import FROZEN

EXE = ".exe" if sys.platform == "win32" else ""
# A packaged server runs without a console on Windows: don't flash one per call.
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}

# H.264 encoders to try, best first. Hardware ones only work with the right
# GPU and driver, so each is checked with a short test encode.
ENCODERS = {
    "darwin": ["h264_videotoolbox"],
    "win32": ["h264_nvenc", "h264_qsv", "h264_amf"],
}.get(sys.platform, ["h264_nvenc"]) + ["libx264"]
ENCODER_ARGS = {"libx264": ["-preset", "veryfast", "-pix_fmt", "yuv420p"]}
BITRATE = "3M"

# yt-dlp's names for the runtimes it can use to solve YouTube's challenges.
JS_RUNTIMES = {"deno": "deno", "node": "node", "quickjs": "qjs"}


def bin_dirs():
    dirs = [Path(d) for d in os.environ.get("SNAPCUT_BIN", "").split(os.pathsep) if d]
    if FROZEN:
        dirs += [Path(getattr(sys, "_MEIPASS", "")) / "bin", Path(sys.executable).parent / "bin"]
    return dirs


@functools.cache
def which(name):
    for d in bin_dirs():
        if (d / (name + EXE)).is_file():
            return str(d / (name + EXE))
    return shutil.which(name)


def need(name):
    path = which(name)
    if not path:
        raise RuntimeError(f"No encuentro {name}.")
    return path


def ffmpeg():
    return need("ffmpeg")


def ffprobe():
    return need("ffprobe")


def run(cmd, **kw):
    return subprocess.run(cmd, **NO_WINDOW, **kw)


def popen(cmd, **kw):
    return subprocess.Popen(cmd, **NO_WINDOW, **kw)


def _encodes(name):
    try:
        return run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "testsrc2=size=640x360:rate=30:duration=0.5",
                    "-c:v", name, "-b:v", BITRATE, *ENCODER_ARGS.get(name, []), "-f", "null", "-"],
                   capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@functools.cache
def encoder():
    """The best H.264 encoder that works here. SNAPCUT_ENCODER forces one."""
    for name in [os.environ["SNAPCUT_ENCODER"]] if os.environ.get("SNAPCUT_ENCODER") else ENCODERS:
        if _encodes(name):
            print(f"encoder: {name}")
            return name
    raise RuntimeError("ffmpeg no puede codificar H.264 en este equipo.")


def encoder_args():
    name = encoder()
    return ["-c:v", name, "-b:v", BITRATE, *ENCODER_ARGS.get(name, [])]


@functools.cache
def js_runtime():
    """yt-dlp's js_runtimes option: the first runtime found, deno preferred."""
    for key, exe in JS_RUNTIMES.items():
        if path := which(exe):
            return {key: {"path": path}}
    return None
