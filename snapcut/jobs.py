"""Background queue that turns a game's highlight video into its cut version."""
import json
import queue
import threading
import time
import traceback
from pathlib import Path

from . import cut, cuts, ytdl, ytupdate
from .paths import DATA

# ESPN broadcast name -> scorebug preset, used when the logo match is unsure.
NETWORK_HINTS = {"CBS": "cbs", "FOX": "fox", "NBC": "nbc", "PRIME VIDEO": "prime",
                 "ESPN": "espn", "ABC": "espn", "ESPN2": "espn"}  # ABC carries ESPN's graphics

# Disk housekeeping. The source is deleted as soon as the cut exists; cuts are
# kept for their whole week and cleared when the next week's first game comes
# in (see autopilot). The cut points stay, so making one again only needs the
# download and the render.
STALE_DOWNLOAD_TTL = 2 * 24 * 3600
PURGE_EVERY = 3600

# Share of the progress bar each stage takes.
STAGES = {"queued": (0.0, 0.0), "downloading": (0.0, 0.45), "analyzing": (0.45, 0.65),
          "rendering": (0.65, 1.0), "done": (1.0, 1.0)}
ACTIVE = ("queued", "downloading", "analyzing", "rendering")
# With the cut points known up front there's no analysis stage.
FAST_STAGES = {**STAGES, "downloading": (0.0, 0.6), "rendering": (0.6, 1.0)}


