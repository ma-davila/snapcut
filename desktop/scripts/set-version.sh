#!/usr/bin/env bash
# Set the app's version everywhere it's written: set-version.sh 0.2.0
# The release workflow checks the tag (v0.2.0) against tauri.conf.json.
set -euo pipefail
V=${1:?usage: set-version.sh X.Y.Z}
cd "$(dirname "$0")/../.."
sed -i.bak -E "s/^version = \"[^\"]+\"/version = \"$V\"/" pyproject.toml desktop/src-tauri/Cargo.toml
sed -i.bak -E "s/^  \"version\": \"[^\"]+\"/  \"version\": \"$V\"/" desktop/src-tauri/tauri.conf.json
rm -f pyproject.toml.bak desktop/src-tauri/Cargo.toml.bak desktop/src-tauri/tauri.conf.json.bak
uv lock -q
(cd desktop/src-tauri && cargo update -q -p snapcut-desktop)
grep -H -E '^version|"version"' pyproject.toml desktop/src-tauri/Cargo.toml desktop/src-tauri/tauri.conf.json
