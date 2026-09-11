# Releases: candidate from main, promote to stable (AIS-292)

`main` is pull-request only. Releases are **git tags**; nothing is committed to
`main` to release. One script creates the tags, the "Release Bootstrap
Installers" workflow reacts to the push.

| Tag | Meaning | Workflow | Update channel |
|---|---|---|---|
| `vX.Y.Z-rc.N` | release candidate cut from `main` HEAD | builds the installers, publishes a GitHub **pre-release** | `preview` |
| `vX.Y.Z` | stable release, promoted from a candidate | **no rebuild** — re-publishes the candidate's artifacts as the **latest** release | `stable` |

```bash
./createTag.sh status            # highest stable, highest candidate, what HEAD is
./createTag.sh patch             # main only → v0.7.5-rc.1 (minor/major bump the next segment)
./createTag.sh patch             # after more merges → v0.7.5-rc.2
./createTag.sh promote stable    # → v0.7.5 on exactly the commit of v0.7.5-rc.2
./createTag.sh ... --dry-run     # show, don't tag
```

Rules enforced by the script: `main` only, local `main` identical to
`origin/main`, clean tree, annotated tags, confirmation before tag and before
push. A candidate line stays open until it is promoted — `./createTag.sh minor`
while `v0.7.5-rc.1` exists creates `v0.7.5-rc.2`, not `v0.8.0-rc.1`.

## What the workflow does

- **Candidate tag push**: verifies the tag points at the current `main` HEAD
  (the build checks out `main` and the Azure signing credential is bound to
  it), re-dispatches itself on `main` with `version=X.Y.Z-rc.N` and
  `prerelease=true`. `scripts/set_version.py` stamps the version into the
  build workspace only. Release notes are generated from the commits since the
  previous release.
- **Stable tag push**: finds the candidate release of the same version whose
  tag points at the same commit, downloads its assets, creates the `vX.Y.Z`
  release (latest, not pre-release) with those assets and the notes since the
  previous stable release. If no matching candidate exists the job fails —
  promote a candidate, do not tag `main` directly.
- `workflow_dispatch` remains as a fallback: `version` is required (`X.Y.Z` or
  `X.Y.Z-rc.N`), `prerelease` marks the release.

## What the client does

Update channel (`Settings → Advanced → Update channel`, `hermes update
--branch <channel>`, config key `updates.channel`):

- `stable` (default for installed clients; legacy alias `tags`) — the highest
  `vX.Y.Z` tag.
- `preview` — the highest tag including candidates; a stable tag outranks the
  candidates of its own version.
- `main` — the branch, for developers.

Which channel a bare `hermes update` follows (AIS-299): `--branch` wins, then
`updates.channel` in `config.yaml` (the desktop writes it when you change the
channel), then `auto` — `stable` on a detached checkout (installed clients sit
on a release tag), `main` on a named branch (developer checkouts). Fresh
`install.sh` runs check out the highest stable tag unless `--branch`/`--commit`
say otherwise.

The check compares the checkout with the channel's tag and lists the commits
in between (that is the changelog in the updates overlay); the update checks
the tag out **and then runs the same post-update pipeline as a branch pull**
(dependencies, bytecode cache, skills sync, config migration, desktop rebuild
check). A checkout sitting exactly on a release tag reports that version
(`hermes --version`, Desktop, `/api/status`) even though `pyproject.toml` on
`main` still carries the previous number.

### Update source: git or release archives (AIS-312)

`hermes update` has two transports for the same target (channel + tag):

- **git** — `origin` of the checkout (needs access to the source repository).
- **release** — the verified `hermes-source-<ver>.zip` of the public release
  repository (see below). Only `stable` and `preview`; the `main` channel is
  git-only. The tree is swapped in with a rollback, the syntax of the critical
  files is verified before the swap counts, and the same post-update pipeline
  runs. `venv`, `node_modules`, `.git`, `.env`, `.worktrees` are never touched.

