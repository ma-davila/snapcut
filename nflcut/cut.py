"""Cut NFL YouTube highlights down to live action only.

A play is "live" while the scorebug's play clock is frozen: it resets at the
snap (to 40 or 25, or disappears on FOX) and starts counting down again right
after the whistle. Replays and celebrations usually drop the scorebug
entirely, so they fall out too.
"""
import subprocess
from pathlib import Path

import numpy as np
from scipy.ndimage import median_filter

from .extract import BUG_SCALE, FPS, PRESETS, bug_distance, detect_network, read_crops
from .whistle import SR, whistle_onsets

CLOCK_CHANGED = 0.02  # share of clock pixels that must flip for a change
CLOCK_MATCH = 10.0    # mean abs diff for two clock images to be the same state
STATIC_SPAN = 1.2   # seconds; a counting clock always changes within this
BUG_WINDOW = 120    # seconds of footage the scorebug reference is taken from
PRE_ROLL = 0.5      # seconds kept before the snap
POST_ROLL = 0.1     # seconds kept after the whistle
WHISTLE_LAG = 1.0   # when no whistle is heard: the clock shows 40 for >=1s after it
MERGE_GAP = 1.0     # join live runs separated by less than this
MIN_PLAY = 1.5      # drop live runs shorter than this
FADE = 0.08         # audio fade at each cut
WHISTLE_WINDOW = (3.0, 0.3)  # search for the whistle this long before the clock leaves 40
WHISTLE_MIN_PLAY = 2.0       # ignore tones this close to the snap


def otsu(values, bins=128):
    """Threshold splitting a bimodal distribution (scorebug on screen vs. not)."""
    hist, edges = np.histogram(values, bins=bins)
    mids = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * mids) / np.maximum(w0, 1)
    m1 = (np.sum(hist * mids) - np.cumsum(hist * mids)) / np.maximum(w1, 1)
    return mids[np.argmax(w0 * w1 * (m0 - m1) ** 2)]


def frozen_states(crops, min_share=0.03):
    """Images the play clock freezes on repeatedly: 40/25 on CBS/NBC, blank on FOX."""
    flat = crops.reshape(len(crops), -1)[::3]
    clusters = []
    remaining = np.ones(len(flat), bool)
    while remaining.any():
        cand = flat[np.flatnonzero(remaining)[0]]
        members = remaining & (np.abs(flat - cand).mean(axis=1) < CLOCK_MATCH)
        clusters.append((members.sum(), cand))
        remaining &= ~members
    return [c.reshape(crops.shape[1:]) for n, c in clusters if n >= min_share * len(flat)]


FOX_TENS = np.load(Path(__file__).parent / "assets" / "digits" / "fox_tens.npz")
TENS_X = slice(9, 23)   # tens digit inside the aligned FOX clock crop
HURRY_TAIL = 10.0       # seconds kept from a hidden stretch with no pre-snap clock
CLOCK_W = 50


def align_fox_clock(strip):
    """Cut the clock out of the bar row: it's the last CLOCK_W px of the dark
    bar, which is the first long dark run from the left."""
    out = np.zeros((len(strip), strip.shape[1], CLOCK_W), np.uint8)
    bar = (strip < 70).mean(axis=1) > 0.6
    for i, cols in enumerate(bar):
        idx = np.flatnonzero(cols)
        if len(idx) < 40:
            continue
        # Split into runs (letters leave gaps) and take the first long one.
        breaks = np.flatnonzero(np.diff(idx) > 20)
        starts = np.r_[idx[0], idx[breaks + 1]]
        ends = np.r_[idx[breaks], idx[-1]]
        long_runs = [(a, b) for a, b in zip(starts, ends) if b - a > 100]
        if not long_runs:
            continue
        e = min(long_runs[0][1] + 3, strip.shape[2])
        if e >= CLOCK_W:
            out[i] = strip[i, :, e - CLOCK_W:e]
    return out


