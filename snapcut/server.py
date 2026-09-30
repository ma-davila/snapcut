"""Local web app: this week's games (scores hidden) and one-click cut highlights."""
import argparse
import importlib.metadata
import json
import os
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import cuts, games, paths, settings, ytdl, ytupdate
from .autopilot import Autopilot
from .jobs import NETWORK_HINTS, Jobs

STATIC = Path(__file__).parent / "static"

# Where the "buy me a coffee" links point. Empty hides them.
DONATE_URL = os.environ.get("SNAPCUT_DONATE_URL", "https://github.com/sponsors/ma-davila")

# Shipped inside the packaged app; in a checkout, after desktop/scripts/build-server.sh.
LICENSES = [Path(getattr(sys, "_MEIPASS", "")) / "THIRD_PARTY_LICENSES.txt",
            paths.REPO / "desktop" / "build" / "THIRD_PARTY_LICENSES.txt"]

# Exit status asking the desktop app to start the server again (a newer yt-dlp).
RESTART = 75

try:
    VERSION = importlib.metadata.version("snapcut")
except importlib.metadata.PackageNotFoundError:
    VERSION = None

# Run from a checkout it keeps the old address when it's free; the packaged
# app takes any free port and tells the desktop shell which one. Either way
# it tries the last one first: the page keeps what's been revealed in
# localStorage, which belongs to the address.
LAST_PORT = paths.DATA / "port"

jobs = Jobs()
autopilot = None
uvicorn_server = None
exit_code = 0
under_shell = False


def restart_when_idle(version):
    if not under_shell:
        print(f"restart Snapcut to use yt-dlp {version}")
        return

    def wait():
        global exit_code
        while jobs.active():
            time.sleep(30)
        print(f"restarting for yt-dlp {version}")
        exit_code = RESTART
        uvicorn_server.should_exit = True

    threading.Thread(target=wait, daemon=True).start()


@asynccontextmanager
async def lifespan(app):
    global autopilot
    jobs.start()
    autopilot = Autopilot(jobs)
    if ytupdate.ENABLED:
        ytupdate.Updater(on_new=restart_when_idle)
    yield


app = FastAPI(title="snapcut", lifespan=lifespan)
# Only answer requests addressed to this machine (blocks DNS rebinding).
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.get("/api/config")
def config():
    return {"donate_url": DONATE_URL or None, "version": VERSION, "ytdlp": ytdl.VERSION, **settings.load()}


@app.get("/api/status")
def status():
    """For the desktop app: whether an update would interrupt a cut."""
    return {"busy": jobs.active()}


class Settings(BaseModel):
    auto: bool | None = None


@app.put("/api/settings")
def update_settings(body: Settings):
    current = settings.update(**body.model_dump(exclude_none=True))
    if body.auto and autopilot:
        autopilot.wake.set()
    return current


@app.get("/api/ready")
def ready(after: int = 0):
    """Cuts finished after `after` (a seq from an earlier call), for notifications."""
    return jobs.ready_since(after)


@app.get("/licenses.txt", response_class=PlainTextResponse)
def licenses():
    path = next((p for p in LICENSES if p.is_file()), None)
    if not path:
        raise HTTPException(404)
    return path.read_text(encoding="utf-8")


@app.get("/api/week")
def week(season: int | None = None, seasontype: int | None = None, week: int | None = None):
    try:
        data = games.fetch_week(season, seasontype, week)
    except Exception as e:
        raise HTTPException(502, f"No se pudo cargar el calendario de ESPN: {e}")
    try:
        videos = games.channel_videos()
    except Exception as e:
        ytupdate.failed(e)
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
    teams: str | None = None       # "Chiefs - Dolphins", for the notification


@app.post("/api/games/{game_id}/generate")
def generate(game_id: str, body: Generate):
    return jobs.submit(game_id, body.video_id, body.network, body.week, body.teams)


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
    ap.add_argument("--port", type=int, default=int(os.environ.get("SNAPCUT_PORT", 0)) or None,
                    help="preferred port; any free one if taken")
    ap.add_argument("--exit-with-stdin", action="store_true",
                    help="quit when stdin closes (the desktop app holds it open)")
    args = ap.parse_args()
    # The desktop shell reads this output through a pipe, as UTF-8.
    sys.stdout.reconfigure(line_buffering=True, encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

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

    global uvicorn_server, under_shell
    if args.exit_with_stdin:
        # If the desktop app goes away, even without a clean exit, so does the server.
        threading.Thread(target=lambda: (sys.stdin.read(), os._exit(0)), daemon=True).start()
        under_shell = True

    try:
        last = 8765 if paths.DEV else int(LAST_PORT.read_text())
    except (OSError, ValueError):
        last = 0
    sock = _bind(args.port or last)
    port = sock.getsockname()[1]
    LAST_PORT.write_text(str(port))
    url = f"http://127.0.0.1:{port}"
    running.write_text(json.dumps({"url": url, "pid": os.getpid()}))
    print(f"Snapcut: {url}", flush=True)
    print(f"yt-dlp {ytdl.VERSION} ({ytdl.SOURCE})", flush=True)
    uvicorn_server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    try:
        uvicorn_server.run(sockets=[sock])
    finally:
        running.unlink(missing_ok=True)
    return exit_code