Config key `updates.source` (`Settings → General → Update source`, CLI
`--source`): `auto` (default), `git`, `release`. `auto` resolves per run:

1. The install carries `.hermes-release.json` → release.
2. No `.git` → release (a source tree) or the pip path.
3. `.git` and `git ls-remote origin HEAD` answers → **git** (existing
   installations keep behaving exactly as before; the release repository is
   not contacted at all).
4. `.git` but the origin does not answer → release.

**No update deadlock:** in `auto` mode a release manifest that cannot be
served (empty mirror, 404, network) is reported with a warning and the update
continues on the previous path — git when `.git` exists, otherwise the source
repository's tag archive (`archive/refs/tags/<tag>.zip`, the former Windows
ZIP fallback). Only `updates.source: release` has no fallback and fails
loudly. A failure *after* the archive download started (checksum, extraction,
verification) is always fatal and leaves the tree unchanged; it is never
retried through another transport. The same order applies to
`hermes update --check`, the CLI banner and the desktop update check.

**Target resolution on git checkouts (AIS-318).** `stable`/`preview` take
their target from the public release repository first: `hermes-release.json`
names the tag and the `commit_sha`. Where `origin` carries that tag the update
stays a `git checkout <tag>` (the tag's commit must equal `commit_sha`, a
re-pointed tag is refused). Where `origin` cannot deliver the tag — private
or unreachable source repository — the release archive is installed instead
and the marker written. When the manifest cannot be served the tag is
resolved from `origin` as before, announced with a warning. The desktop
`checkUpdates()` follows the same order (manifest, then `ls-remote --tags`).

**Version shown for a checkout past a tag.** `hermes --version`, the banner,
`/api/status` and the desktop report `<highest release tag reachable from
HEAD>+<commits since>` (e.g. `0.7.5+6`, stable above the candidates of its
own version); exactly on a tag only the version. `pyproject.toml` is the last
fallback only.

Manifest resolution: `stable` reads the static
`releases/latest/download/hermes-release.json` (no API call, not rate
limited); `preview` lists the newest releases via the API, picks the highest
tag (stable above its candidates) and reads that release's manifest asset.
The manifest must match the release it came from, the channel, and the
`hermes-source-<version>.zip` naming; `commit_sha`/`sha256` are validated
before anything is downloaded.

**Release marker** `<install>/.hermes-release.json` (`hermes-release-marker-v1`:
channel, tag, version, commit_sha, sha256, build_id, applied_at) is written
after every archive update and is the version identity of such an install:
`hermes --version`, the desktop, `detect_install_method()` (→ `release`) and
the update checks read it *before* git — after a tree swap `git describe`
would still name the old tag (the phantom-update loop of AIS-297). A marker
whose commit equals the manifest's is "up to date" even across an rc →
stable promotion. The marker is removed again when git or the source-archive
fallback update the tree.

What a tag channel reports, by where HEAD sits (`hermes update --check`,
desktop status bar / updates overlay):

| HEAD | stable channel (target `v0.7.4`) | preview channel (target `v0.7.5-rc.2`) |
|---|---|---|
| on the target tag | up to date | up to date |
| behind the target | update available (+N commits, changelog) | update available |
| on `v0.7.5-rc.1` (release tag newer than stable) | up to date — "newer than stable v0.7.4", **no** offer, never a downgrade | update to `v0.7.5-rc.2` |
| on the commit of `v0.7.5-rc.2` after `promote stable` → `v0.7.5` | up to date (same commit) | up to date |
| untagged commit past the tag (dev / `main` checkout) | "development checkout, N commits ahead" — offer to switch to the release (AIS-297) | same |
| channel has no tag yet | "no release tag yet" — `hermes update` would follow `main` | same |

