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
`main` still carries the previous number. The Windows ZIP fallback resolves the
channel's tag through the GitHub Releases API and downloads the tag archive.

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
issues). The mirror is the download source for installers, install scripts
and the source package; the client updater and installer switch to it in
AIS-218 (updater) and AIS-313 (installer), the cutover itself is AIS-314.

### What the workflow publishes

`build-source-package` (workflow_dispatch, same main HEAD as the installers)
runs `scripts/build_source_package.sh <version> <out-dir>`:

| Asset | Content |
|---|---|
| `hermes-source-<version>.zip` | `git archive` of HEAD with the version stamped by `scripts/set_version.py`; root folder `hermes-agent-<version>/`; the `export-ignore` rules in `.gitattributes` keep `.github/`, `.agents/`, `tests/`, `apps/bootstrap-installer/`, `docs/plans/`, `createTag.sh` and `CONTRIBUTING.md` out — nothing that describes internal workflows ships |
| `hermes-source-<version>.zip.sha256` | checksum, `sha256sum -c` format |
| `hermes-release.json` | `{"format": "hermes-release-v1", "version", "tag", "commit_sha", "source_archive", "sha256", "size", "built_at"}` — the manifest the client updater reads |
| `install.sh`, `install.ps1` | the install scripts of this release (the bootstrap installer fetches them from here instead of `raw.githubusercontent.com`) |

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
