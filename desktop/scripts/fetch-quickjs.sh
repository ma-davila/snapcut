#!/usr/bin/env bash
# Fetch QuickJS-NG, the JavaScript runtime yt-dlp uses for YouTube's
# challenges, for this platform. Output in desktop/build/quickjs.
set -euo pipefail

VERSION=v0.17.0
case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) ASSET=qjs-darwin-arm64 SHA256=8be3ddfe3397d2e692e4e1e8972ee9d032a0a580505d2f8b4ea528cf1b651c11 ;;
  Darwin-x86_64) ASSET=qjs-darwin-x86_64 SHA256=9e5e101b4fd13cda3204222ca9f8be35412c41dcdef3745829633b7a67245412 ;;
  MINGW*-x86_64|MSYS*-x86_64) ASSET=qjs-windows-x86_64.exe SHA256=2aeabf0092c3262d6b2609824418f7dd7ed1f1df939f73b2b15645230cac0d77 ;;
  *) echo "unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

OUT=${OUT:-$(cd "$(dirname "$0")/.." && pwd)/build/quickjs}
EXE=$([[ $ASSET == *.exe ]] && echo .exe || true)
rm -rf "$OUT" && mkdir -p "$OUT/bin" "$OUT/licenses"
curl -fsSL -o "$OUT/bin/qjs$EXE" "https://github.com/quickjs-ng/quickjs/releases/download/$VERSION/$ASSET"
GOT=$( (command -v sha256sum >/dev/null && sha256sum "$OUT/bin/qjs$EXE" || shasum -a 256 "$OUT/bin/qjs$EXE") | cut -d' ' -f1)
[ "$GOT" = "$SHA256" ] || { echo "checksum mismatch: $ASSET" >&2; exit 1; }
chmod +x "$OUT/bin/qjs$EXE"
curl -fsSL -o "$OUT/licenses/quickjs-ng.txt" "https://raw.githubusercontent.com/quickjs-ng/quickjs/$VERSION/LICENSE"
echo "QuickJS-NG $VERSION ($ASSET)" > "$OUT/BUILD.txt"
