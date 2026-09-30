"""Write latest.json, the manifest the app's updater reads, from a release's files.

  python3 desktop/scripts/latest-json.py VERSION DIR BASE_URL

DIR holds the signed update bundles (Snapcut_<v>_aarch64.app.tar.gz,
Snapcut_<v>_x64.app.tar.gz, Snapcut_<v>_x64-setup.exe, each with its .sig);
BASE_URL is where the release serves them. A platform whose files are
missing is left out.
"""
import datetime as dt
import json
import sys
from pathlib import Path

PLATFORMS = {
    "darwin-aarch64": "Snapcut_{v}_aarch64.app.tar.gz",
    "darwin-x86_64": "Snapcut_{v}_x64.app.tar.gz",
    "windows-x86_64": "Snapcut_{v}_x64-setup.exe",
}


def main():
    version, folder, base = sys.argv[1], Path(sys.argv[2]), sys.argv[3].rstrip("/")
    platforms = {}
    for key, pattern in PLATFORMS.items():
        name = pattern.format(v=version)
        sig = folder / f"{name}.sig"
        if (folder / name).is_file() and sig.is_file():
            platforms[key] = {"url": f"{base}/{name}", "signature": sig.read_text().strip()}
    if not platforms:
        sys.exit(f"no signed update files for {version} in {folder}")
    manifest = {
        "version": version,
        "notes": f"Snapcut {version}",
        "pub_date": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }
    (folder / "latest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"latest.json: {', '.join(platforms)}")


if __name__ == "__main__":
    main()
