#!/usr/bin/env bash
# Build a small static ffmpeg and ffprobe for Snapcut: x264 to encode, dav1d
# to decode YouTube's AV1, the platform's hardware H.264 encoders, and only
# the formats and filters the app and yt-dlp use.
#
# macOS: run it as is (Xcode command line tools; nasm on Intel).
# Windows: run it from an MSYS2 MINGW64 shell with
#   pacman -S git make nasm diffutils mingw-w64-x86_64-{gcc,cmake,zlib,meson,ninja,pkgconf}
#
# Output in desktop/build/ffmpeg: bin/ (the programs), licenses/, sources/
# (everything that went into the binaries, to ship with each release: GPL),
# BUILD.txt.
set -euo pipefail

FFMPEG_VERSION=9.0.2
FFMPEG_SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e
DAV1D_VERSION=1.5.4
DAV1D_SHA256=686616b7c69eb88d44459391ab25cac13b6647a3b288835c5784e71c1514a5c5
X264_COMMIT=b35605ace3ddf7c1a5d67a2eb553f034aef41d55  # stable branch
# Windows only: GPU encoder headers (NVIDIA, AMD) and Intel's QSV dispatcher.
NVCODEC_VERSION=n12.2.72.0  # NVIDIA driver 550+; older ones fall back to another encoder
AMF_VERSION=v1.5.3  # ffmpeg 9 needs headers >= 1.5.2
LIBVPL_VERSION=v2.17.0

DESKTOP=$(cd "$(dirname "$0")/.." && pwd)
OUT=${OUT:-$DESKTOP/build/ffmpeg}
WORK=${WORK:-$DESKTOP/build/ffmpeg-work}
PREFIX=$WORK/prefix
JOBS=$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)

case "$(uname -s)" in
  Darwin) OS=mac ;;
  MINGW*|MSYS*) OS=win ;;
  *) echo "unsupported system: $(uname -s)" >&2; exit 1 ;;
esac

rm -rf "$OUT" "$PREFIX"
mkdir -p "$WORK" "$PREFIX" "$OUT/bin" "$OUT/licenses" "$OUT/sources"
cd "$WORK"

if [ "$OS" = mac ]; then
  [ "$(uname -m)" = arm64 ] && export MACOSX_DEPLOYMENT_TARGET=11.0 || export MACOSX_DEPLOYMENT_TARGET=10.15
fi
export PKG_CONFIG_PATH=$PREFIX/lib/pkgconfig

# meson, ninja and pkgconf: MSYS2's on Windows; on macOS in a venv, kept out of the system.
if [ "$OS" = mac ]; then
  [ -x tools/bin/meson ] || { python3 -m venv tools && tools/bin/pip install -q meson ninja pkgconf; }
  export PATH=$WORK/tools/bin:$PATH
fi

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

fetch() {  # url file sha256
  [ -f "$2" ] || curl -fsSL -o "$2" "$1"
  [ "$(sha256 "$2")" = "$3" ] || { echo "checksum mismatch: $2" >&2; exit 1; }
  cp "$2" "$OUT/sources/"
}

clone() {  # url dir ref [paths]: shallow; shipped as a tarball of that tree (or of the paths used)
  local url=$1 dir=$2 ref=$3
  shift 3
  [ -d "$dir/.git" ] || git init -q "$dir"
  git -C "$dir" fetch -q --depth 1 "$url" "$ref"
  git -C "$dir" checkout -q --force FETCH_HEAD
  git -C "$dir" archive --prefix="$dir/" -o "$OUT/sources/$dir.tar.gz" HEAD "$@"
}

# --- x264 ---
clone https://code.videolan.org/videolan/x264.git x264 "$X264_COMMIT"
(cd x264 && ./configure --prefix="$PREFIX" --enable-static --enable-pic --disable-cli \
   --disable-opencl --bit-depth=8 --chroma-format=420 && make -j"$JOBS" && make install)
cp x264/COPYING "$OUT/licenses/x264.txt"

# --- dav1d ---
fetch "https://downloads.videolan.org/pub/videolan/dav1d/$DAV1D_VERSION/dav1d-$DAV1D_VERSION.tar.xz" \
  "dav1d-$DAV1D_VERSION.tar.xz" "$DAV1D_SHA256"
rm -rf "dav1d-$DAV1D_VERSION" && tar xf "dav1d-$DAV1D_VERSION.tar.xz"
(cd "dav1d-$DAV1D_VERSION" && meson setup build --prefix="$PREFIX" --libdir=lib --buildtype=release \
   --default-library=static -Denable_tools=false -Denable_tests=false && ninja -C build install)
cp "dav1d-$DAV1D_VERSION/COPYING" "$OUT/licenses/dav1d.txt"

HW=()
if [ "$OS" = mac ]; then
  HW=(--enable-videotoolbox --enable-encoder=h264_videotoolbox)
