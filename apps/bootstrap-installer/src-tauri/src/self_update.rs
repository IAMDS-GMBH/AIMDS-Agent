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
//!
//! AIS-346 (SUP-20260915-125435): the release assets are *installer
//! packages*, not bare binaries — on macOS `HermesSetup.dmg` is a disk image.
//! Renaming that image onto `installer_dest()` bricked every macOS client's
//! GUI update (`spawn ENOEXEC` on the next hand-off). So now:
//!
//! * a DMG is mounted and the installer binary is extracted from its
//!   `*.app/Contents/MacOS/` (`extract_from_dmg`),
//! * whatever is about to be staged must carry this OS's executable magic
//!   (Mach-O / ELF / MZ) — anything else is discarded and the old installer
//!   stays (`ensure_native_executable`),
//! * the previous installer is kept as `hermes-setup.prev` and restored when
//!   the relaunch of the new one fails (`restore_previous`),
//! * the release tag of the staged binary is remembered in a sidecar file so
//!   a self-updated installer (whose compiled `CARGO_PKG_VERSION` never
//!   changes) does not download the same release on every update.

use anyhow::{anyhow, Context, Result};
use std::path::{Path, PathBuf};
use tokio::io::AsyncWriteExt;

use crate::paths;

// AIS-313: installer binaries are published (mirrored) in the public release
// repository; the source repository is private. AIS-323 keeps the source
// repository as an emergency fallback (it can be made public again without
// losing clients) — every use of it is logged.
const REPO: &str = "IAMDS-GMBH/AIMDS-Agent-Releases";
const FALLBACK_REPO: &str = "IAMDS-GMBH/AIMDS-Agent";

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

/// Sidecar next to the staged installer that records the release tag it was
/// taken from (`hermes-setup.release-tag`). `CARGO_PKG_VERSION` is baked in at
/// build time and does not change when the binary is swapped, so without this
/// a self-updated installer would fetch the same release again on every run.
fn staged_tag_path(dest: &Path) -> PathBuf {
    dest.with_extension("release-tag")
}

/// Path the previous installer is parked at while a new one is staged.
fn previous_path(dest: &Path) -> PathBuf {
    dest.with_extension("prev")
}

fn read_staged_tag(dest: &Path) -> Option<String> {
    let raw = std::fs::read_to_string(staged_tag_path(dest)).ok()?;
    let tag = raw.trim();
    (!tag.is_empty()).then(|| tag.to_string())
}

/// The version the staged installer effectively has: the compiled version or
/// the sidecar's release tag, whichever is higher.
fn effective_local_version(dest: &Path, compiled: &str) -> String {
    match read_staged_tag(dest) {
        Some(tag) if is_newer(&tag, compiled) => tag,
        _ => compiled.to_string(),
    }
}

/// The executable magic this OS's loader expects at the start of a binary.
/// A downloaded *package* (DMG, zip, an HTML error page) never passes.
fn is_native_executable_for(os: &str, head: &[u8]) -> bool {
    if head.len() < 4 {
        return false;
    }
    match os {
        "windows" => head.starts_with(b"MZ"),
        "macos" => matches!(
            head[..4],
            // Mach-O 64/32-bit, both byte orders, and fat/universal binaries.
            [0xcf, 0xfa, 0xed, 0xfe]
                | [0xce, 0xfa, 0xed, 0xfe]
                | [0xfe, 0xed, 0xfa, 0xcf]
                | [0xfe, 0xed, 0xfa, 0xce]
                | [0xca, 0xfe, 0xba, 0xbe]
                | [0xbe, 0xba, 0xfe, 0xca]
        ),
        _ => head.starts_with(b"\x7fELF"),
    }
}

fn current_os() -> &'static str {
    if cfg!(target_os = "windows") {
        "windows"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        "linux"
    }
}

