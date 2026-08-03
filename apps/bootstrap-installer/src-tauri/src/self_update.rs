//! Self-update for the STAGED installer binary (`paths::installer_dest()`).
//!
//! Background: `paths::copy_self_to_hermes_home()` only copies the running
//! installer to `HERMES_HOME/hermes-setup.exe` on a fresh full install, and
//! deliberately no-ops when already running from that path (true for every
//! `--update` re-invocation — see that function's doc comment for why:
//! copying onto the exe you're currently executing from is a Windows sharing
//! violation). Consequence: the staged binary is frozen forever after the
//! very first install, so bootstrap-installer fixes never reach clients that
//! already went through onboarding once, no matter how many times `main` is
//! patched — only a from-scratch reinstall would pick them up.
//!
//! This module closes that gap: on every `--update` run, before the normal
//! update flow proceeds, check whether a newer bootstrap-installer release
//! exists on GitHub and, if so, download it to a temp path, atomically swap
//! it into `installer_dest()`, and re-exec `--update` against the fresh
//! binary (exiting the stale one). Best-effort throughout — any failure here
//! must fall through to the existing (stale-binary) update flow rather than
//! blocking it; a client stuck on an old installer version is the status
//! quo, not a regression.
//!
//! Deliberately NOT implemented (see plan discussion): signature/checksum
//! verification of the downloaded asset, and any throttling/caching of the
//! check — it runs on every `--update` invocation.

use anyhow::{anyhow, Context, Result};
use std::path::{Path, PathBuf};
use tokio::io::AsyncWriteExt;

use crate::paths;

const REPO: &str = "IAMDS-GMBH/hermes-agent";

/// Our own version, baked in at compile time from Cargo.toml.
const CURRENT_VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Debug, serde::Deserialize)]
struct ReleaseAsset {
    name: String,
    browser_download_url: String,
}

#[derive(Debug, serde::Deserialize)]
struct ReleaseInfo {
    tag_name: String,
    assets: Vec<ReleaseAsset>,
}

/// Which OS-specific asset filename to look for in the release, mirroring
/// `release-bootstrap-installers.yml`'s `HermesSetup.{exe,dmg,AppImage,deb,rpm}`
/// naming (the non-`-debug` variant).
fn asset_name_for_os(os: &str) -> &'static str {
    match os {
        "windows" => "HermesSetup.exe",
        "macos" => "HermesSetup.dmg",
        _ => "HermesSetup.AppImage",
    }
}

/// Parses a `major.minor.patch`-shaped version string into a comparable
/// tuple. Non-numeric or missing components default to 0, so this never
/// panics on unexpected input (e.g. a stray `-beta` suffix) — it just
/// compares what it can parse, which is good enough for "is remote newer".
fn parse_version(v: &str) -> (u64, u64, u64) {
    let v = v.strip_prefix('v').unwrap_or(v);
    let mut parts = v.split('.').map(|p| {
        p.chars()
            .take_while(|c| c.is_ascii_digit())
            .collect::<String>()
            .parse::<u64>()
            .unwrap_or(0)
    });
    (
        parts.next().unwrap_or(0),
        parts.next().unwrap_or(0),
        parts.next().unwrap_or(0),
    )
}

fn is_newer(remote: &str, local: &str) -> bool {
    parse_version(remote) > parse_version(local)
}

/// Picks the download URL for `asset_name` out of a release's asset list.
fn pick_asset<'a>(assets: &'a [ReleaseAsset], asset_name: &str) -> Option<&'a ReleaseAsset> {
    assets.iter().find(|a| a.name == asset_name)
}

