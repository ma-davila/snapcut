"""Local web app: this week's games (scores hidden) and one-click cut highlights."""
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cuts, games
from .autopilot import Autopilot
from .jobs import NETWORK_HINTS, Jobs

STATIC = Path(__file__).parent / "static"

# Where the "buy me a coffee" links point. Empty hides them.
DONATE_URL = os.environ.get("SNAPCUT_DONATE_URL", "https://github.com/sponsors/ma-davila")

# Cut finished games in the background; SNAPCUT_AUTO=0 turns it off.
AUTO = os.environ.get("SNAPCUT_AUTO", "1") != "0"

app = FastAPI(title="snapcut")
jobs = Jobs()
autopilot = Autopilot(jobs) if AUTO else None


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


def main():
    import uvicorn
    uvicorn.run("snapcut.server:app", host="127.0.0.1", port=8765)