else
  clone https://github.com/FFmpeg/nv-codec-headers.git nv-codec-headers "$NVCODEC_VERSION"
  make -C nv-codec-headers PREFIX="$PREFIX" install
  head -n 26 nv-codec-headers/include/ffnvcodec/nvEncodeAPI.h > "$OUT/licenses/nv-codec-headers.txt"

  # Only its headers go into ffmpeg; the repository also carries samples and binaries.
  clone https://github.com/GPUOpen-LibrariesAndSDKs/AMF.git AMF "$AMF_VERSION" amf/public/include LICENSE.txt
  mkdir -p "$PREFIX/include/AMF" && cp -r AMF/amf/public/include/* "$PREFIX/include/AMF/"
  cp AMF/LICENSE.txt "$OUT/licenses/amf.txt"

  clone https://github.com/intel/libvpl.git libvpl "$LIBVPL_VERSION"
  git -C libvpl apply "$DESKTOP/scripts/patches/libvpl-mingw.patch"
  cp "$DESKTOP/scripts/patches/libvpl-mingw.patch" "$OUT/sources/"
  cmake -S libvpl -B libvpl/build -G Ninja -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTS=OFF -DBUILD_EXAMPLES=OFF \
    -DINSTALL_EXAMPLES=OFF -DBUILD_TOOLS=OFF
  cmake --build libvpl/build -j"$JOBS"
  cmake --install libvpl/build
  # Its .pc gives paths relative to ${pcfiledir}, which configure doesn't
  # resolve under MSYS2 (MSYS2's own package patches the same thing), and
  # leaves out the C++ runtime a static link needs.
  sed -i "s|^prefix=.*|prefix=$PREFIX|; s|^libdir=.*|libdir=\${prefix}/lib|; s|^includedir=.*|includedir=\${prefix}/include|; /^Libs:/ s|\$| -lstdc++|" \
    "$PREFIX/lib/pkgconfig/vpl.pc"
  cp libvpl/LICENSE "$OUT/licenses/libvpl.txt"

  HW=(--enable-ffnvcodec --enable-nvenc --enable-encoder=h264_nvenc
      --enable-amf --enable-encoder=h264_amf
      --enable-libvpl --enable-encoder=h264_qsv --enable-d3d11va --enable-dxva2
      --extra-ldflags=-static)  # no MinGW runtime DLLs next to the program
fi

# --- ffmpeg ---
fetch "https://ffmpeg.org/releases/ffmpeg-$FFMPEG_VERSION.tar.xz" "ffmpeg-$FFMPEG_VERSION.tar.xz" "$FFMPEG_SHA256"
rm -rf "ffmpeg-$FFMPEG_VERSION" && tar xf "ffmpeg-$FFMPEG_VERSION.tar.xz"
cd "ffmpeg-$FFMPEG_VERSION"
CONFIG=(
  --prefix="$PREFIX" --pkg-config=pkgconf --pkg-config-flags=--static
  --extra-cflags="-I$PREFIX/include" --extra-ldflags="-L$PREFIX/lib"
  --enable-gpl --enable-static --disable-shared --disable-autodetect --disable-debug
  --disable-doc --disable-ffplay --disable-network --enable-zlib
  --disable-everything
  --enable-libx264 --enable-libdav1d
  --enable-protocol=file,pipe
  --enable-demuxer=mov,aac,h264
  --enable-muxer=mp4,mov,ipod,null,rawvideo,pcm_s16le
  --enable-decoder=h264,aac,libdav1d,wrapped_avframe,pcm_s16le
  --enable-encoder=libx264,aac,rawvideo,pcm_s16le,wrapped_avframe
  --enable-parser=h264,aac,av1
  --enable-bsf=aac_adtstoasc,h264_mp4toannexb,extract_extradata,av1_frame_split,av1_frame_merge
  --enable-filter=trim,atrim,setpts,asetpts,afade,concat,fps,scale,crop,format,aformat,aresample,null,anull,split,asplit,copy,testsrc2,sine
  --enable-indev=lavfi
  "${HW[@]}"
)
./configure "${CONFIG[@]}" || { tail -n 40 ffbuild/config.log; exit 1; }
make -j"$JOBS"
EXE=$([ "$OS" = win ] && echo .exe || true)
for p in ffmpeg ffprobe; do
  cp "$p$EXE" "$OUT/bin/"
  strip "$OUT/bin/$p$EXE"
done
cp COPYING.GPLv2 "$OUT/licenses/ffmpeg.txt"
cd ..

{
  echo "ffmpeg $FFMPEG_VERSION, x264 $X264_COMMIT, dav1d $DAV1D_VERSION"
  [ "$OS" = win ] && echo "nv-codec-headers $NVCODEC_VERSION, AMF $AMF_VERSION, libvpl $LIBVPL_VERSION"
  echo "configure: ${CONFIG[*]}" | sed "s|$PREFIX|<prefix>|g"
} > "$OUT/BUILD.txt"
ls -l "$OUT/bin"