/// Fetches the newest release (by creation order, NOT the semver-highest tag)
/// from `/repos/{REPO}/releases?per_page=1`. We deliberately don't use
/// `/releases/latest` — every bootstrap-installer release is created with
/// `--prerelease` (see release-bootstrap-installers.yml) and GitHub's
/// `/latest` endpoint excludes prereleases and drafts, so it would never
/// return anything for this repo's installer releases.
async fn fetch_newest_release() -> Result<ReleaseInfo> {
    let url = format!("https://api.github.com/repos/{REPO}/releases?per_page=1");
    let response = reqwest::Client::new()
        .get(&url)
        .header("User-Agent", "hermes-setup/0.0.1")
        .header("Accept", "application/vnd.github+json")
        .send()
        .await
        .with_context(|| format!("GET {url}"))?;

    if !response.status().is_success() {
        return Err(anyhow!(
            "Failed to list releases: HTTP {} from {}",
            response.status(),
            url
        ));
    }

    let mut releases: Vec<ReleaseInfo> = response
        .json()
        .await
        .with_context(|| format!("parsing releases JSON from {url}"))?;

    if releases.is_empty() {
        return Err(anyhow!("no releases found for {REPO}"));
    }
    Ok(releases.remove(0))
}

/// Downloads `url` to `dest_path` via a `.tmp` sibling + atomic rename, same
/// pattern as `install_script.rs::download`.
async fn download_asset(url: &str, dest_path: &Path) -> Result<()> {
    if let Some(parent) = dest_path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("creating cache dir {}", parent.display()))?;
    }

    let tmp_path = dest_path.with_extension({
        let ext = dest_path
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("tmp");
        format!("{ext}.tmp")
    });

    let response = reqwest::Client::new()
        .get(url)
        .header("User-Agent", "hermes-setup/0.0.1")
        .send()
        .await
        .with_context(|| format!("GET {url}"))?;

    if !response.status().is_success() {
        return Err(anyhow!(
            "Failed to download installer asset: HTTP {} from {}",
            response.status(),
            url
        ));
    }

    let bytes = response
        .bytes()
        .await
        .with_context(|| format!("reading body of {url}"))?;

    let mut file = tokio::fs::File::create(&tmp_path)
        .await
        .with_context(|| format!("creating temp file {}", tmp_path.display()))?;
    file.write_all(&bytes)
        .await
        .with_context(|| format!("writing temp file {}", tmp_path.display()))?;
    file.flush().await.context("flushing temp file")?;
    drop(file);

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = tokio::fs::metadata(&tmp_path).await?.permissions();
        perms.set_mode(0o755);
        tokio::fs::set_permissions(&tmp_path, perms).await?;
    }

    tokio::fs::rename(&tmp_path, dest_path)
        .await
        .with_context(|| format!("renaming {} -> {}", tmp_path.display(), dest_path.display()))?;

    Ok(())
}