/// Refuses to stage anything that is not a native executable for this OS.
async fn ensure_native_executable(path: &Path) -> Result<()> {
    let bytes = tokio::fs::read(path)
        .await
        .with_context(|| format!("reading {}", path.display()))?;
    let head: Vec<u8> = bytes.iter().take(8).copied().collect();
    if is_native_executable_for(current_os(), &head) {
        return Ok(());
    }
    Err(anyhow!(
        "{} is not a {} executable (magic {:02x?}); refusing to stage it as the installer",
        path.display(),
        current_os(),
        head
    ))
}

/// The installer binary inside a mounted DMG: the first Mach-O file under any
/// `*.app/Contents/MacOS/` directory (the Tauri bundle carries exactly one).
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn find_installer_in_bundle_root(root: &Path) -> Option<PathBuf> {
    let apps = std::fs::read_dir(root).ok()?;
    let mut bundles: Vec<PathBuf> = apps
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().and_then(|e| e.to_str()) == Some("app") && p.is_dir())
        .collect();
    bundles.sort();
    for bundle in bundles {
        let macos_dir = bundle.join("Contents").join("MacOS");
        let Ok(entries) = std::fs::read_dir(&macos_dir) else {
            continue;
        };
        let mut files: Vec<PathBuf> = entries.flatten().map(|e| e.path()).filter(|p| p.is_file()).collect();
        files.sort();
        for file in files {
            let Ok(mut handle) = std::fs::File::open(&file) else {
                continue;
            };
            use std::io::Read;
            let mut head = [0u8; 8];
            let n = handle.read(&mut head).unwrap_or(0);
            if is_native_executable_for("macos", &head[..n]) {
                return Some(file);
            }
        }
    }
    None
}

/// Mounts `dmg` read-only, copies the installer binary out of its `.app`
/// bundle to `out`, and detaches the image again (macOS only).
#[cfg(target_os = "macos")]
async fn extract_from_dmg(dmg: &Path, out: &Path) -> Result<()> {
    let mount = paths::bootstrap_cache_dir().join(format!("hermes-setup-dmg-{}", std::process::id()));
    tokio::fs::create_dir_all(&mount)
        .await
        .with_context(|| format!("creating mount point {}", mount.display()))?;
    let attach = tokio::process::Command::new("/usr/bin/hdiutil")
        .args(["attach", "-nobrowse", "-readonly", "-noverify", "-noautoopen", "-mountpoint"])
        .arg(&mount)
        .arg(dmg)
        .output()
        .await
        .context("running hdiutil attach")?;
    if !attach.status.success() {
        let _ = tokio::fs::remove_dir(&mount).await;
        return Err(anyhow!(
            "hdiutil attach failed for {}: {}",
            dmg.display(),
            String::from_utf8_lossy(&attach.stderr).trim()
        ));
    }
    let result = match find_installer_in_bundle_root(&mount) {
        Some(binary) => tokio::fs::copy(&binary, out)
            .await
            .map(|_| ())
            .with_context(|| format!("copying {} -> {}", binary.display(), out.display())),
        None => Err(anyhow!(
            "{} contains no *.app/Contents/MacOS/ Mach-O binary",
            dmg.display()
        )),
    };
    let _ = tokio::process::Command::new("/usr/bin/hdiutil")
        .args(["detach", "-force"])
        .arg(&mount)
        .output()
        .await;
    let _ = tokio::fs::remove_dir(&mount).await;
    result
}

#[cfg(not(target_os = "macos"))]
async fn extract_from_dmg(dmg: &Path, _out: &Path) -> Result<()> {
    Err(anyhow!("{} is a macOS disk image; not applicable on this OS", dmg.display()))
}

