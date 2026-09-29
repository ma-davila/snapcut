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
them (about 2 minutes on an M-series Mac, or about 1.5 when the cut points are
already published; see below).

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

## Published cut points

Finding the plays is the slow part, so the maintainer runs the analysis once
per video and publishes the result as static JSON. Before cutting, the app
asks for that video's cut points and, if they exist, only downloads and
renders. The card shows "Rápido" for those games.

The service lives at `CUTS_URL` in `snapcut/cuts.py`
(`https://ma-davila.github.io/snapcut-cuts/v1`). Set `SNAPCUT_CUTS_URL` to
point it elsewhere, or to an empty string to always analyse locally. The app
also analyses locally when the service doesn't answer, has nothing for the
video, or the downloaded video's length differs from the published one by more
than a second (the video was replaced).

Layout (all times on the original YouTube video's clock, in seconds):

- `videos/<youtube id>.json`: `video_id`, `game_id` (ESPN), `game`, `season`,
  `seasontype`, `week`, `network` (ESPN's name), `preset` (scorebug preset),
  `duration`, `segments` (`[[start, end], ...]`), `analyzer`
  (`ANALYZER_VERSION` in `snapcut/cut.py`), `published_at`.
- `weeks/<season>-<seasontype>-<week>.json`: the week's videos with
  `video_id`, `game_id`, `game`, `network`, `duration`, `analyzer`, `plays`.

No scores or results go into these files.

### Publishing

`snapcut-publish` checks the current and previous week and, for every finished
game with a highlights video and no published cuts, downloads the video to a
temporary folder, analyses it, deletes it and writes the JSON. Then it
commits and pushes. It's idempotent, keeps going when a game fails (retried up
to 3 times, an hour apart; an unsupported scorebug isn't retried) and runs
one at a time.

It runs on the maintainer's Mac, not in the cloud: YouTube blocks most
datacenter IPs, and the analysis needs ffmpeg and a few CPU minutes.

One-time setup:

1. A repo for the site (`ma-davila/snapcut-cuts`) with GitHub Pages serving
   the `main` branch root, cloned to `~/snapcut-cuts` (or set
   `SNAPCUT_CUTS_REPO`).
2. Credentials for the push, never in the repo. Either git's own (the
   `gh`/keychain credential helper you already use for GitHub), or a
   fine-grained token with *Contents: read and write* on that repo only, in
   the macOS keychain (`-w` with no value prompts for it):

   ```bash
   security add-generic-password -a "$USER" -s snapcut-publish -w
   ```

   `SNAPCUT_PUBLISH_TOKEN` overrides the keychain. The token is handed to git
   through the environment, so it isn't written to `.git/config`.

Run it by hand:

```bash
uv run snapcut-publish                  # everything pending
uv run snapcut-publish --game 401872952 # one ESPN game
uv run snapcut-publish --no-push        # write the files, don't commit or push
```

`--limit N` analyses at most N videos; `--force` redoes videos already
published or given up on. Failures are kept in
`~/Library/Application Support/Snapcut/failures.json`.

Schedule it with launchd, every 15 minutes on game nights (Spanish time:
Thursday 18:00 to Friday 09:00, Sunday 15:00 to Monday 09:00, Monday 22:00 to
Tuesday 09:00; edit `WINDOWS` in `snapcut/publish.py` for other slots, such as
late-season Saturdays):

```bash
uv run snapcut-publish install
```

This writes `~/Library/LaunchAgents/io.github.ma-davila.snapcut-publish.plist`
and loads it. It keeps the current shell's `PATH` (for ffmpeg, git and
yt-dlp's JavaScript runtime), so run it from a terminal where those work. The
log is `~/Library/Logs/Snapcut/publish.log`. Runs missed while the Mac sleeps
happen on wake. Run it now, or remove it:

```bash
launchctl kickstart gui/$(id -u)/io.github.ma-davila.snapcut-publish
```

```bash
uv run snapcut-publish uninstall
```

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
- `snapcut/cuts.py` – client for the published cut points
- `snapcut/publish.py` – analyses finished games and publishes their cut points
- `snapcut/server.py`, `snapcut/static/` – the web app

## License

Snapcut is free software, released under the
[GNU General Public License v3.0 or later](LICENSE).
