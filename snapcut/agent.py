"""Keep the Snapcut app running in the background on macOS (launchd).

  uv run snapcut-agent install     start at login and restart if it stops
  uv run snapcut-agent uninstall   stop it and remove the agent
"""
import argparse
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

LABEL = "io.github.ma-davila.snapcut"
PLIST = Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
LOGS = Path("~/Library/Logs/Snapcut").expanduser()


def install():
    LOGS.mkdir(parents=True, exist_ok=True)
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parent.parent
    plist = {
        "Label": LABEL,
        "ProgramArguments": [shutil.which("uv"), "run", "--project", str(project), "snapcut"],
        # launchd starts with a bare PATH: keep this shell's, for ffmpeg and yt-dlp's JS runtime.
        "EnvironmentVariables": {"PATH": os.environ["PATH"], "PYTHONUNBUFFERED": "1"},
        "RunAtLoad": True,
        "KeepAlive": True,
        # Background work: don't compete with what's on screen.
        "ProcessType": "Background",
        "StandardOutPath": str(LOGS / "app.log"),
        "StandardErrorPath": str(LOGS / "app.log"),
        "WorkingDirectory": str(project),
    }
    uninstall(quiet=True)
    PLIST.write_bytes(plistlib.dumps(plist))
    subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(PLIST)], check=True)
    print(f"Installed {PLIST}\nSnapcut runs at http://127.0.0.1:8765; log in {LOGS / 'app.log'}")


def uninstall(quiet=False):
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)
    if PLIST.exists():
        PLIST.unlink()
        if not quiet:
            print(f"Removed {PLIST}")
    elif not quiet:
        print("Not installed")


def main():
    ap = argparse.ArgumentParser(description="Run Snapcut in the background at login.")
    ap.add_argument("command", choices=["install", "uninstall"])
    args = ap.parse_args()
    install() if args.command == "install" else uninstall()


if __name__ == "__main__":
    main()
