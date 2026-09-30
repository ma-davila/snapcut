"""Local web app: this week's games (scores hidden) and one-click cut highlights."""
import argparse
import json
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import cuts, games, paths
from .autopilot import Autopilot
from .jobs import NETWORK_HINTS, Jobs

STATIC = Path(__file__).parent / "static"

# Where the "buy me a coffee" links point. Empty hides them.
DONATE_URL = os.environ.get("SNAPCUT_DONATE_URL", "https://github.com/sponsors/ma-davila")

# Cut finished games in the background; SNAPCUT_AUTO=0 turns it off.
AUTO = os.environ.get("SNAPCUT_AUTO", "1") != "0"

# Run from a checkout it keeps the old address when it's free; the packaged
# app takes any free port and tells the desktop shell which one.
DEFAULT_PORT = 8765 if paths.DEV else 0

jobs = Jobs()
autopilot = None


@asynccontextmanager
async def lifespan(app):
    global autopilot
    jobs.start()
    autopilot = Autopilot(jobs) if AUTO else None
    yield


app = FastAPI(title="snapcut", lifespan=lifespan)
# Only answer requests addressed to this machine (blocks DNS rebinding).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.get("/api/config")
def config():
    return {"donate_url": DONATE_URL or None, "auto": AUTO}


@app.get("/api/week")
def week(season: int | None = None, seasontype: int | None = None, week: int | None = None):
    try:
        data = games.fetch_week(season, seasontype, week)
    except Exception as e:
        raise HTTPException(502, f"No se pudo cargar el calendario de ESPN: {e}")
    try:
        videos = games.channel_videos()
    except Exception:
        videos = []
    published = cuts.week(data["season"], data["seasontype"], data["week"])
    for g in data["games"]:
        v = games.match_video(g, videos, data["week"]) if g["state"] == "post" else None
        g["video"] = v
        g["cuts"] = bool(v) and v["id"] in published
        g["job"] = jobs.status(g["id"])
        g["supported"] = (g["network"] or "").upper() in NETWORK_HINTS
    return data


class Generate(BaseModel):
    video_id: str
    network: str | None = None
    week: list[int] | None = None  # [season, seasontype, week]


@app.post("/api/games/{game_id}/generate")
def generate(game_id: str, body: Generate):
    return jobs.submit(game_id, body.video_id, body.network, body.week)


@app.get("/api/games/{game_id}/job")
def job(game_id: str):
    return jobs.status(game_id) or {"stage": None}


@app.get("/api/games/{game_id}/plays")
def plays(game_id: str):
    info = jobs.plays(game_id) if game_id.isdigit() else None
    if not info:
        raise HTTPException(404)
    return info


@app.get("/media/{game_id}.mp4")
def media(game_id: str):
    path = jobs.dir(game_id) / "cut.mp4"
    if not game_id.isdigit() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def _bind(port):
    for p in dict.fromkeys([port, 0]):
        sock = socket.socket()
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", p))
            return sock
        except OSError:
            sock.close()
    raise OSError("no free port")


def main():
    import uvicorn

    ap = argparse.ArgumentParser(description="Snapcut web app.")
    ap.add_argument("--port", type=int, default=int(os.environ.get("SNAPCUT_PORT", DEFAULT_PORT)),
                    help="preferred port; any free one if taken (0: any)")
    args = ap.parse_args()
    # The desktop shell reads this output through a pipe.
    sys.stdout.reconfigure(line_buffering=True)

    # One server per data folder. A second one points at the first and quits;
    # the desktop shell reads the same line.
    running = paths.DATA / "server.json"
    lock = paths.lock(paths.DATA / "server.lock")
    if not lock:
        try:
            print(f"Snapcut: {json.loads(running.read_text())['url']}", flush=True)
            return 0
        except (OSError, ValueError, KeyError):
            print("Snapcut is already starting", file=sys.stderr)
            return 1

    sock = _bind(args.port)
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    running.write_text(json.dumps({"url": url, "pid": os.getpid()}))
    print(f"Snapcut: {url}", flush=True)
    try:
        uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[sock])
    finally:
        running.unlink(missing_ok=True)