Note for the first update after this landed: the running `hermes update` is
still the old code, so that one tag checkout skips the post-update pipeline
(the desktop's updater rebuilds the app regardless); from the next update on,
the pipeline runs.

Version files on `main` (`pyproject.toml`, `acp_registry/agent.json`, …) are
not bumped per release any more; bump them occasionally with
`python scripts/set_version.py <x.y.z>` in a normal PR when a new minor line
starts, so PyPI-style installs and `importlib.metadata` stay close to reality.

## Public release repository (AIS-311)

This repository is going private. Clients cannot read releases of a private
repository, so every release is mirrored into the public
**`IAMDS-GMBH/AIMDS-Agent-Releases`** (README plus releases, no code, no
issues). The mirror is the download source for installers and the source package; the client updater switched to it in AIS-312
(`updates.source`, see above), the installer follows in AIS-313, the cutover
itself is AIS-314.

### What the workflow publishes

`build-source-package` (workflow_dispatch, same main HEAD as the installers)
runs `scripts/build_source_package.sh <version> <out-dir>`:

| Asset | Content |
|---|---|
| `hermes-source-<version>.zip` | `git archive` of HEAD with the version stamped by `scripts/set_version.py`; root folder `hermes-agent-<version>/`; the `export-ignore` rules in `.gitattributes` keep everything out that the installed client does not need to run, build its UIs or update itself — `.github/`, `.agents/`, `tests/`, `apps/bootstrap-installer/`, `docs/`, `docker/` + `Dockerfile`, `assets/` (README screenshots), PR/design material and the developer/CI scripts under `scripts/` (AIS-322). `scripts/install.sh` / `install.ps1` travel inside the archive; they are not separate assets — the installer (AIS-313) takes them from the verified archive |
| `hermes-source-<version>.zip.sha256` | checksum, `sha256sum -c` format |
| `hermes-release.json` | `{"format": "hermes-release-v1", "version", "tag", "commit_sha", "source_archive", "sha256", "size", "built_at"}` — the manifest the client updater reads |

`publish-release-assets` uploads these next to the installers on the internal
release, then mirrors the release: same tag, `--prerelease` for candidates,
`--latest` for stable, title `Hermes <tag>`, and **public notes = the AI
summary only** (never the commit bullets — they carry commit hashes and author
logins). `promote-stable` mirrors the promoted `vX.Y.Z` with the candidate's
assets and the candidate's public notes. The tag in the public repository is
created by `gh release create --target <default branch>`; it points at the
README commit, not at source.

Download URL scheme (anonymous, no token):

```
https://github.com/IAMDS-GMBH/AIMDS-Agent-Releases/releases/download/<tag>/<asset>
```

### Setup (once, org admin)

1. **GitHub App** `AIMDS Release Publisher` (Organization → Settings →
   Developer settings → GitHub Apps → New): no webhook, permission
   **Repository → Contents: Read and write** only; install it on
   `AIMDS-Agent-Releases` **only**. Generate a private key.
2. In this repository: secrets `RELEASES_APP_ID` (the App ID) and
   `RELEASES_APP_PRIVATE_KEY` (the `.pem` content); variable
   `RELEASES_REPO=IAMDS-GMBH/AIMDS-Agent-Releases`.
3. The workflow mints a short-lived installation token with
   `actions/create-github-app-token` scoped to that one repository. While the
   secrets are missing, the "Check public release mirror configuration" step
   prints a warning and the mirror is skipped — the internal release is
   unaffected.

To rehearse, point `RELEASES_REPO` at a sandbox repository the app is
installed on, run `./createTag.sh patch`, and check
`gh release view <tag> --repo <sandbox>`; then switch the variable back.

### Local check of the source package

```bash
scripts/build_source_package.sh 0.7.6-rc.1 "$TMPDIR/hermes-pkg"   # clean tree required
unzip -l "$TMPDIR/hermes-pkg/hermes-source-0.7.6-rc.1.zip" | grep -E '\.github/|tests/|bootstrap-installer/' && echo "LEAK" || echo "clean"
(cd "$TMPDIR/hermes-pkg" && shasum -a 256 -c hermes-source-0.7.6-rc.1.zip.sha256)
git status --short   # empty: the version stamp was reverted
```
