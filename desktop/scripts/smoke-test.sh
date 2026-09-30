#!/usr/bin/env bash
# Check the packaged server on this machine: it starts from a bare
# environment, answers, and its ffmpeg and QuickJS work and need nothing
# outside the system. YouTube isn't tried: it blocks CI's addresses.
set -euo pipefail

DIST=$(cd "$(dirname "$0")/.." && pwd)/build/dist/snapcut-server
EXE=""
[[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]] && EXE=.exe
BIN=$DIST/_internal/bin
TMP=$(mktemp -d)
SERVER=""
# Stop the server first: Windows won't delete files it has open.
cleanup() {
  local status=$?
  if [ -n "$SERVER" ]; then kill "$SERVER" 2>/dev/null || true; wait "$SERVER" 2>/dev/null || true; fi
  rm -rf "$TMP" || true
  exit "$status"
}
trap cleanup EXIT

echo "--- ffmpeg"
"$BIN/ffmpeg$EXE" -hide_banner -version | head -1
"$BIN/ffmpeg$EXE" -hide_banner -encoders 2>/dev/null | grep -E "libx264|h264_(videotoolbox|nvenc|qsv|amf)"
"$BIN/ffmpeg$EXE" -hide_banner -decoders 2>/dev/null | grep -q libdav1d
"$BIN/ffmpeg$EXE" -hide_banner -loglevel error -f lavfi -i testsrc2=size=640x360:duration=1 \
  -f lavfi -i sine=duration=1 -c:v libx264 -b:v 3M -c:a aac -shortest "$TMP/t.mp4"
"$BIN/ffprobe$EXE" -v error -show_entries format=duration -of csv=p=0 "$TMP/t.mp4"
"$BIN/ffmpeg$EXE" -loglevel error -i "$TMP/t.mp4" -ac 1 -ar 22050 -f s16le - | wc -c

echo "--- QuickJS"
"$BIN/qjs$EXE" -e 'print("qjs ok")'

echo "--- linked libraries"
if [ -z "$EXE" ]; then
  for f in "$BIN"/*; do otool -L "$f" | tail -n +2 | awk '{print $1}'; done | sort -u | tee "$TMP/libs"
  ! grep -vE "^/(usr/lib|System/Library)/" "$TMP/libs"
else
  for f in "$BIN"/*.exe; do objdump -p "$f" | awk '/DLL Name/ {print $3}'; done | sort -u | tee "$TMP/libs"
  ! grep -iE "^(libgcc|libstdc|libwinpthread|libx264|libdav1d|zlib)" "$TMP/libs"
fi

echo "--- server"
SNAPCUT_DATA=$TMP/data "$DIST/snapcut-server$EXE" --exit-with-stdin < <(sleep 60) > "$TMP/out.log" 2>&1 &
SERVER=$!
for _ in $(seq 60); do grep -q "^Snapcut: " "$TMP/out.log" && break; sleep 1; done
URL=$(sed -n 's/^Snapcut: //p' "$TMP/out.log" | tr -d '\r')
[ -n "$URL" ] || { cat "$TMP/out.log"; exit 1; }
curl -fsS "$URL/api/config"; echo
curl -fsS -o /dev/null "$URL/licenses.txt"
curl -fsS -o /dev/null "$URL/"
echo "smoke test passed ($URL)"
