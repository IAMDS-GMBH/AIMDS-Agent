#!/usr/bin/env bash
# Build the public Hermes source package for a release (AIS-311).
#
# Usage: scripts/build_source_package.sh <version> <out-dir>
#   version  X.Y.Z or X.Y.Z-rc.N (a leading "v" is stripped)
#   out-dir  destination directory, must be OUTSIDE the repository
#
# Produces in <out-dir>:
#   hermes-source-<version>.zip         git archive of HEAD with the version
#                                       stamped (scripts/set_version.py); the
#                                       export-ignore rules in .gitattributes
#                                       apply; root folder hermes-agent-<version>/
#   hermes-source-<version>.zip.sha256  checksum in `sha256sum -c` format
#   hermes-release.json                 release manifest, format hermes-release-v1
#   install.sh, install.ps1             the install scripts of this release
#
# The working tree is left as it was: the version stamp is captured into a
# temporary git index, archived from there, and reverted on exit. The real
# index is never touched. Requires a clean tree (tracked files) and python3.
set -euo pipefail

usage() {
    echo "usage: $0 <version> <out-dir>" >&2
    exit 2
}

version="${1:-}"
out_dir="${2:-}"
[[ -n "$version" && -n "$out_dir" ]] || usage
version="${version#v}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$ ]]; then
    echo "error: version must be X.Y.Z or X.Y.Z-rc.N, got '$version'" >&2
    exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
mkdir -p "$out_dir"
out_dir="$(cd "$out_dir" && pwd -P)"
case "$out_dir" in
    "$repo_root" | "$repo_root"/*)
        echo "error: out-dir must be outside the repository ($repo_root)" >&2
        exit 2
        ;;
esac

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "error: working tree has uncommitted changes to tracked files" >&2
    exit 1
fi

python_bin="$(command -v python3 || command -v python || true)"
if [[ -z "$python_bin" ]]; then
    echo "error: python3 is required (scripts/set_version.py, manifest)" >&2
    exit 1
fi

commit_sha="$(git rev-parse HEAD)"
tag="v$version"
prefix="hermes-agent-$version/"
archive="hermes-source-$version.zip"

changed=()
tmp_index="$(mktemp)"
rm -f "$tmp_index" # git expects a fresh index path; mktemp only reserved the name
cleanup() {
    rm -f "$tmp_index"
    if [[ ${#changed[@]} -gt 0 ]]; then
        git checkout -q -- "${changed[@]}"
    fi
}
trap cleanup EXIT

echo "Stamping version $version into the working tree (reverted afterwards)"
"$python_bin" scripts/set_version.py "$version"
while IFS= read -r file; do
    [[ -n "$file" ]] && changed+=("$file")
done < <(git diff --name-only)

GIT_INDEX_FILE="$tmp_index" git read-tree HEAD
if [[ ${#changed[@]} -gt 0 ]]; then
    GIT_INDEX_FILE="$tmp_index" git update-index --add -- "${changed[@]}"
fi
tree="$(GIT_INDEX_FILE="$tmp_index" git write-tree)"

echo "Archiving tree $tree as $archive (prefix $prefix)"
git archive --format=zip --prefix="$prefix" -o "$out_dir/$archive" "$tree"

if command -v sha256sum >/dev/null 2>&1; then
    sha256="$(sha256sum "$out_dir/$archive" | cut -d' ' -f1)"
else
    sha256="$(shasum -a 256 "$out_dir/$archive" | cut -d' ' -f1)"
fi
printf '%s  %s\n' "$sha256" "$archive" > "$out_dir/$archive.sha256"

size="$(wc -c < "$out_dir/$archive" | tr -d ' ')"
ARCHIVE="$archive" VERSION="$version" TAG="$tag" COMMIT_SHA="$commit_sha" SHA256="$sha256" SIZE="$size" \
    "$python_bin" - "$out_dir/hermes-release.json" <<'PY'
import datetime
import json
import os
import sys

manifest = {
    "format": "hermes-release-v1",
    "version": os.environ["VERSION"],
    "tag": os.environ["TAG"],
    "commit_sha": os.environ["COMMIT_SHA"],
    "source_archive": os.environ["ARCHIVE"],
    "sha256": os.environ["SHA256"],
    "size": int(os.environ["SIZE"]),
    "built_at": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
}
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(manifest, fh, indent=2)
    fh.write("\n")
PY

echo "Source package ready in $out_dir:"
ls -l "$out_dir"
