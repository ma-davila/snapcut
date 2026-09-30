# PyInstaller spec for the packaged server; run by desktop/scripts/build-server.sh.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
BUILD = os.path.join(ROOT, "desktop", "build")
SUFFIX = ".exe" if os.name == "nt" else ""

# ffmpeg, ffprobe and QuickJS go to bin/, where snapcut.tools looks for them.
binaries = [(os.path.join(BUILD, folder, "bin", name + SUFFIX), "bin")
            for folder, name in [("ffmpeg", "ffmpeg"), ("ffmpeg", "ffprobe"), ("quickjs", "qjs")]]
datas = [
    (os.path.join(ROOT, "snapcut", "static"), "snapcut/static"),
    (os.path.join(ROOT, "snapcut", "assets"), "snapcut/assets"),
    (os.path.join(BUILD, "THIRD_PARTY_LICENSES.txt"), "."),
    *collect_data_files("yt_dlp_ejs"),
    *copy_metadata("snapcut"),  # version shown in "Acerca de"
    *copy_metadata("yt-dlp"),   # bundled version, compared with downloaded ones
]

a = Analysis(
    [os.path.join(SPECPATH, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules("uvicorn"),
    excludes=["tkinter", "PIL", "playwright"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="snapcut-server", console=True, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, upx=False, name="snapcut-server")
