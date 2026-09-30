"""NFL schedule (ESPN's public scoreboard) matched to the official highlight videos."""
import json
import re
import time
import unicodedata
import urllib.request

from . import ytdl

SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
CHANNEL = "https://www.youtube.com/@NFL/videos"
CHANNEL_DEPTH = 200    # most recent uploads scanned for highlight videos
CACHE_TTL = 15 * 60

_cache = {}


def _cached(key, ttl, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def _get_json(url):
    # ESPN answers 403 to most non-curl user agents.
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.7.1", "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def _norm(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower())


def _team(c):
    t = c["team"]
    return {
        "name": t.get("displayName"),
        "short": t.get("shortDisplayName"),
        "abbr": t.get("abbreviation"),
        "color": "#" + t.get("color", "444444"),
        "alt": "#" + t.get("alternateColor", "888888"),
        "record": next((r["summary"] for r in c.get("records", []) if r.get("type") == "total"), None),
        "score": c.get("score"),
        "winner": c.get("winner", False),
    }


def fetch_week(season=None, seasontype=None, week=None):
    """Games for one week (the current one when nothing is given), plus the
    season calendar for navigation."""
    url = SCOREBOARD
    if week:
        url += f"?seasontype={seasontype or 2}&week={week}&dates={season}"
    ttl = 60 if not week else 5 * 60
    data = _cached(url, ttl, lambda: _get_json(url))

    calendar = []
    for st in data.get("leagues", [{}])[0].get("calendar", []):
        for entry in st.get("entries", []):
            calendar.append({
                "label": entry.get("label"),
                "seasontype": int(st.get("value", 2)),
                "week": int(entry.get("value", 0)),
                "start": entry.get("startDate"),
                "end": entry.get("endDate"),
            })

    games = []
    for ev in data.get("events", []):
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: _team(c) for c in comp["competitors"]}
        status = ev["status"]["type"]
        games.append({
            "id": ev["id"],
            "date": ev["date"],
            "state": status.get("state"),          # pre / in / post
            "status": status.get("shortDetail"),
            "network": next((n for b in comp.get("broadcasts", []) for n in b.get("names", [])), None),
            "venue": comp.get("venue", {}).get("fullName"),
            "away": teams.get("away"),
            "home": teams.get("home"),
        })
    return {
        "season": data.get("season", {}).get("year"),
        "seasontype": data.get("season", {}).get("type"),
        "week": data.get("week", {}).get("number"),
        "calendar": calendar,
        "games": games,
    }


def _channel_videos():
    opts = {"extract_flat": True, "playlistend": CHANNEL_DEPTH, "quiet": True, "no_warnings": True}
    with ytdl.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(CHANNEL, download=False)
    return [
        {"id": e["id"], "title": e.get("title") or "", "duration": e.get("duration")}
        for e in info.get("entries", []) if e and e.get("id")
    ]


def channel_videos():
    return _cached("channel", CACHE_TTL, _channel_videos)


def match_video(game, videos, week=None):
    """The official highlight video for a game: both team names and "highlights"
    in the title. Titles read "Away vs Home Game Highlights | 2026 NFL Season Week 3"."""
    away, home = _norm(game["away"]["name"]), _norm(game["home"]["name"])
    candidates = []
    for v in videos:
        title = _norm(v["title"])
        if "highlights" not in title or away not in title or home not in title:
            continue
        # Skip player/position compilations ("... Top Plays", "... Every Catch").
        if "game highlights" not in title:
            continue
        score = 1 if week and re.search(rf"\bweek {week}\b", title) else 0
        candidates.append((score, v))
    if not candidates:
        return None
    candidates.sort(key=lambda c: -c[0])  # channel order is newest first
    return candidates[0][1]
