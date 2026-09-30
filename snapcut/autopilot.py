"""Cut finished games in the background as their highlights come out.

Every few minutes: list the current week's finished games that have their
official video, and queue the ones not cut yet. A game waits for published cut
points (fast: download + render) for up to WAIT_FOR_CUTS after its video is
first seen, then falls back to analysing locally. The first time a new week
has something to cut, the previous weeks' cuts are deleted, so at most one
week of cuts sits on disk.
"""
import json
import threading
import time
import traceback

from . import cuts, games, settings, ytupdate
from .jobs import NETWORK_HINTS
from .paths import DATA

EVERY = 15 * 60
WAIT_FOR_CUTS = 60 * 60
STATE = DATA / "autopilot.json"


class Autopilot:
    def __init__(self, jobs):
        self.jobs = jobs
        self.wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _load(self):
        try:
            return json.loads(STATE.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, state):
        DATA.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(state))

    def run_once(self, now=None):
        now = now or time.time()
        data = games.fetch_week()
        week = [data["season"], data["seasontype"], data["week"]]
        videos = games.channel_videos()
        published = cuts.week(*week)
        state = self._load()
        seen = state.setdefault("seen", {})

        ready = []
        for g in sorted(data["games"], key=lambda g: g["date"]):
            if g["state"] != "post" or (g["network"] or "").upper() not in NETWORK_HINTS:
                continue
            video = games.match_video(g, videos, data["week"])
            if not video:
                continue
            job = self.jobs.status(g["id"])
            # Done, running, or failed (retried after a restart): leave it.
            if job and job["stage"] != "expired":
                continue
            first_seen = seen.setdefault(video["id"], now)
            if video["id"] in published or now - first_seen > WAIT_FOR_CUTS:
                ready.append((g, video))

        if ready and state.get("week") != week:
            self.jobs.purge_weeks(keep=week)
            state["week"] = week
            # Forget videos from older weeks.
            state["seen"] = {v: t for v, t in seen.items() if now - t < 8 * 24 * 3600}
        self._save(state)
        for g, video in ready:
            print(f"autopilot: queueing {g['away']['abbr']}@{g['home']['abbr']} ({video['id']})")
            self.jobs.submit(g["id"], video["id"], g["network"], week, teams=games.teams(g))
        return len(ready)

    def _loop(self):
        while True:
            try:
                if settings.load()["auto"]:
                    self.run_once()
            except Exception as e:
                traceback.print_exc()
                ytupdate.failed(e)
            # Switching it on (see server) runs a round right away.
            self.wake.wait(EVERY)
            self.wake.clear()
