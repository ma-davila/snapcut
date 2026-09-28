"""Local web app: this week's games (scores hidden) and one-click cut highlights."""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import games
from .jobs import NETWORK_HINTS, Jobs

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="nflcut")
jobs = Jobs()


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
    for g in data["games"]:
        v = games.match_video(g, videos, data["week"]) if g["state"] == "post" else None
        g["video"] = v
        g["job"] = jobs.status(g["id"])
        g["supported"] = (g["network"] or "").upper() in NETWORK_HINTS
    return data


class Generate(BaseModel):
    video_id: str
    network: str | None = None


@app.post("/api/games/{game_id}/generate")
def generate(game_id: str, body: Generate):
    return jobs.submit(game_id, body.video_id, body.network)


@app.get("/api/games/{game_id}/job")
def job(game_id: str):
    return jobs.status(game_id) or {"stage": None}


@app.get("/media/{game_id}.mp4")
def media(game_id: str):
    path = jobs.dir(game_id) / "cut.mp4"
    if not game_id.isdigit() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="video/mp4")


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def main():
    import uvicorn
    uvicorn.run("nflcut.server:app", host="127.0.0.1", port=8765)
