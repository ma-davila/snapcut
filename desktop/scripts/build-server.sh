#!/usr/bin/env bash
# Package the Snapcut server with PyInstaller, with ffmpeg and QuickJS inside.
# Output: desktop/build/dist/snapcut-server/
set -euo pipefail

DESKTOP=$(cd "$(dirname "$0")/.." && pwd)
EXE=""
[[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]] && EXE=.exe
[ -x "$DESKTOP/build/ffmpeg/bin/ffmpeg$EXE" ] || "$DESKTOP/scripts/build-ffmpeg.sh"
[ -x "$DESKTOP/build/quickjs/bin/qjs$EXE" ] || "$DESKTOP/scripts/fetch-quickjs.sh"

cd "$DESKTOP/.."
uv run python desktop/scripts/licenses.py desktop/build/THIRD_PARTY_LICENSES.txt \
  --cargo desktop/src-tauri/Cargo.toml \
  desktop/build/ffmpeg/licenses desktop/build/quickjs/licenses
uv run --group desktop pyinstaller --noconfirm --clean --log-level WARN \
  --distpath desktop/build/dist --workpath desktop/build/pyinstaller desktop/server/snapcut-server.spec
du -sh desktop/build/dist/snapcut-server
