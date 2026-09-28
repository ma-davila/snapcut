"""Background queue that turns a game's highlight video into its cut version."""
import json
import queue
import threading
import time
import traceback
from pathlib import Path

import yt_dlp

from . import cut

DATA = Path(__file__).resolve().parent.parent / "data"

# ESPN broadcast name -> scorebug preset, used when the logo match is unsure.
NETWORK_HINTS = {"CBS": "cbs", "FOX": "fox", "NBC": "nbc", "PRIME VIDEO": "prime"}

# Share of the progress bar each stage takes.
STAGES = {"queued": (0.0, 0.0), "downloading": (0.0, 0.45), "analyzing": (0.45, 0.65),
          "rendering": (0.65, 1.0), "done": (1.0, 1.0)}


class Jobs:
    def __init__(self):
        self.state = {}
        self.lock = threading.Lock()
        self.queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def dir(self, game_id):
        return DATA / str(game_id)

    def status(self, game_id):
        with self.lock:
            if game_id in self.state:
                return dict(self.state[game_id])
        meta = self.dir(game_id) / "cut.json"
        if meta.exists() and (self.dir(game_id) / "cut.mp4").exists():
            info = json.loads(meta.read_text())
            info.pop("segments", None)
            return {"stage": "done", "progress": 1.0, **info}
        return None

    def submit(self, game_id, video_id, network=None):
        current = self.status(game_id)
        if current and current["stage"] not in ("error",):
            return current
        self._set(game_id, stage="queued", progress=0.0, error=None)
        self.queue.put((game_id, video_id, network))
        return self.status(game_id)

    def _set(self, game_id, stage=None, frac=None, **extra):
        with self.lock:
            s = self.state.setdefault(game_id, {"stage": "queued", "progress": 0.0})
            if stage:
                s["stage"] = stage
            lo, hi = STAGES.get(s["stage"], (0, 1))
            if frac is not None:
                s["progress"] = round(lo + (hi - lo) * frac, 3)
            elif stage:
                s["progress"] = lo
            s.update(extra)

    def _worker(self):
        while True:
            game_id, video_id, network = self.queue.get()
            try:
                self._run(game_id, video_id, network)
            except Exception as e:
                traceback.print_exc()
                self._set(game_id, stage="error", error=str(e) or type(e).__name__)
            finally:
                self.queue.task_done()

    def _run(self, game_id, video_id, network):
        d = self.dir(game_id)
        d.mkdir(parents=True, exist_ok=True)
        src = d / "src.mp4"
        started = time.time()

        if not src.exists():
            self._set(game_id, stage="downloading")

            files = []  # video first, then audio: weigh them 90/10 on one bar

            def hook(p):
                total = p.get("total_bytes") or p.get("total_bytes_estimate")
                if p["status"] != "downloading" or not total:
                    return
                if p["filename"] not in files:
                    files.append(p["filename"])
                done = p["downloaded_bytes"] / total
                frac = 0.9 * done if files.index(p["filename"]) == 0 else 0.9 + 0.1 * done
                self._set(game_id, frac=frac)

            opts = {
                "format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]",
                "merge_output_format": "mp4",
                "outtmpl": str(d / "src.%(ext)s"),
                "progress_hooks": [hook],
                "quiet": True, "no_warnings": True, "noprogress": True,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

        self._set(game_id, stage="analyzing")
        hint = NETWORK_HINTS.get((network or "").upper())
        try:
            result = cut.analyze(src, network=hint, log=print)
        except cut.UnsupportedNetwork:
            raise RuntimeError(f"Aún no sé leer el marcador de {network or 'esta cadena'}.")

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
            "video_id": video_id,
            "took": round(time.time() - started),
        }
        (d / "cut.json").write_text(json.dumps({**info, "segments": result["segments"]}))
        self._set(game_id, stage="done", progress=1.0, **info)