def download(video_id, outdir, progress=None):
    """Fetch a video at 720p to <outdir>/src.mp4; progress gets 0..1."""
    files = []  # video first, then audio: weigh them 90/10 on one bar

    def hook(p):
        total = p.get("total_bytes") or p.get("total_bytes_estimate")
        if not progress or p["status"] != "downloading" or not total:
            return
        if p["filename"] not in files:
            files.append(p["filename"])
        done = p["downloaded_bytes"] / total
        progress(0.9 * done if files.index(p["filename"]) == 0 else 0.9 + 0.1 * done)

    opts = {
        "format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]",
        "merge_output_format": "mp4",
        "outtmpl": str(Path(outdir) / "src.%(ext)s"),
        "progress_hooks": [hook],
        "quiet": True, "no_warnings": True, "noprogress": True,
    }
    with ytdl.YoutubeDL(opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    return Path(outdir) / "src.mp4"


class Jobs:
    def __init__(self):
        self.state = {}
        self.lock = threading.Lock()
        self.queue = queue.Queue()
        # Finished cuts, for the desktop app's notifications: team names only.
        self.events = []
        self.seq = 0
        self.boot = time.time_ns()  # tells a restarted server apart

    def start(self):
        threading.Thread(target=self._worker, daemon=True).start()
        threading.Thread(target=self._housekeeping, daemon=True).start()

    def dir(self, game_id):
        return DATA / str(game_id)

    def _game_dirs(self):
        # Game folders are named by ESPN id; leave everything else in DATA alone.
        return [d for d in DATA.iterdir() if d.is_dir() and d.name.isdigit()] if DATA.exists() else []

    def _meta(self, game_id):
        meta = self.dir(game_id) / "cut.json"
        return json.loads(meta.read_text()) if meta.exists() else None

    def _write_meta(self, game_id, info):
        (self.dir(game_id) / "cut.json").write_text(json.dumps(info))

    def status(self, game_id):
        # Running jobs live in memory; finished ones are read from disk so an
        # expired cut shows up as such.
        with self.lock:
            live = self.state.get(game_id)
            if live and live["stage"] != "done":
                return dict(live)
        info = self._meta(game_id)
        if not info:
            return None
        info.pop("segments", None)
        if not (self.dir(game_id) / "cut.mp4").exists():
            return {"stage": "expired", "progress": 0.0, **info}
        return {"stage": "done", "progress": 1.0, **info}

    def busy(self, game_id):
        with self.lock:
            return self.state.get(game_id, {}).get("stage") in ACTIVE

    def active(self):
        """Whether anything is queued or being cut."""
        with self.lock:
            return any(s.get("stage") in ACTIVE for s in self.state.values())

    def purge_weeks(self, keep):
        """Delete the cuts of every week but `keep` ([season, seasontype, week])."""
        for d in self._game_dirs():
            cut_mp4 = d / "cut.mp4"
            if not cut_mp4.exists() or self.busy(d.name):
                continue
            info = self._meta(d.name) or {}
            if info.get("week") != list(keep):
                cut_mp4.unlink(missing_ok=True)
                print(f"purged cut of {d.name} (week {info.get('week')})")
                with self.lock:
                    self.state.pop(d.name, None)

    def purge(self, now=None):
        now = now or time.time()
        for d in self._game_dirs():
            if self.busy(d.name):
                continue
            cut_mp4, src = d / "cut.mp4", d / "src.mp4"
            if cut_mp4.exists():
                src.unlink(missing_ok=True)
            # Leftovers of failed or interrupted jobs.
            for f in d.iterdir():
                if f.name in ("cut.json", "cut.mp4"):
                    continue
                if now - f.stat().st_mtime > STALE_DOWNLOAD_TTL:
                    f.unlink(missing_ok=True)
            with self.lock:
                if self.state.get(d.name, {}).get("stage") == "done" and not cut_mp4.exists():
                    self.state.pop(d.name, None)

    def _housekeeping(self):
        while True:
            try:
                self.purge()
            except Exception:
                traceback.print_exc()
            time.sleep(PURGE_EVERY)

    def plays(self, game_id):
        """Start of each play in the cut video, plus the cut's nominal length."""
        meta = self.dir(game_id) / "cut.json"
        if not meta.exists() or not (self.dir(game_id) / "cut.mp4").exists():
            return None
        starts, t = [], 0.0
        for a, b in json.loads(meta.read_text())["segments"]:
            starts.append(round(t, 3))
            t += b - a
        return {"starts": starts, "duration": round(t, 3)}

    def ready_since(self, seq):
        with self.lock:
            return {"boot": self.boot, "seq": self.seq, "ready": [e for e in self.events if e["seq"] > seq]}

    def submit(self, game_id, video_id, network=None, week=None, teams=None):
        current = self.status(game_id)
        if current and current["stage"] not in ("error", "expired"):
            return current
        self._set(game_id, stage="queued", progress=0.0, error=None, fast=False)
        self.queue.put((game_id, video_id, network, week, teams))
        return self.status(game_id)

    def _set(self, game_id, stage=None, frac=None, **extra):
        with self.lock:
            s = self.state.setdefault(game_id, {"stage": "queued", "progress": 0.0})
            s.update(extra)
            if stage:
                s["stage"] = stage
            lo, hi = (FAST_STAGES if s.get("fast") else STAGES).get(s["stage"], (0, 1))
            if frac is not None:
                s["progress"] = round(lo + (hi - lo) * frac, 3)
            elif stage:
                s["progress"] = lo

    def _worker(self):
        while True:
            game_id, video_id, network, week, teams = self.queue.get()
            try:
                self._run(game_id, video_id, network, week, teams)
            except Exception as e:
                traceback.print_exc()
                ytupdate.failed(e)
                self._set(game_id, stage="error", error=str(e) or type(e).__name__)
            finally:
                self.queue.task_done()

    def _run(self, game_id, video_id, network, week=None, teams=None):
        d = self.dir(game_id)
        d.mkdir(parents=True, exist_ok=True)
        src = d / "src.mp4"
        started = time.time()

        previous = self._meta(game_id)
        if previous and previous.get("video_id") == video_id and previous.get("segments"):
            # Cut points from an earlier run: no need to analyse again.
            known = {"segments": previous["segments"], "duration": previous["original"],
                     "network": previous["network"], "source": previous.get("source", "local")}
        else:
            published = cuts.video(video_id)
            known = published and {"segments": published["segments"], "duration": published["duration"],
                                   "network": published["preset"], "source": "published"}
        self._set(game_id, fast=bool(known))

        if not src.exists():
            self._set(game_id, stage="downloading")
            download(video_id, d, progress=lambda f: self._set(game_id, frac=f))

        if known and known["source"] == "published":
            duration = cut.probe_duration(src)
            if not cuts.matches(known, duration):
                print(f"{game_id}: download is {duration:.1f}s, published cuts are for "
                      f"{known['duration']:.1f}s; analysing locally")
                known = None
        if known:
            result = {**known, "kept": sum(b - a for a, b in known["segments"])}
        else:
            self._set(game_id, stage="analyzing", fast=False)
            hint = NETWORK_HINTS.get((network or "").upper())
            try:
                result = cut.analyze(src, network=hint, log=print)
            except cut.UnsupportedNetwork:
                raise RuntimeError(f"Aún no sé leer el marcador de {network or 'esta cadena'}.")
            result["source"] = "local"

        if not result["segments"]:
            raise RuntimeError("No he encontrado jugadas en el vídeo.")
        self._set(game_id, stage="rendering")
        cut.render(src, result["segments"], d / "cut.mp4",
                   progress=lambda f: self._set(game_id, frac=f))

        info = {
            "plays": len(result["segments"]),
            "original": round(result["duration"]),
            "kept": round(result["kept"]),
            "network": result["network"],
            "source": result["source"],
            "video_id": video_id,
            "took": round(time.time() - started),
            "cut_at": round(time.time()),
            "week": list(week) if week else (previous or {}).get("week"),
            "teams": teams or (previous or {}).get("teams"),
        }
        self._write_meta(game_id, {**info, "segments": result["segments"]})
        src.unlink(missing_ok=True)
        self._set(game_id, stage="done", progress=1.0, **info)
        with self.lock:
            self.seq += 1
            self.events = [*self.events[-49:], {"seq": self.seq, "game_id": game_id, "teams": info["teams"]}]
