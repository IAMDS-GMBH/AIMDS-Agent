#!/usr/bin/env bash
# createTag.sh — cut a release candidate from main or promote one to stable.
#
# Releases are git tags (AIS-292); the "Release Bootstrap Installers" workflow
# reacts to the tag push. main is pull-request only, so nothing here commits —
# the tag is the version source, the workflow stamps the version into the
# build workspace. See docs/RELEASE.md.
#
# Usage:
#   ./createTag.sh [patch|minor|major] [--dry-run] [--yes]
#       main only, local main == origin/main. Bumps the highest STABLE tag
#       (vX.Y.Z) and tags HEAD as the next release candidate vX.Y.Z-rc.N.
#       If candidates for that version already exist, N increments; if HEAD
#       already carries a candidate of that version, nothing is created.
#       The workflow builds the installers and publishes a GitHub pre-release.
#
#   ./createTag.sh promote stable [--dry-run] [--yes]
#       main only. Tags the exact commit of the highest candidate whose version
#       is above the highest stable tag as vX.Y.Z (no bump, no new code). The
#       workflow re-publishes that candidate's artifacts as the latest release.
#
#   ./createTag.sh status
#       Shows the highest stable tag, the current candidate and what HEAD is.
#
# Options:
#   --dry-run   Print what would be created/pushed, change nothing.
#   --yes       Skip the confirmation prompts (CI / scripted use).
set -euo pipefail

MODE="bump"
BUMP_TYPE="patch"
PROMOTE_TARGET=""
DRY_RUN=false
ASSUME_YES=false

usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    patch|minor|major) MODE="bump"; BUMP_TYPE="$1" ;;
    promote) MODE="promote"; shift; PROMOTE_TARGET="${1:-}"; [[ -z "$PROMOTE_TARGET" ]] && { echo "❌ promote needs a target: stable" >&2; exit 1; } ;;
    status) MODE="status" ;;
    --dry-run) DRY_RUN=true ;;
    --yes|-y) ASSUME_YES=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "❌ Unknown argument: $1" >&2; usage >&2; exit 1 ;;
  esac
  shift
done

if [[ "$MODE" == "promote" && "$PROMOTE_TARGET" != "stable" ]]; then
  echo "❌ Only 'promote stable' is supported (got '${PROMOTE_TARGET}')." >&2
  exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "❌ Not inside a git repository." >&2; exit 1; }
cd "$REPO_ROOT"

CURRENT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
HEAD_SHA="$(git rev-parse HEAD)"

# ── helpers ──────────────────────────────────────────────────────────────────

# Refresh tags and main from origin (best-effort; offline keeps the local cache).
refresh_refs() {
  if [[ "$DRY_RUN" == "true" ]]; then
    return 0
  fi
  echo "🔄 Fetching tags and main from origin..."
  GIT_TERMINAL_PROMPT=0 git fetch origin --tags --prune --quiet 2>/dev/null \
    || echo "⚠️  Could not fetch tags from origin (offline?) — using the local tag cache." >&2
  GIT_TERMINAL_PROMPT=0 git fetch origin main --quiet 2>/dev/null \
    || echo "⚠️  Could not fetch main from origin (offline?) — using the local ref cache." >&2
}

# highest_stable — highest vX.Y.Z tag, or "".
highest_stable() {
  git tag -l 'v*' | { grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true; } | sort -t. -k1,1V -k2,2V -k3,3V | sort -V | tail -1
}

# highest_rc_for <X.Y.Z> — highest N among vX.Y.Z-rc.N tags, or 0.
highest_rc_for() {
  local version="$1"
  git tag -l "v${version}-rc.*" | { grep -E "^v${version//./\\.}-rc\.[0-9]+$" || true; } \
    | sed -E 's/.*-rc\.([0-9]+)$/\1/' | sort -n | tail -1 | { read -r n || true; echo "${n:-0}"; }
}

# highest_candidate — the highest vX.Y.Z-rc.N tag overall (semver order), or "".
highest_candidate() {
  git tag -l 'v*-rc.*' | { grep -E '^v[0-9]+\.[0-9]+\.[0-9]+-rc\.[0-9]+$' || true; } \
    | sed -E 's/^v([0-9]+)\.([0-9]+)\.([0-9]+)-rc\.([0-9]+)$/\1 \2 \3 \4 &/' \
    | sort -k1,1n -k2,2n -k3,3n -k4,4n | tail -1 | awk '{print $5}'
}

bump_version() {
  local version="$1" type="$2" major minor patch
  IFS='.' read -r major minor patch <<< "$version"
  case "$type" in
    patch) patch=$((patch + 1)) ;;
    minor) minor=$((minor + 1)); patch=0 ;;
    major) major=$((major + 1)); minor=0; patch=0 ;;
  esac
  echo "${major}.${minor}.${patch}"
}

tag_commit() { git rev-list -n 1 "$1"; }

