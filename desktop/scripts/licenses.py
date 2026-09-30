"""Collect the licenses of everything shipped with the desktop app into one file.

  uv run python desktop/scripts/licenses.py OUT.txt [LICENSE_DIR ...]

Takes the Python packages the server depends on (from uv's lock, without
dev groups), the Python runtime, and every *.txt in the given folders
(ffmpeg, QuickJS, the desktop shell's crates).
"""
import importlib.metadata as md
import subprocess
import sys
import sysconfig
from pathlib import Path

HEADER = """Snapcut includes the following third-party software. Each part is
covered by its own license, reproduced below. The source code of ffmpeg and
the libraries built into it is published with every release.
"""


def python_packages():
    reqs = subprocess.run(["uv", "export", "--no-dev", "--no-hashes", "--no-emit-project",
                           "--format", "requirements-txt"], capture_output=True, text=True, check=True).stdout
    names = [line.split("==")[0].split("[")[0].strip() for line in reqs.splitlines()
             if line and not line.startswith(("#", " "))]
    for name in sorted(set(names), key=str.lower):
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        # Skip packages for other platforms (not installed here).
        files = [f for f in dist.files or [] if "licen" in f.name.lower() or "copying" in f.name.lower()]
        texts = [f.locate().read_text(errors="replace") for f in files if f.locate().is_file()]
        meta = dist.metadata
        license_name = meta.get("License-Expression") or meta.get("License") or ""
        yield f"{dist.name} {dist.version}", license_name.splitlines()[0] if license_name else "", texts


def main():
    out = Path(sys.argv[1])
    parts = [HEADER]

    def add(title, license_name, texts):
        parts.append("=" * 78 + f"\n{title}" + (f" ({license_name})" if license_name else "") + "\n" + "=" * 78)
        parts.extend(t.strip() + "\n" for t in texts)

    py_license = Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt"
    add(f"Python {sys.version.split()[0]}", "PSF-2.0",
        [py_license.read_text()] if py_license.exists() else ["https://docs.python.org/3/license.html"])
    for folder in sys.argv[2:]:
        for f in sorted(Path(folder).glob("*.txt")):
            add(f.stem, "", [f.read_text(errors="replace")])
    for title, license_name, texts in python_packages():
        add(title, license_name, texts or ["(license text not included in the package)"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"{out}: {sum(p.startswith('=') for p in parts)} components")


if __name__ == "__main__":
    main()