def fox_live(clock, present):
    """FOX hides the play clock during the play, but also for most of the
    countdown. It shows it twice between plays: right after the whistle
    (:39, :38...) and in the last seconds before the snap (:15...:01). So a
    hidden stretch right after a low reading is the play; right after a :3x
    reading, or a text banner (flag, penalty), it's dead time."""
    c = clock.astype(np.float32)
    dark = (c < 70).mean(axis=(1, 2))
    bright = (c > 190).mean(axis=(1, 2))
    shown = (dark > 0.4) & (dark < 0.9) & (bright > 0.02)
    shown = present & (median_filter(shown.astype(np.uint8), size=int(0.5 * FPS) | 1) > 0)
    hidden = present & ~shown & (bright <= 0.4)  # bright = yellow FLAG banner

    tens = c[:, :, TENS_X]

    def reading(i):
        d = {k: min(np.abs(tens[i] - t).mean() for t in FOX_TENS[k]) for k in ("high", "low", "junk")}
        return min(d, key=d.get)

    def runs(mask):
        out, i = [], 0
        while i < len(mask):
            if mask[i]:
                j = i
                while j < len(mask) and mask[j]:
                    j += 1
                out.append((i, j))
                i = j
            else:
                i += 1
        return out

    # Label each shown run by its first and last readings: low means the snap
    # is coming; high (:3x) or a text banner means a play just ended.
    # Text that isn't the clock (the tail of "1ST & GOAL", "FG ATTEMPT") at the
    # bar's end means the clock is hidden, not shown.
    junk = np.array([shown[i] and reading(i) == "junk" for i in range(len(c))])
    shown &= ~junk
    hidden |= present & junk
    starts, ends = {}, {}
    for a, b in runs(shown):
        first = [reading(i) for i in range(a, min(b, a + 5))]
        last = [reading(i) for i in range(max(a, b - 5), b)]
        starts[a] = "pre" if first.count("low") > len(first) / 2 else "post"
        ends[b] = "pre" if last.count("low") > len(last) / 2 else "post"

    live = np.zeros(len(c), bool)
    gap = int(0.5 * FPS)
    tail = int(HURRY_TAIL * FPS)
    for a, b in runs(hidden):
        prev = next((ends[e] for e in range(a, max(0, a - gap) - 1, -1) if e in ends), None)
        nxt = next((starts[s] for s in range(b, min(len(c), b + gap) + 1) if s in starts), None)
        if prev == "pre":
            live[a:b] = True
        elif prev is None and nxt == "post":
            live[max(a, b - tail):b] = True
    return live


def live_mask(clock, bug, mode="frozen"):
    bug_dist = bug_distance(bug, window=BUG_WINDOW * FPS)
    present = bug_dist < otsu(bug_dist)
    if mode == "hidden":
        return fox_live(clock, present)
    # A counting play clock changes at least once in any 1.2s window.
    c = clock.astype(np.float32)
    k = int(round(STATIC_SPAN * FPS))
    same = (np.abs(c[k:] - c[:-k]) > 40).mean(axis=(1, 2)) < CLOCK_CHANGED
    static = np.zeros(len(c), bool)
    static[k:] |= same
    static[:-k] |= same
    # The clock freezes on its reset value (40/25, or blank) during most plays.
    states = frozen_states(c[present & static])
    known = np.zeros(len(c), bool)
    for st in states:
        known |= np.abs(c - st).mean(axis=(1, 2)) < CLOCK_MATCH
    # Sometimes it just stops wherever it was at the snap (":14") and the edit
    # cuts away before it resets. Accept such a freeze only when it follows a
    # countdown directly and no play just ended; after a play, a clock stopped
    # on some other value is dead time (field goal, injury, celebration).
    odd = present & static & ~known
    ticking = present & ~static
    accept = np.zeros(len(c), bool)
    i = 0
    while i < len(c):
        if not odd[i]:
            i += 1
            continue
        j = i
        while j < len(c) and odd[j]:
            j += 1
        recent_play = known[max(0, i - int(4 * FPS)):i].any()
        if ticking[max(0, i - 3):i].any() and not recent_play:
            accept[i:j] = True
        i = j
    return present & static & (known | accept)


