"""Cut a highlight video from the command line.

Usage: uv run snapcut-cut <youtube-url-or-mp4> [-o out.mp4] [--network cbs|fox|nbc]
"""
import argparse
import json
from pathlib import Path

from . import cut, ytdl
from .extract import PRESETS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--workdir", default="work")
    ap.add_argument("--network", choices=sorted(PRESETS), help="skip auto-detection")
    args = ap.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(exist_ok=True)
    src = Path(args.source)
    if not src.exists():
        src = workdir / "src.mp4"
        opts = {"format": "bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720]",
                "merge_output_format": "mp4", "outtmpl": str(workdir / "src.%(ext)s")}
        with ytdl.YoutubeDL(opts) as ydl:
            ydl.download([args.source])

    result = cut.analyze(src, network=args.network)
    dst = Path(args.output or workdir / f"{src.stem}_cut.mp4")
    dst.with_suffix(".json").write_text(json.dumps(result["segments"], indent=1))
    cut.render(src, result["segments"], dst)
    print(f"-> {dst}")


if __name__ == "__main__":
    main()