/// Puts the previous installer back after a failed relaunch of the new one
/// (best-effort; the caller logs). Returns `true` when a restore happened.
pub fn restore_previous() -> bool {
    let dest = paths::installer_dest();
    let prev = previous_path(&dest);
    if !prev.is_file() {
        return false;
    }
    let _ = std::fs::remove_file(&dest);
    match std::fs::rename(&prev, &dest) {
        Ok(()) => {
            let _ = std::fs::remove_file(staged_tag_path(&dest));
            tracing::warn!(?dest, "restored the previous installer binary");
            true
        }
        Err(err) => {
            tracing::warn!(%err, ?prev, ?dest, "could not restore the previous installer binary");
            false
        }
    }
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
    match fetch_newest_release_from(REPO).await {
        Ok(release) => Ok(release),
        Err(primary_err) => {
            tracing::warn!(
                %primary_err,
                "release repository {REPO} unavailable for the installer self-update; falling back to {FALLBACK_REPO}"
            );
            fetch_newest_release_from(FALLBACK_REPO)
                .await
                .map_err(|fallback_err| anyhow!("{primary_err}; fallback {FALLBACK_REPO}: {fallback_err}"))
        }
    }
}

async fn fetch_newest_release_from(repo: &str) -> Result<ReleaseInfo> {
    let url = format!("https://api.github.com/repos/{repo}/releases?per_page=1");
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
        return Err(anyhow!("no releases found for {repo}"));
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
    let dest = paths::installer_dest();
    let local_version = effective_local_version(&dest, CURRENT_VERSION);

    if !is_newer(&release.tag_name, &local_version) {
        tracing::info!(
            remote = %release.tag_name,
            local = %local_version,
            "installer already up to date; skipping self-update"
        );
        return Ok(None);
    }

    let os = current_os();
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

    let tmp_download = paths::bootstrap_cache_dir().join(format!("hermes-setup.new-{}", asset.name));
    download_asset(&asset.browser_download_url, &tmp_download).await?;

    // The asset is a package: on macOS a disk image the binary has to be
    // taken out of; on Windows/Linux the .exe/AppImage *is* the binary.
    let staged_candidate = if asset.name.to_ascii_lowercase().ends_with(".dmg") {
        let extracted = paths::bootstrap_cache_dir().join("hermes-setup.new-binary");
        let _ = tokio::fs::remove_file(&extracted).await;
        let result = extract_from_dmg(&tmp_download, &extracted).await;
        let _ = tokio::fs::remove_file(&tmp_download).await;
        result?;
        extracted
    } else {
        tmp_download
    };

    // Never stage anything the loader would reject (AIS-346): a DMG, a zip,
    // an HTML error page … keep the working installer instead.
    if let Err(err) = ensure_native_executable(&staged_candidate).await {
        let _ = tokio::fs::remove_file(&staged_candidate).await;
        return Err(err);
    }

    // Atomic rename over the stable staged path. Safe here (unlike a
    // self-copy from `current_exe()`) because we are not the file we're
    // replacing — the process currently executing is the OLD staged binary
    // running from `dest`, and Windows allows renaming a file that isn't the
    // one backing a running process's mapped image as long as it's a
    // separate temp file being moved on top, not an in-place rewrite of the
    // open handle's own bytes. The old binary is parked next to it first so
    // `restore_previous` can undo the swap when the new one fails to launch.
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("creating {}", parent.display()))?;
    }
    let prev = previous_path(&dest);
    if dest.exists() {
        let _ = tokio::fs::remove_file(&prev).await;
        if let Err(err) = tokio::fs::rename(&dest, &prev).await {
            tracing::warn!(%err, ?dest, "could not park the previous installer; replacing in place");
        }
    }
    tokio::fs::rename(&staged_candidate, &dest)
        .await
        .with_context(|| format!("staging new installer at {}", dest.display()))?;

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = tokio::fs::metadata(&dest).await?.permissions();
        perms.set_mode(0o755);
        tokio::fs::set_permissions(&dest, perms).await?;
    }
    if let Err(err) = tokio::fs::write(staged_tag_path(&dest), format!("{}\n", release.tag_name)).await {
        tracing::warn!(%err, "could not record the staged installer's release tag");
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
    fn native_executable_magic_per_os() {
        let macho64 = [0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0x00, 0x00, 0x01];
        let fat = [0xca, 0xfe, 0xba, 0xbe, 0x00, 0x00, 0x00, 0x02];
        let elf = b"\x7fELF\x02\x01\x01\x00";
        let mz = b"MZ\x90\x00\x03\x00\x00\x00";
        // A DMG (koly trailer at the end, arbitrary bytes at the start), a
        // zip and an HTML error page must never pass for any OS.
        let dmg_like = [0x78, 0x01, 0x73, 0x0d, 0x62, 0x62, 0x60, 0x60];
        let zip = b"PK\x03\x04\x14\x00\x00\x00";
        let html = b"<!DOCTYP";

        assert!(is_native_executable_for("macos", &macho64));
        assert!(is_native_executable_for("macos", &fat));
        assert!(!is_native_executable_for("macos", elf));
        assert!(!is_native_executable_for("macos", &dmg_like));
        assert!(!is_native_executable_for("macos", zip));
        assert!(!is_native_executable_for("macos", html));

        assert!(is_native_executable_for("linux", elf));
        assert!(!is_native_executable_for("linux", &macho64));

        assert!(is_native_executable_for("windows", mz));
        assert!(!is_native_executable_for("windows", html));
        assert!(!is_native_executable_for("windows", &[0x4d]));
    }

    #[test]
    fn finds_the_mach_o_inside_an_app_bundle() {
        let root = std::env::temp_dir().join(format!("hermes-selfupdate-bundle-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let macos_dir = root.join("Hermes.app").join("Contents").join("MacOS");
        std::fs::create_dir_all(&macos_dir).unwrap();
        // A helper script sorts before the binary; it must be skipped.
        std::fs::write(macos_dir.join("Helper.sh"), b"#!/bin/sh\necho hi\n").unwrap();
        std::fs::write(macos_dir.join("Hermes-Setup"), [0xcf, 0xfa, 0xed, 0xfe, 0, 0, 0, 0]).unwrap();
        // Something that is not a bundle at the root is ignored.
        std::fs::write(root.join("README.txt"), b"drag to Applications").unwrap();

        let found = find_installer_in_bundle_root(&root).expect("binary found");
        assert_eq!(found, macos_dir.join("Hermes-Setup"));

        std::fs::remove_dir_all(&root).unwrap();
        assert!(find_installer_in_bundle_root(&root).is_none());
    }

    #[test]
    fn staged_release_tag_raises_the_local_version() {
        let dir = std::env::temp_dir().join(format!("hermes-selfupdate-tag-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let dest = dir.join("hermes-setup");
        assert_eq!(effective_local_version(&dest, "0.7.4"), "0.7.4");

        std::fs::write(staged_tag_path(&dest), "v0.7.6-rc.3\n").unwrap();
        assert_eq!(effective_local_version(&dest, "0.7.4"), "v0.7.6-rc.3");
        // The compiled version wins when it is the higher one (a fresh
        // install after the sidecar was written by an older stage).
        assert_eq!(effective_local_version(&dest, "0.8.0"), "0.8.0");
        assert!(!is_newer("v0.7.6-rc.3", &effective_local_version(&dest, "0.7.4")));

        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn sidecar_and_prev_paths_sit_next_to_the_installer() {
        let dest = Path::new("/home/x/.hermes/hermes-setup");
        assert_eq!(staged_tag_path(dest), Path::new("/home/x/.hermes/hermes-setup.release-tag"));
        assert_eq!(previous_path(dest), Path::new("/home/x/.hermes/hermes-setup.prev"));
        let exe = Path::new("C:\\hermes\\hermes-setup.exe");
        assert!(staged_tag_path(exe).to_string_lossy().ends_with("hermes-setup.release-tag"));
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