def segments(mask, duration, audio=None):
    runs, i = [], 0
    while i < len(mask):
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < len(mask) and mask[j]:
            j += 1
        runs.append([i / FPS, j / FPS])
        i = j
    merged = []
    for r in runs:
        if merged and r[0] - merged[-1][1] < MERGE_GAP:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    out, whistles = [], 0
    for a, b in merged:
        if b - a < MIN_PLAY:
            continue
        end = b - WHISTLE_LAG
        if audio is not None:
            lo = max(a + WHISTLE_MIN_PLAY, b - WHISTLE_WINDOW[0])
            onsets = whistle_onsets(audio, lo, b - WHISTLE_WINDOW[1]) if lo < b - WHISTLE_WINDOW[1] else []
            if onsets:
                end, whistles = onsets[0], whistles + 1
        a, b = max(0.0, a - PRE_ROLL), min(duration, end + POST_ROLL)
        if out and a <= out[-1][1]:
            out[-1][1] = b
        else:
            out.append([a, b])
    return out, whistles


def load_audio(src):
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


LOGO_MAX = 35.0  # logo match above this means none of the known networks


class UnsupportedNetwork(Exception):
    pass


def probe_duration(src):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, check=True).stdout)


def render(src, segs, dst, progress=None):
    parts = []
    for k, (a, b) in enumerate(segs):
        d = b - a
        parts.append(
            f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS[v{k}];"
            f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d={FADE},afade=t=out:st={d - FADE:.3f}:d={FADE}[a{k}];"
        )
    joined = "".join(f"[v{k}][a{k}]" for k in range(len(segs)))
    graph = "".join(parts) + f"{joined}concat=n={len(segs)}:v=1:a=1[v][a]"
    script = Path(dst).with_suffix(".filter.txt")
    script.write_text(graph)
    tmp = Path(dst).with_suffix(".part.mp4")
    total = sum(b - a for a, b in segs)
    proc = subprocess.Popen([
        "ffmpeg", "-loglevel", "error", "-y", "-i", str(src),
        "-filter_complex_script", str(script), "-map", "[v]", "-map", "[a]",
        "-c:v", "h264_videotoolbox", "-b:v", "5M", "-c:a", "aac", "-b:a", "160k",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(tmp),
    ], stdout=subprocess.PIPE, text=True)
    for line in proc.stdout:
        if progress and line.startswith("out_time_us="):
            try:
                progress(min(1.0, int(line.split("=")[1]) / 1e6 / total))
            except ValueError:
                pass
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed while rendering")
    tmp.replace(dst)
    script.unlink(missing_ok=True)


def analyze(src, network=None, log=print):
    """Find the live-play segments of a highlight video."""
    duration = probe_duration(src)
    detected, scores = detect_network(src, duration)
    if not network and scores[detected] > LOGO_MAX:
        raise UnsupportedNetwork(
            f"This broadcaster's scorebug isn't supported yet (closest: {detected}, "
            f"match {scores[detected]:.0f})")
    network = network or detected
    log(f"network: {network} (logo match: {', '.join(f'{k} {v:.0f}' for k, v in scores.items())})")
    preset = PRESETS[network]
    clock, bug = read_crops(src, preset["clock"]), read_crops(src, preset["bug"], BUG_SCALE)
    if preset["mode"] == "hidden":
        clock = align_fox_clock(clock)
    n = min(len(clock), len(bug))
    segs, whistles = segments(live_mask(clock[:n], bug[:n], preset["mode"]), duration, load_audio(src))
    kept = sum(b - a for a, b in segs)
    log(f"{len(segs)} plays ({whistles} cut on the whistle), "
        f"{kept / 60:.1f} of {duration / 60:.1f} min")
    return {"network": network, "segments": segs, "duration": duration, "kept": kept}
