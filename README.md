# nflcut

Spoiler-free NFL highlights, cut down to live plays only.

The official NFL YouTube highlights run 15–20 minutes. nflcut keeps only the
stretch from the snap to the whistle and drops replays, celebrations and the
dead time between plays, which usually leaves 6–9 minutes.

It runs locally and is meant for personal use: videos are downloaded to
`data/` on your machine and never leave it.

## Run

```bash
uv run nflcut
```

Then open http://127.0.0.1:8765. The page lists the current week's games
(from ESPN's public scoreboard) with scores hidden; click a score panel to
reveal it. "Generar vídeo" downloads the game's official highlights and cuts
them (about 2 minutes on an M-series Mac).

From the command line:

```bash
uv run nflcut-cut "https://www.youtube.com/watch?v=..." -o cut.mp4
```

Requires `ffmpeg` on the PATH (the renderer uses `h264_videotoolbox`, so macOS).

## How the cut works

The scorebug's play clock tells when a play is live:

- **CBS, NBC**: the clock counts down before the snap, freezes (on 40, 25, or
  wherever it was at the snap) while the play runs, and counts down again
  after the whistle. Live = frozen.
- **FOX**: the clock is hidden during the play but also for most of the
  countdown. It shows up right after the whistle (`:39`) and in the last
  seconds before the snap (`:10`…`:01`). A hidden stretch right after a low
  reading is a play.

Replays and celebrations usually drop the scorebug entirely, so they fall
out. The end of each play is cut 0.1 s after the referee's whistle when it's
audible, or estimated from the clock when it isn't.

The broadcaster is detected from the watermark in the top-right corner
(`nflcut/assets/logos`). ESPN/ABC and Prime Video aren't supported yet.

## Layout

- `nflcut/cut.py` – live-play detection and rendering
- `nflcut/extract.py` – per-network scorebug layouts and frame sampling
- `nflcut/whistle.py` – referee whistle detection
- `nflcut/games.py` – ESPN schedule and YouTube video matching
- `nflcut/jobs.py` – background download/cut queue
- `nflcut/server.py`, `nflcut/static/` – the web app
