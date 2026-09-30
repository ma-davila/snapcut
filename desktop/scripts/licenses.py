"""Collect the licenses of everything shipped with the desktop app into one file.

  uv run python desktop/scripts/licenses.py OUT.txt [--cargo Cargo.toml] [LICENSE_DIR ...]

Takes the Python packages the server depends on (from uv's lock, without
dev groups), the Python runtime, the desktop shell's Rust crates for this
platform, and every *.txt in the given folders (ffmpeg, QuickJS). Identical
license texts are printed once, after the list of what uses them.
"""
import argparse
import hashlib
import importlib.metadata as md
import json
import subprocess
import sys
import sysconfig
from pathlib import Path

HEADER = """Snapcut includes the following third-party software. Each part is
covered by its own license, reproduced below. The source code of ffmpeg and
the libraries built into it is published with every release.
"""
LICENSE_FILES = ("licen", "copying", "notice", "unlicense")


def python_packages():
    reqs = subprocess.run(["uv", "export", "--no-dev", "--no-hashes", "--no-emit-project",
                           "--format", "requirements-txt"], capture_output=True, text=True, check=True).stdout
    names = [line.split("==")[0].split("[")[0].strip() for line in reqs.splitlines()
             if line and not line.startswith(("#", " "))]
    for name in sorted(set(names), key=str.lower):
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:  # only for other platforms
            continue
        files = [f.locate() for f in dist.files or [] if f.name.lower().startswith(LICENSE_FILES)]
        meta = dist.metadata
        license_name = (meta.get("License-Expression") or meta.get("License") or "").strip().split("\n")[0]
        yield f"{dist.name} {dist.version}", license_name, [f.read_text(errors="replace") for f in files if f.is_file()]


def rust_crates(manifest):
    host = next(line.split()[1] for line in subprocess.run(
        ["rustc", "-vV"], capture_output=True, text=True, check=True).stdout.splitlines()
        if line.startswith("host:"))
    meta = json.loads(subprocess.run(
        ["cargo", "metadata", "--format-version", "1", "--manifest-path", str(manifest), "--filter-platform", host],
        capture_output=True, text=True, check=True).stdout)
    used = {n["id"] for n in meta["resolve"]["nodes"]}
    own = set(meta["workspace_members"])
    for pkg in sorted(meta["packages"], key=lambda p: p["name"]):
        if pkg["id"] not in used or pkg["id"] in own:
            continue
        folder = Path(pkg["manifest_path"]).parent
        files = sorted(f for f in folder.iterdir() if f.is_file() and f.name.lower().startswith(LICENSE_FILES))
        yield f"{pkg['name']} {pkg['version']}", pkg.get("license") or "", [f.read_text(errors="replace") for f in files]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("folders", nargs="*", type=Path)
    ap.add_argument("--cargo", type=Path, help="the desktop shell's Cargo.toml")
    args = ap.parse_args()

    components = []
    py_license = Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt"
    components.append((f"Python {sys.version.split()[0]}", "PSF-2.0",
                       [py_license.read_text()] if py_license.exists() else []))
    for folder in args.folders:
        for f in sorted(folder.glob("*.txt")):
            components.append((f.stem, "", [f.read_text(errors="replace")]))
    components += python_packages()
    if args.cargo:
        components += rust_crates(args.cargo)

    texts, users = {}, {}
    lines = [HEADER, "Components:", ""]
    for title, license_name, bodies in components:
        lines.append(f"  {title}" + (f" ({license_name})" if license_name else ""))
        for body in bodies:
            key = hashlib.sha256(" ".join(body.split()).encode()).hexdigest()
            texts.setdefault(key, body.strip())
            users.setdefault(key, []).append(title)
    for key, body in texts.items():
        lines += ["", "=" * 78, "Used by: " + ", ".join(users[key]), "=" * 78, body]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{args.out}: {len(components)} components, {len(texts)} license texts")


if __name__ == "__main__":
    main()
