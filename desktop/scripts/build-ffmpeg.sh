#!/usr/bin/env bash
# Build a small static ffmpeg and ffprobe for Snapcut: x264 to encode, dav1d
# to decode YouTube's AV1, the platform's hardware H.264 encoders, and only
# the formats and filters the app and yt-dlp use.
#
# macOS: run it as is (Xcode command line tools; nasm on Intel).
# Windows: run it from an MSYS2 MINGW64 shell with
#   pacman -S git make nasm mingw-w64-x86_64-{gcc,cmake,zlib}
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
AMF_VERSION=v1.4.36
LIBVPL_VERSION=v2.15.0

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

# meson, ninja and pkgconf for the build, kept out of the system.
if [ ! -x tools/bin/meson ] && [ ! -x tools/Scripts/meson ]; then
  python3 -m venv tools
  tools/bin/pip install -q meson ninja pkgconf 2>/dev/null || tools/Scripts/pip install -q meson ninja pkgconf
fi
export PATH=$WORK/tools/bin:$WORK/tools/Scripts:$PATH

fetch() {  # url file sha256
  [ -f "$2" ] || curl -fsSL -o "$2" "$1"
  echo "$3  $2" | shasum -a 256 -c - >/dev/null || { echo "checksum mismatch: $2" >&2; exit 1; }
  cp "$2" "$OUT/sources/"
}

clone() {  # url dir ref (tag or commit), shallow; shipped as a tarball of that tree
  [ -d "$2/.git" ] || git init -q "$2"
  git -C "$2" fetch -q --depth 1 "$1" "$3"
  git -C "$2" checkout -q --force FETCH_HEAD
  git -C "$2" archive --prefix="$2/" -o "$OUT/sources/$2.tar.gz" HEAD
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

  clone https://github.com/GPUOpen-LibrariesAndSDKs/AMF.git AMF "$AMF_VERSION"
  mkdir -p "$PREFIX/include/AMF" && cp -r AMF/amf/public/include/* "$PREFIX/include/AMF/"
  cp AMF/LICENSE.txt "$OUT/licenses/amf.txt"

  clone https://github.com/intel/libvpl.git libvpl "$LIBVPL_VERSION"
  cmake -S libvpl -B libvpl/build -G "MSYS Makefiles" -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DBUILD_TESTS=OFF -DBUILD_EXAMPLES=OFF \
    -DINSTALL_EXAMPLES=OFF -DBUILD_TOOLS=OFF
  cmake --build libvpl/build -j"$JOBS" && cmake --install libvpl/build
  cp libvpl/LICENSE "$OUT/licenses/libvpl.txt"

  HW=(--enable-ffnvcodec --enable-nvenc --enable-encoder=h264_nvenc
      --enable-amf --enable-encoder=h264_amf
      --enable-libvpl --enable-encoder=h264_qsv --enable-d3d11va --enable-dxva2)
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
  --enable-decoder=h264,aac,libdav1d,wrapped_avframe
  --enable-encoder=libx264,aac,rawvideo,pcm_s16le,wrapped_avframe
  --enable-parser=h264,aac,av1
  --enable-bsf=aac_adtstoasc,h264_mp4toannexb,extract_extradata,av1_frame_split,av1_frame_merge
  --enable-filter=trim,atrim,setpts,asetpts,afade,concat,fps,scale,crop,format,aformat,aresample,null,anull,split,asplit,copy,testsrc2
  --enable-indev=lavfi
  "${HW[@]}"
)
./configure "${CONFIG[@]}"
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