# version_gt <a> <b> — true when semver a > b (both X.Y.Z).
version_gt() {
  [[ "$1" != "$2" ]] && [[ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -1)" == "$1" ]]
}

ensure_main() {
  if [[ "$CURRENT_BRANCH" != "main" ]]; then
    echo "❌ Release tags are created on 'main' only (current branch: ${CURRENT_BRANCH})." >&2
    echo "   Merge your PR first, then: git checkout main && git pull --ff-only" >&2
    exit 1
  fi
}

ensure_main_matches_origin() {
  local origin_sha
  origin_sha="$(git rev-parse origin/main 2>/dev/null || true)"
  if [[ -z "$origin_sha" ]]; then
    echo "⚠️  Could not resolve origin/main — skipping the sync check." >&2
    return 0
  fi
  [[ "$HEAD_SHA" == "$origin_sha" ]] && return 0
  if git merge-base --is-ancestor "$HEAD_SHA" "$origin_sha" 2>/dev/null; then
    echo "❌ Local main (${HEAD_SHA:0:7}) is behind origin/main (${origin_sha:0:7}): git pull --ff-only" >&2
  elif git merge-base --is-ancestor "$origin_sha" "$HEAD_SHA" 2>/dev/null; then
    echo "❌ Local main (${HEAD_SHA:0:7}) is ahead of origin/main (${origin_sha:0:7}) — main is PR-only, open a PR instead." >&2
  else
    echo "❌ Local main (${HEAD_SHA:0:7}) has diverged from origin/main (${origin_sha:0:7}): git fetch origin && git reset --hard origin/main" >&2
  fi
  exit 1
}

ensure_clean_tree() {
  if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "❌ Working tree has uncommitted changes — a tag must point at a commit CI can reproduce." >&2
    exit 1
  fi
}

confirm() {
  local prompt="$1" answer
  [[ "$ASSUME_YES" == "true" ]] && return 0
  read -r -p "${prompt} [y/N] " answer </dev/tty
  [[ "$answer" == "y" || "$answer" == "Y" ]]
}

create_and_push_tag() {
  local tag_name="$1" target_commit="$2" message="$3"
  if git rev-parse -q --verify "refs/tags/${tag_name}" >/dev/null; then
    echo "❌ Tag '${tag_name}' already exists (commit $(tag_commit "$tag_name" | cut -c1-7))." >&2
    exit 1
  fi
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "(dry-run) would run:"
    echo "  git tag -a \"${tag_name}\" \"${target_commit}\" -m \"${message}\""
    echo "  git push origin \"${tag_name}\""
    return 0
  fi
  confirm "Create tag '${tag_name}' on ${target_commit:0:7}?" || { echo "Aborted."; exit 1; }
  git tag -a "$tag_name" "$target_commit" -m "$message"
  echo "✅ Created ${tag_name}"
  if confirm "Push '${tag_name}' to origin now? This starts the release workflow."; then
    git push origin "$tag_name"
    echo "🚀 Pushed ${tag_name} — follow the run: gh run list --workflow release-bootstrap-installers.yml"
  else
    echo "ℹ️  Tag created locally only. Push later with: git push origin ${tag_name}"
  fi
}

# ── main ─────────────────────────────────────────────────────────────────────

refresh_refs
STABLE="$(highest_stable)"
STABLE_VERSION="${STABLE#v}"
CANDIDATE="$(highest_candidate)"

if [[ "$MODE" == "status" ]]; then
  echo "branch:            ${CURRENT_BRANCH} (${HEAD_SHA:0:7})"
  echo "highest stable:    ${STABLE:-<none>}"
  echo "highest candidate: ${CANDIDATE:-<none>}"
  exact="$(git describe --tags --exact-match HEAD 2>/dev/null || true)"
  echo "HEAD tag:          ${exact:-<untagged>}"
  exit 0
fi

ensure_main
ensure_clean_tree
ensure_main_matches_origin

if [[ "$MODE" == "bump" ]]; then
  base="${STABLE_VERSION:-0.0.0}"
  # A candidate above the highest stable tag continues its version; the bump
  # type only matters when a NEW version is opened.
  target=""
  if [[ -n "$CANDIDATE" ]]; then
    cand_version="$(echo "$CANDIDATE" | sed -E 's/^v([0-9]+\.[0-9]+\.[0-9]+)-rc\.[0-9]+$/\1/')"
    if version_gt "$cand_version" "$base"; then
      target="$cand_version"
      echo "ℹ️  Open candidate line ${cand_version} (${CANDIDATE}); ignoring bump type '${BUMP_TYPE}'."
    fi
  fi
  [[ -z "$target" ]] && target="$(bump_version "$base" "$BUMP_TYPE")"
  next_rc=$(( $(highest_rc_for "$target") + 1 ))
  if [[ $next_rc -gt 1 ]]; then
    prev="v${target}-rc.$((next_rc - 1))"
    if [[ "$(tag_commit "$prev")" == "$HEAD_SHA" ]]; then
      echo "ℹ️  HEAD already is ${prev} — nothing new to tag."
      exit 0
    fi
  fi
  tag="v${target}-rc.${next_rc}"
  echo "📦 Release candidate: ${tag} (highest stable: ${STABLE:-none}) on ${HEAD_SHA:0:7}"
  create_and_push_tag "$tag" "$HEAD_SHA" "Release candidate ${tag} (from main ${HEAD_SHA:0:7})"
  exit 0
fi

# promote stable
if [[ -z "$CANDIDATE" ]]; then
  echo "❌ No release candidate tag found. Cut one first: ./createTag.sh patch" >&2
  exit 1
fi
cand_version="$(echo "$CANDIDATE" | sed -E 's/^v([0-9]+\.[0-9]+\.[0-9]+)-rc\.[0-9]+$/\1/')"
if [[ -n "$STABLE_VERSION" ]] && ! version_gt "$cand_version" "$STABLE_VERSION"; then
  echo "❌ Highest candidate ${CANDIDATE} is not above the highest stable ${STABLE}. Cut a new candidate first." >&2
  exit 1
fi
target_commit="$(tag_commit "$CANDIDATE")"
if [[ "$target_commit" != "$HEAD_SHA" ]]; then
  echo "ℹ️  ${CANDIDATE} points at ${target_commit:0:7}; main HEAD is ${HEAD_SHA:0:7} (newer commits are NOT part of this release)."
fi
tag="v${cand_version}"
echo "🏷  Promote ${CANDIDATE} → ${tag} on ${target_commit:0:7}"
create_and_push_tag "$tag" "$target_commit" "Stable release ${tag} (promoted from ${CANDIDATE})"
