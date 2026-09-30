"""Keep yt-dlp current without reinstalling the app.

Every EVERY, and soon after yt-dlp fails (at most once per RETRY_AFTER),
look at yt-dlp's latest release on GitHub. A newer one is downloaded to
<data>/yt-dlp, checked against both GitHub's digest and the release's
SHA2-256SUMS, and must contain that same version. It's loaded on the next
start of the server (see ytdl), which the desktop app does as soon as
nothing is being cut.

On in the packaged app; SNAPCUT_YTDLP_UPDATE=1 turns it on in a checkout,
SNAPCUT_YTDLP_REPO points it at another repo (yt-dlp/yt-dlp-nightly-builds).
"""
import hashlib
import json
import os
import threading
import time
import traceback
import urllib.request

from . import ytdl
from .paths import FROZEN

REPO = os.environ.get("SNAPCUT_YTDLP_REPO", "yt-dlp/yt-dlp")
ENABLED = FROZEN or os.environ.get("SNAPCUT_YTDLP_UPDATE") == "1"
FIRST_CHECK = 60
EVERY = 12 * 3600
RETRY_AFTER = 3600
KEEP = 2  # newest downloads kept; the previous one is the fallback
TIMEOUT = 60

_updater = None


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "snapcut"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def failed(error):
    """Called when something went wrong: if it came from yt-dlp, check for a new one."""
    if _updater and type(error).__module__.split(".")[0] == "yt_dlp":
        _updater.check_soon()


class Updater:
    def __init__(self, on_new):
        global _updater
        _updater = self
        self.on_new = on_new
        self.wake = threading.Event()
        self.last_check = 0.0
        self.pending = None  # version downloaded, waiting for a restart
        threading.Thread(target=self._loop, daemon=True).start()

    def check_soon(self):
        if time.time() - self.last_check > RETRY_AFTER:
            self.wake.set()

    def check(self):
        """Download a newer yt-dlp if there is one; returns its version."""
        self.last_check = time.time()
        release = json.loads(_get(f"https://api.github.com/repos/{REPO}/releases/latest"))
        version = release["tag_name"]
        newest = max([ytdl.VERSION, self.pending or "0"], key=ytdl.version_key)
        if ytdl.version_key(version) <= ytdl.version_key(newest):
            return None
        assets = {a["name"]: a for a in release["assets"]}
        asset = assets["yt-dlp"]
        sums = _get(assets["SHA2-256SUMS"]["browser_download_url"]).decode()
        listed = {line.split()[1]: line.split()[0] for line in sums.splitlines() if len(line.split()) == 2}
        data = _get(asset["browser_download_url"])
        digest = hashlib.sha256(data).hexdigest()
        if digest != listed.get("yt-dlp") or asset.get("digest", f"sha256:{digest}") != f"sha256:{digest}":
            raise RuntimeError(f"yt-dlp {version}: checksum mismatch")

        ytdl.UPDATES.mkdir(parents=True, exist_ok=True)
        tmp = ytdl.UPDATES / f"yt-dlp-{version}.part"
        tmp.write_bytes(data)
        if ytdl.zip_version(tmp) != version:
            tmp.unlink()
            raise RuntimeError(f"yt-dlp {version}: not the expected release file")
        tmp.replace(ytdl.UPDATES / f"yt-dlp-{version}.zip")
        self._prune()
        self.pending = version
        return version

    def _prune(self):
        zips = sorted(ytdl.UPDATES.glob("yt-dlp-*.zip"), key=lambda p: ytdl.version_key(ytdl.zip_version(p)))
        for old in zips[:-KEEP]:
            old.unlink(missing_ok=True)
        for leftover in [*ytdl.UPDATES.glob("*.part"), *ytdl.UPDATES.glob("*.bad")]:
            leftover.unlink(missing_ok=True)

    def _loop(self):
        self.wake.wait(FIRST_CHECK)
        while True:
            self.wake.clear()
            try:
                if version := self.check():
                    print(f"yt-dlp {version} downloaded (running {ytdl.VERSION})")
                    self.on_new(version)
            except Exception:
                traceback.print_exc()
            self.wake.wait(EVERY)
