"""Cut points published ahead of time, so the app can skip the analysis.

Layout under the base URL (static JSON, see snapcut/publish.py):
  videos/<youtube id>.json            one video's segments, on the original's clock
  weeks/<season>-<type>-<week>.json   the videos published for a week
"""
import json
import os
import time
import urllib.error
import urllib.request

# Empty disables the service: every video is analysed locally.
CUTS_URL = os.environ.get("SNAPCUT_CUTS_URL", "https://ma-davila.github.io/snapcut-cuts/v1").rstrip("/")
TIMEOUT = 5
HIT_TTL = 24 * 3600  # a video's cuts; the week index is re-read like a miss
MISS_TTL = 5 * 60   # new cuts show up every few minutes on game nights
DOWN_TTL = 60       # don't wait on an unresponsive service at every page load
DURATION_TOLERANCE = 1.0

_cache = {}


def _get(path, hit_ttl):
    hit = _cache.get(path)
    if hit and time.time() < hit[0]:
        return hit[1]
    try:
        req = urllib.request.Request(f"{CUTS_URL}/{path}", headers={"User-Agent": "snapcut"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            value, ttl = json.load(r), hit_ttl
    except urllib.error.HTTPError as e:
        value, ttl = None, MISS_TTL if e.code == 404 else DOWN_TTL
    except (OSError, ValueError):
        value, ttl = None, DOWN_TTL
    _cache[path] = (time.time() + ttl, value)
    return value


def week(season, seasontype, week):
    """Video ids with published cuts for a week (empty when unknown)."""
    if not CUTS_URL or not season or not week:
        return set()
    index = _get(f"weeks/{season}-{seasontype or 2}-{week}.json", MISS_TTL)
    return {v["video_id"] for v in (index or {}).get("videos", [])}


def video(video_id):
    """Published cut points for a YouTube video, or None."""
    if not CUTS_URL:
        return None
    info = _get(f"videos/{video_id}.json", HIT_TTL)
    if not info or info.get("video_id") != video_id or not info.get("segments"):
        return None
    return info


def matches(info, duration):
    """Whether a local download is the same video the cuts were made on."""
    return abs(info["duration"] - duration) <= DURATION_TOLERANCE
