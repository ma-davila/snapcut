# Snapcut

Spoiler-free American football highlights, cut down to live plays only.

Official game highlights on YouTube run 15–20 minutes. Snapcut keeps only the
stretch from the snap to the whistle and drops replays, celebrations and the
dead time between plays, which usually leaves 6–9 minutes.

It runs locally and is meant for personal use: videos are downloaded to
`data/` on your machine and never leave it.

> Snapcut is an independent project, not affiliated with or endorsed by any
> league, team or broadcaster. It doesn't host or distribute any video. You're
> responsible for how you use it, including YouTube's Terms of Service and the
> rights of the content owners.

## Run

```bash
uv run snapcut
```

Then open http://127.0.0.1:8765. The page lists the current week's games
(from ESPN's public scoreboard) with scores hidden; click a score panel to
reveal it. "Generar vídeo" downloads the game's official highlights and cuts
them (about 2 minutes on an M-series Mac).

From the command line:

```bash
uv run snapcut-cut "https://www.youtube.com/watch?v=..." -o cut.mp4
```

Requires `ffmpeg` on the PATH (the renderer uses `h264_videotoolbox`, so macOS).

## How the cut works

The scorebug's play clock tells when a play is live:

- **CBS, NBC, ESPN/ABC, Prime Video**: the clock counts down before the snap,
  freezes (on 40, 25, or wherever it was at the snap) while the play runs,
  and counts down again after the whistle. Live = frozen.
- **FOX**: the clock is hidden during the play but also for most of the
  countdown. It shows up right after the whistle (`:39`) and in the last
  seconds before the snap (`:10`…`:01`). A hidden stretch right after a low
  reading is a play. The bar sits over the side of the team with the ball,
  so the clock is read at both bar ends.

Replays and celebrations usually drop the scorebug entirely, so they fall
out. The end of each play is cut 0.1 s after the referee's whistle when it's
audible, or estimated from the clock when it isn't.

The broadcaster is detected from the watermark in the top-right corner
(`snapcut/assets/logos`), or taken from ESPN's schedule data in the app.

Known gaps: kickoffs are lost on FOX (no play clock is shown around them)
and on ESPN's opening kickoff (no scorebug); a few seconds of dead time can
slip in when the clock stays on 40 after a play (penalty announcements).

## Support

Snapcut is free. If it saves you time, you can support it through
[GitHub Sponsors](https://github.com/sponsors/ma-davila).

The app links there from the header and when a video ends. The link is
`DONATE_URL` in `snapcut/server.py`; set `SNAPCUT_DONATE_URL` to point it
elsewhere, or to an empty string to hide it.

Per-network calibration notes, results and known issues:
[docs/marcadores.md](docs/marcadores.md).

## Layout

- `snapcut/cut.py` – live-play detection and rendering
- `snapcut/extract.py` – per-network scorebug layouts and frame sampling
- `snapcut/whistle.py` – referee whistle detection
- `snapcut/games.py` – ESPN schedule and YouTube video matching
- `snapcut/jobs.py` – background download/cut queue
- `snapcut/server.py`, `snapcut/static/` – the web app

## License

Snapcut is free software, released under the
[GNU General Public License v3.0 or later](LICENSE).