/// Checks GitHub for a newer bootstrap-installer release than
/// `CURRENT_VERSION`; if found, downloads it, swaps it into
/// `paths::installer_dest()`, and returns the path to the freshly-staged
/// binary so the caller can re-exec `--update` against it and exit.
///
/// Returns `Ok(None)` when already up to date (the normal case) — the
/// caller should just proceed with today's stale-binary update flow.
/// Errors are non-fatal by contract: callers must log and continue rather
/// than fail the update on a network hiccup.
pub async fn check_and_maybe_replace() -> Result<Option<PathBuf>> {
    let release = fetch_newest_release().await?;

    if !is_newer(&release.tag_name, CURRENT_VERSION) {
        tracing::info!(
            remote = %release.tag_name,
            local = %CURRENT_VERSION,
            "installer already up to date; skipping self-update"
        );
        return Ok(None);
    }

    let os = if cfg!(target_os = "windows") {
        "windows"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    };
    let asset_name = asset_name_for_os(os);
    let asset = pick_asset(&release.assets, asset_name).ok_or_else(|| {
        anyhow!(
            "release {} has no asset named {asset_name} for this OS",
            release.tag_name
        )
    })?;

    tracing::info!(
        remote = %release.tag_name,
        local = %CURRENT_VERSION,
        asset = %asset.name,
        "newer bootstrap-installer release found; downloading"
    );

    let dest = paths::installer_dest();
    let tmp_download = paths::bootstrap_cache_dir().join(format!("hermes-setup.new-{}", asset.name));
    download_asset(&asset.browser_download_url, &tmp_download).await?;

    // Atomic rename over the stable staged path. Safe here (unlike a
    // self-copy from `current_exe()`) because we are not the file we're
    // replacing — the process currently executing is the OLD staged binary
    // running from `dest`, and Windows allows renaming a file that isn't the
    // one backing a running process's mapped image as long as it's a
    // separate temp file being moved on top, not an in-place rewrite of the
    // open handle's own bytes. If this ever proves unreliable in practice on
    // Windows, fall back to renaming the old one aside first.
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("creating {}", parent.display()))?;
    }
    tokio::fs::rename(&tmp_download, &dest)
        .await
        .with_context(|| format!("staging new installer at {}", dest.display()))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = tokio::fs::metadata(&dest).await?.permissions();
        perms.set_mode(0o755);
        tokio::fs::set_permissions(&dest, perms).await?;
    }

    tracing::info!(?dest, version = %release.tag_name, "staged newer installer binary");
    Ok(Some(dest))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_plain_versions() {
        assert_eq!(parse_version("0.7.1"), (0, 7, 1));
        assert_eq!(parse_version("v0.7.1"), (0, 7, 1));
        assert_eq!(parse_version("1.2.3"), (1, 2, 3));
    }

    #[test]
    fn parses_partial_and_malformed_versions_without_panicking() {
        assert_eq!(parse_version("0.7"), (0, 7, 0));
        assert_eq!(parse_version("0.7.1-beta.2"), (0, 7, 1));
        assert_eq!(parse_version("garbage"), (0, 0, 0));
        assert_eq!(parse_version(""), (0, 0, 0));
    }

    #[test]
    fn numeric_compare_beats_lexicographic_pitfalls() {
        // Lexicographic string compare would say "0.7.10" < "0.7.2" (wrong);
        // numeric tuple compare must get this right.
        assert!(is_newer("v0.7.10", "0.7.2"));
        assert!(!is_newer("v0.7.2", "0.7.10"));
    }

    #[test]
    fn is_newer_true_only_when_strictly_greater() {
        assert!(is_newer("v0.7.2", "0.7.1"));
        assert!(!is_newer("v0.7.1", "0.7.1"));
        assert!(!is_newer("v0.7.0", "0.7.1"));
    }

    #[test]
    fn asset_name_matches_release_workflow_naming() {
        assert_eq!(asset_name_for_os("windows"), "HermesSetup.exe");
        assert_eq!(asset_name_for_os("macos"), "HermesSetup.dmg");
        assert_eq!(asset_name_for_os("linux"), "HermesSetup.AppImage");
        assert_eq!(asset_name_for_os("some-other-unix"), "HermesSetup.AppImage");
    }

    #[test]
    fn pick_asset_finds_exact_name_match() {
        let assets = vec![
            ReleaseAsset {
                name: "HermesSetup.exe".into(),
                browser_download_url: "https://example.com/exe".into(),
            },
            ReleaseAsset {
                name: "HermesSetup-debug.exe".into(),
                browser_download_url: "https://example.com/debug-exe".into(),
            },
            ReleaseAsset {
                name: "HermesSetup.dmg".into(),
                browser_download_url: "https://example.com/dmg".into(),
            },
        ];

        let found = pick_asset(&assets, "HermesSetup.exe").expect("asset present");
        assert_eq!(found.browser_download_url, "https://example.com/exe");

        assert!(pick_asset(&assets, "HermesSetup.AppImage").is_none());
    }

    #[test]
    fn release_info_deserializes_from_github_api_shape() {
        let json = r#"{
            "tag_name": "v0.7.3",
            "prerelease": true,
            "assets": [
                {"name": "HermesSetup.exe", "browser_download_url": "https://example.com/a.exe"},
                {"name": "HermesSetup.dmg", "browser_download_url": "https://example.com/a.dmg"}
            ]
        }"#;
        let parsed: ReleaseInfo = serde_json::from_str(json).expect("valid release JSON");
        assert_eq!(parsed.tag_name, "v0.7.3");
        assert_eq!(parsed.assets.len(), 2);
    }
}
