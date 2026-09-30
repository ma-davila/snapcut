"""Analyse finished games and publish their cut points, so the app can skip that step.

Runs on the maintainer's machine: YouTube blocks most datacenter IPs and the
analysis needs ffmpeg and a few CPU minutes per video. The cuts go to a local
clone of the static site (GitHub Pages) and are pushed from there.

Usage:
  uv run snapcut-publish                 check this week and last, publish what's new
  uv run snapcut-publish --no-push       write the files but don't commit or push
  uv run snapcut-publish install         schedule it with launchd on game nights
  uv run snapcut-publish uninstall
"""
import argparse
import base64
import datetime as dt
import json
import logging
import logging.handlers
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import cut, games, paths
from .jobs import NETWORK_HINTS, download

REPO = Path(os.environ.get("SNAPCUT_CUTS_REPO", "~/snapcut-cuts")).expanduser()
FORMAT = "v1"
STATE = paths.DATA
LOGS = paths.LOGS
KEYCHAIN_SERVICE = "snapcut-publish"
MAX_ATTEMPTS = 3
RETRY_AFTER = 3600

LABEL = "io.github.ma-davila.snapcut-publish"
PLIST = Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
EVERY = 15  # minutes
# (launchd weekday, from hour, to hour) in the Mac's local time, meant for
# Spain: 0 is Sunday. Games end between ~21:30 and ~05:30 here.
WINDOWS = [
    (4, 18, 24), (5, 0, 9),               # Thursday night (and Thanksgiving)
    (0, 15, 24), (1, 0, 9),               # Sunday, from the London games to SNF
    (1, 22, 24), (2, 0, 9),               # Monday night
]

log = logging.getLogger("snapcut.publish")


# ---------- publishing ----------

def week_key(season, seasontype, week):
    return f"{season}-{seasontype or 2}-{week}"


def weeks_to_check():
    """This week and the one before: the Monday game is still on last week's
    schedule when ESPN moves on."""
    current = games.fetch_week()
    out = [current]
    cal = current["calendar"]
    i = next((k for k, c in enumerate(cal)
              if c["seasontype"] == current["seasontype"] and c["week"] == current["week"]), None)
    if i:
        prev = cal[i - 1]
        out.append(games.fetch_week(current["season"], prev["seasontype"], prev["week"]))
    return out


def load_state():
    try:
        return json.loads((STATE / "failures.json").read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "failures.json").write_text(json.dumps(state, indent=1))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    text = json.dumps(data, indent=1, ensure_ascii=False)
    text = re.sub(r"\[\s+([\d.]+),\s+([\d.]+)\s+\]", r"[\1, \2]", text)  # one segment per line
    tmp.write_text(text + "\n")
    tmp.replace(path)


def analyze(video_id, network):
    with tempfile.TemporaryDirectory(prefix="snapcut-") as tmp:
        t0 = time.time()
        src = download(video_id, tmp)
        log.info("  downloaded in %.0fs", time.time() - t0)
        t0 = time.time()
        result = cut.analyze(src, network=NETWORK_HINTS.get((network or "").upper()),
                             log=lambda m: log.info("  %s", m))
        log.info("  analysed in %.0fs", time.time() - t0)
    return result


def publish_video(root, week, game, video):
    result = analyze(video["id"], game["network"])
    if not result["segments"]:
        raise RuntimeError("no plays found")
    write_json(root / "videos" / f"{video['id']}.json", {
        "video_id": video["id"],
        "game_id": game["id"],
        "game": f"{game['away']['abbr']} @ {game['home']['abbr']}",
        "season": week["season"],
        "seasontype": week["seasontype"],
        "week": week["week"],
        "network": game["network"],
        "preset": result["network"],
        "duration": round(result["duration"], 3),
        "segments": [[round(a, 3), round(b, 3)] for a, b in result["segments"]],
        "analyzer": cut.ANALYZER_VERSION,
        "published_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    })


def write_week_index(root, week):
    """weeks/<key>.json lists every published video of the week. Rewritten
    only when that list changes."""
    key = (week["season"], week["seasontype"], week["week"])
    videos = []
    for f in sorted((root / "videos").glob("*.json")):
        v = json.loads(f.read_text())
        if (v["season"], v["seasontype"], v["week"]) != key:
            continue
        videos.append({k: v[k] for k in ("video_id", "game_id", "game", "network", "duration", "analyzer")}
                      | {"plays": len(v["segments"])})
    path = root / "weeks" / f"{week_key(*key)}.json"
    try:
        if json.loads(path.read_text())["videos"] == videos:
            return
    except (OSError, ValueError, KeyError):
        pass
    if not videos:
        return
    write_json(path, {"season": key[0], "seasontype": key[1], "week": key[2],
                      "updated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                      "videos": videos})


def run(args):
    root = args.repo / FORMAT
    state = load_state()
    if has_remote(args.repo) and not args.no_push:
        git(args.repo, "pull", "--ff-only", "--quiet")

    weeks = weeks_to_check()
    videos = games.channel_videos()
    done, failed, pending = [], [], []
    for week in weeks:
        for g in week["games"]:
            if g["state"] != "post" or (args.game and g["id"] not in args.game):
                continue
            v = games.match_video(g, videos, week["week"])
            if not v:
                log.info("%s @ %s: no highlights video yet", g["away"]["abbr"], g["home"]["abbr"])
                continue
            if (root / "videos" / f"{v['id']}.json").exists() and not args.force:
                continue
            fail = state.get(v["id"])
            if fail and not args.force and (fail.get("final") or fail["attempts"] >= MAX_ATTEMPTS
                         or time.time() - fail["last"] < RETRY_AFTER):
                continue
            pending.append((week, g, v))
    log.info("week %s: %d video(s) to analyse", weeks[0]["week"], len(pending))

    for week, g, v in pending[:args.limit]:
        name = f"{g['away']['abbr']} @ {g['home']['abbr']}"
        log.info("%s (%s, %s, video %s)", name, g["network"], g["id"], v["id"])
        try:
            publish_video(root, week, g, v)
            state.pop(v["id"], None)
            done.append(name)
            log.info("  published")
        except Exception as e:
            log.exception("  failed: %s", e)
            prev = state.get(v["id"], {"attempts": 0})
            state[v["id"]] = {"attempts": prev["attempts"] + 1, "last": round(time.time()),
                              "error": str(e) or type(e).__name__, "game_id": g["id"],
                              "final": isinstance(e, cut.UnsupportedNetwork)}
            failed.append(name)
        save_state(state)

    for w in weeks:
        write_week_index(root, w)
    if not args.no_push:
        (args.repo / ".nojekyll").touch()
        commit_and_push(args.repo, f"Cuts for week {weeks[0]['week']}: {', '.join(done) or 'update'}")
    log.info("done: %d published, %d failed", len(done), len(failed))
    return 1 if failed else 0


# ---------- git ----------

def git(repo, *cmd, env=None):
    return subprocess.run(["git", "-C", str(repo), *cmd], check=True, capture_output=True,
                          text=True, env=env).stdout


def has_remote(repo):
    return bool(git(repo, "remote").strip())


def token():
    """GitHub token for pushing: env var first, then the macOS keychain. None
    leaves it to git's own credentials."""
    if os.environ.get("SNAPCUT_PUBLISH_TOKEN"):
        return os.environ["SNAPCUT_PUBLISH_TOKEN"]
    r = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def commit_and_push(repo, message):
    git(repo, "add", "-A", FORMAT, ".nojekyll")
    if not git(repo, "status", "--porcelain", "--", FORMAT, ".nojekyll").strip():
        return
    git(repo, "commit", "--quiet", "-m", message)
    log.info("committed: %s", message)
    if not has_remote(repo):
        return
    env = None
    if tok := token():
        # Passed through the environment so it never lands in .git/config or ps.
        basic = base64.b64encode(f"x-access-token:{tok}".encode()).decode()
        env = {**os.environ, "GIT_CONFIG_COUNT": "1",
               "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
               "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}"}
    git(repo, "push", "--quiet", env=env)
    log.info("pushed")


# ---------- launchd ----------

def schedule():
    return [{"Weekday": day, "Hour": h, "Minute": m}
            for day, start, end in WINDOWS for h in range(start, end) for m in range(0, 60, EVERY)]


def install(args):
    LOGS.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parent.parent
    plist = {
        "Label": LABEL,
        "ProgramArguments": [shutil.which("uv"), "run", "--project", str(project), "snapcut-publish",
                             "--repo", str(args.repo), "--log", str(LOGS / "publish.log")],
        # launchd starts with a bare PATH: keep this shell's, for ffmpeg, git and yt-dlp's JS runtime.
        "EnvironmentVariables": {"PATH": os.environ["PATH"], "PYTHONUNBUFFERED": "1"},
        "StartCalendarInterval": schedule(),
        "StandardOutPath": str(LOGS / "launchd.log"),
        "StandardErrorPath": str(LOGS / "launchd.log"),
        "WorkingDirectory": str(project),
    }
    uninstall(quiet=True)
    PLIST.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)], check=True)
    print(f"Installed {PLIST}\n{len(plist['StartCalendarInterval'])} runs a week; log in {LOGS / 'publish.log'}")


def uninstall(quiet=False):
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    if PLIST.exists():
        PLIST.unlink()
        if not quiet:
            print(f"Removed {PLIST}")
    elif not quiet:
        print("Not installed")


# ---------- entry point ----------

def main():
    ap = argparse.ArgumentParser(description="Publish Snapcut cut points.")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "install", "uninstall"])
    ap.add_argument("--repo", type=Path, default=REPO, help="local clone of the cuts site")
    ap.add_argument("--no-push", action="store_true", help="write the files only")
    ap.add_argument("--game", action="append", help="only this ESPN game id (repeatable)")
    ap.add_argument("--limit", type=int, default=None, help="analyse at most this many videos")
    ap.add_argument("--force", action="store_true", help="redo videos already published or given up on")
    ap.add_argument("--log", type=Path, help="log to this file (rotated) instead of stderr")
    args = ap.parse_args()
    args.repo = args.repo.expanduser().resolve()

    if args.command == "install":
        return install(args)
    if args.command == "uninstall":
        return uninstall()

    handlers = [logging.StreamHandler()]
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
        handlers = [logging.handlers.RotatingFileHandler(args.log, maxBytes=2_000_000, backupCount=3)]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)

    if not (args.repo / ".git").exists():
        log.error("%s is not a git clone of the cuts site (set SNAPCUT_CUTS_REPO or --repo)", args.repo)
        return 2
    lock = paths.lock(STATE / "publish.lock")
    if not lock:
        log.info("another run is in progress")
        return 0
    with lock:
        try:
            return run(args)
        except Exception:
            log.exception("run failed")
            return 1


if __name__ == "__main__":
    sys.exit(main())
