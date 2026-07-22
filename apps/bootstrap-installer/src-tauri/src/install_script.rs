//! Resolves and downloads `scripts/install.ps1` (and `install.sh`).
//!
//! Resolution order:
//!   1. Dev shortcut: a sibling repo checkout via $HERMES_SETUP_DEV_REPO_ROOT
//!      env var. Lets devs iterate without re-publishing the script.
//!   2. Bundled fallback: use the install scripts embedded into this binary at
//!      build time, so local bootstrap fixes ship with the generated installer.
//!   3. Network: download from GitHub raw at a pinned commit or branch.
//!      Commit pins are immutable; branch pins are HEAD-tracking.
//!
//! Mirrors `apps/desktop/electron/bootstrap-runner.cjs`'s `resolveInstallScript`,
//! but the dev-checkout resolution is driven by an env var rather than the
//! Electron app's APP_ROOT/../.. trick, because Hermes-Setup.exe is meant
//! to live OUTSIDE any repo checkout.

use anyhow::{anyhow, Context, Result};
use std::path::{Path, PathBuf};
use tokio::io::AsyncWriteExt;

use crate::paths;

/// Identity of the install.ps1 we'll execute. Used by both the manifest
/// fetch and the per-stage runs.
#[derive(Debug, Clone)]
pub struct ResolvedScript {
    pub path: PathBuf,
    pub source: ScriptSource,
    /// Commit pin (40-char SHA) if known. install.ps1's `-Commit` arg is
    /// what makes the repo stage clone the exact tested SHA.
    pub commit: Option<String>,
    pub branch: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ScriptSource {
    DevCheckout,
    Bundled,
    Cached,
    Downloaded,
}

/// What flavor of script (Windows .ps1 vs Unix .sh).
#[derive(Debug, Clone, Copy)]
pub enum ScriptKind {
    Ps1,
    Sh,
}

impl ScriptKind {
    pub fn for_current_os() -> Self {
        if cfg!(target_os = "windows") {
            Self::Ps1
        } else {
            Self::Sh
        }
    }

    fn filename(&self) -> &'static str {
        match self {
            Self::Ps1 => "install.ps1",
            Self::Sh => "install.sh",
        }
    }
}

const BUNDLED_INSTALL_PS1: &[u8] = include_bytes!("../../../../scripts/install.ps1");
const BUNDLED_INSTALL_SH: &[u8] = include_bytes!("../../../../scripts/install.sh");

const BUNDLED_AIMDS_ASSETS: &[(&str, &[u8])] = &[
    (
        "installer/scripts/seed-workspace-cwd.py",
        include_bytes!("../../../../installer/scripts/seed-workspace-cwd.py"),
    ),
    (
        "skills/productivity/excel/SKILL.md",
        include_bytes!("../../../../skills/productivity/excel/SKILL.md"),
    ),
    (
        "skills/productivity/excel/scripts/read.py",
        include_bytes!("../../../../skills/productivity/excel/scripts/read.py"),
    ),
    (
        "skills/productivity/excel/scripts/write.py",
        include_bytes!("../../../../skills/productivity/excel/scripts/write.py"),
    ),
    (
        "skills/productivity/pdf/SKILL.md",
        include_bytes!("../../../../skills/productivity/pdf/SKILL.md"),
    ),
    (
        "skills/productivity/pdf/scripts/pdf.py",
        include_bytes!("../../../../skills/productivity/pdf/scripts/pdf.py"),
    ),
    (
        "skills/productivity/file-conversion/SKILL.md",
        include_bytes!("../../../../skills/productivity/file-conversion/SKILL.md"),
    ),
    (
        "skills/productivity/file-conversion/scripts/convert.py",
        include_bytes!("../../../../skills/productivity/file-conversion/scripts/convert.py"),
    ),
    (
        "skills/productivity/word/SKILL.md",
        include_bytes!("../../../../skills/productivity/word/SKILL.md"),
    ),
    (
        "skills/productivity/word/scripts/read.py",
        include_bytes!("../../../../skills/productivity/word/scripts/read.py"),
    ),
    (
        "skills/productivity/word/scripts/write.py",
        include_bytes!("../../../../skills/productivity/word/scripts/write.py"),
    ),
    (
        "skills/productivity/word/scripts/convert.py",
        include_bytes!("../../../../skills/productivity/word/scripts/convert.py"),
    ),
    (
        "installer/workspace-template/.workspace-template-version",
        include_bytes!("../../../../installer/workspace-template/.workspace-template-version"),
    ),
    (
        "installer/workspace-template/AGENTS.md",
        include_bytes!("../../../../installer/workspace-template/AGENTS.md"),
    ),
    (
        "installer/workspace-template/README.md",
        include_bytes!("../../../../installer/workspace-template/README.md"),
    ),
    (
        "installer/workspace-template/SETUP.md",
        include_bytes!("../../../../installer/workspace-template/SETUP.md"),
    ),
    (
        "installer/workspace-template/_conventions.md",
        include_bytes!("../../../../installer/workspace-template/_conventions.md"),
    ),
    (
        "installer/workspace-template/_findings.md",
        include_bytes!("../../../../installer/workspace-template/_findings.md"),
    ),
    (
        "installer/workspace-template/_open-questions.md",
        include_bytes!("../../../../installer/workspace-template/_open-questions.md"),
    ),
    (
        "installer/workspace-template/contacts/.gitkeep",
        include_bytes!("../../../../installer/workspace-template/contacts/.gitkeep"),
    ),
    (
        "installer/workspace-template/decisions/.gitkeep",
        include_bytes!("../../../../installer/workspace-template/decisions/.gitkeep"),
    ),
    (
        "installer/workspace-template/documents/.gitkeep",
        include_bytes!("../../../../installer/workspace-template/documents/.gitkeep"),
    ),
    (
        "installer/workspace-template/ideas/.gitkeep",
        include_bytes!("../../../../installer/workspace-template/ideas/.gitkeep"),
    ),
    (
        "installer/workspace-template/journal/.gitkeep",
        include_bytes!("../../../../installer/workspace-template/journal/.gitkeep"),
    ),
    (
        "installer/workspace-template/knowledge/.gitkeep",
        include_bytes!("../../../../installer/workspace-template/knowledge/.gitkeep"),
    ),
    (
        "installer/workspace-template/meetings/.gitkeep",
        include_bytes!("../../../../installer/workspace-template/meetings/.gitkeep"),
    ),
    (
        "installer/workspace-template/notes/.gitkeep",
        include_bytes!("../../../../installer/workspace-template/notes/.gitkeep"),
    ),
    (
        "installer/workspace-template/projects/.gitkeep",
        include_bytes!("../../../../installer/workspace-template/projects/.gitkeep"),
    ),
    (
        "installer/workspace-template/tasks/tasks.md",
        include_bytes!("../../../../installer/workspace-template/tasks/tasks.md"),
    ),
    (
        "installer/workspace-template/tasks/thisweek.md",
        include_bytes!("../../../../installer/workspace-template/tasks/thisweek.md"),
    ),
    (
        "installer/workspace-template/_inbox/_archive/.gitkeep",
        include_bytes!("../../../../installer/workspace-template/_inbox/_archive/.gitkeep"),
    ),
    (
        "installer/workspace-template/_templates/contact.md",
        include_bytes!("../../../../installer/workspace-template/_templates/contact.md"),
    ),
    (
        "installer/workspace-template/_templates/decision.md",
        include_bytes!("../../../../installer/workspace-template/_templates/decision.md"),
    ),
    (
        "installer/workspace-template/_templates/idea.md",
        include_bytes!("../../../../installer/workspace-template/_templates/idea.md"),
    ),
    (
        "installer/workspace-template/_templates/meeting.md",
        include_bytes!("../../../../installer/workspace-template/_templates/meeting.md"),
    ),
    (
        "installer/workspace-template/_templates/note.md",
        include_bytes!("../../../../installer/workspace-template/_templates/note.md"),
    ),
    (
        "installer/workspace-template/_templates/project.md",
        include_bytes!("../../../../installer/workspace-template/_templates/project.md"),
    ),
];

/// Validates a string looks like a git SHA (7+ hex chars). Mirrors
/// `STAMP_COMMIT_RE` from bootstrap-runner.cjs.
fn is_valid_commit(s: &str) -> bool {
    let len = s.len();
    (7..=40).contains(&len) && s.chars().all(|c| c.is_ascii_hexdigit())
}

/// Resolves the install script to use for this run.
///
/// `pin` is the commit-or-branch from either Hermes-Setup's build-time
/// constant (compiled into the installer) or a runtime override.
pub async fn resolve(
    kind: ScriptKind,
    pin: &Pin,
    emit_log: &impl Fn(&str),
) -> Result<ResolvedScript> {
    // 1. Dev shortcut.
    if let Ok(repo_root) = std::env::var("HERMES_SETUP_DEV_REPO_ROOT") {
        let candidate = PathBuf::from(repo_root).join("scripts").join(kind.filename());
        if candidate.exists() {
            emit_log(&format!(
                "[bootstrap] dev mode — using local {} at {}",
                kind.filename(),
                candidate.display()
            ));
            return Ok(ResolvedScript {
                path: candidate,
                source: ScriptSource::DevCheckout,
                commit: pin.commit.clone(),
                branch: pin.branch.clone(),
            });
        }
    }

    // 2. Bundled fallback. This is the default for shipped installers so the
    // build always executes the scripts from the same checkout it was built
    // from. Set HERMES_FORCE_REMOTE_INSTALL_SCRIPT=1 to bypass this during
    // debugging and fetch from GitHub instead.
    if std::env::var("HERMES_FORCE_REMOTE_INSTALL_SCRIPT")
        .map(|v| v.trim() != "1")
        .unwrap_or(true)
    {
        let bundled = bundled_path(kind);
        materialize_bundled_script(kind, &bundled)?;

        if let Some(cache_root) = bundled.parent() {
            materialize_bundled_aimds_assets(cache_root)?;
        }

        emit_log(&format!(
            "[bootstrap] using bundled {} at {}",
            kind.filename(),
            bundled.display()
        ));
        return Ok(ResolvedScript {
            path: bundled,
            source: ScriptSource::Bundled,
            commit: pin.commit.clone(),
            branch: pin.branch.clone(),
        });
    }

    // 3. Network. Pin must be a real commit or a branch ref.
    let commit_or_ref = match (&pin.commit, &pin.branch) {
        (Some(c), _) if is_valid_commit(c) => c.clone(),
        (_, Some(b)) if !b.trim().is_empty() => b.clone(),
        (Some(other), _) => {
            return Err(anyhow!(
                "install script pin commit `{other}` is not a valid git SHA"
            ));
        }
        _ => {
            return Err(anyhow!(
                "no install-script pin supplied — installer cannot resolve a script source"
            ));
        }
    };

    let cached = cached_path(kind, &commit_or_ref);
    if cached.exists() {
        emit_log(&format!(
            "[bootstrap] using cached {} for {}",
            kind.filename(),
            truncate_ref(&commit_or_ref)
        ));
        return Ok(ResolvedScript {
            path: cached,
            source: ScriptSource::Cached,
            commit: pin.commit.clone(),
            branch: pin.branch.clone(),
        });
    }

    emit_log(&format!(
        "[bootstrap] downloading {} for {} from GitHub",
        kind.filename(),
        truncate_ref(&commit_or_ref)
    ));

    download(kind, &commit_or_ref, &cached).await?;

    emit_log(&format!("[bootstrap] cached to {}", cached.display()));

    Ok(ResolvedScript {
        path: cached,
        source: ScriptSource::Downloaded,
        commit: pin.commit.clone(),
        branch: pin.branch.clone(),
    })
}

fn bundled_path(kind: ScriptKind) -> PathBuf {
    let version = env!("CARGO_PKG_VERSION");
    let filename = match kind {
        ScriptKind::Ps1 => format!("install-bundled-{version}.ps1"),
        ScriptKind::Sh => format!("install-bundled-{version}.sh"),
    };
    paths::bootstrap_cache_dir().join(filename)
}

fn bundled_bytes(kind: ScriptKind) -> &'static [u8] {
    match kind {
        ScriptKind::Ps1 => BUNDLED_INSTALL_PS1,
        ScriptKind::Sh => BUNDLED_INSTALL_SH,
    }
}

fn materialize_bundled_script(kind: ScriptKind, dest_path: &Path) -> Result<()> {
    if let Some(parent) = dest_path.parent() {
        std::fs::create_dir_all(parent).with_context(|| {
            format!("creating bootstrap-cache parent dir {}", parent.display())
        })?;
    }

    std::fs::write(dest_path, bundled_bytes(kind))
        .with_context(|| format!("writing bundled script to {}", dest_path.display()))?;

    #[cfg(unix)]
    if matches!(kind, ScriptKind::Sh) {
        use std::os::unix::fs::PermissionsExt;
        let mut perms = std::fs::metadata(dest_path)
            .with_context(|| format!("reading metadata for {}", dest_path.display()))?
            .permissions();
        perms.set_mode(0o755);
        std::fs::set_permissions(dest_path, perms).with_context(|| {
            format!("setting executable bit on {}", dest_path.display())
        })?;
    }

    Ok(())
}

fn materialize_bundled_aimds_assets(cache_root: &Path) -> Result<()> {
    for (rel_path, bytes) in BUNDLED_AIMDS_ASSETS {
        let dest = cache_root.join(rel_path);
        if let Some(parent) = dest.parent() {
            std::fs::create_dir_all(parent)
                .with_context(|| format!("creating asset dir {}", parent.display()))?;
        }

        std::fs::write(&dest, bytes)
            .with_context(|| format!("writing bundled AIMDS asset {}", dest.display()))?;
    }

    Ok(())
}

#[derive(Debug, Clone, Default)]
pub struct Pin {
    pub commit: Option<String>,
    pub branch: Option<String>,
}

fn cached_path(kind: ScriptKind, commit_or_ref: &str) -> PathBuf {
    let safe = sanitize_ref(commit_or_ref);
    let filename = match kind {
        ScriptKind::Ps1 => format!("install-{safe}.ps1"),
        ScriptKind::Sh => format!("install-{safe}.sh"),
    };
    paths::bootstrap_cache_dir().join(filename)
}

/// Replace anything that's not [A-Za-z0-9._-] with `_`. Branch refs can
/// contain `/`, dots, etc.; we want a flat filename.
fn sanitize_ref(s: &str) -> String {
    s.chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '.' || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect()
}

fn truncate_ref(s: &str) -> &str {
    if is_valid_commit(s) && s.len() >= 12 {
        &s[..12]
    } else {
        s
    }
}

/// Downloads to `dest_path` via reqwest with rustls. Atomically renames
/// `dest_path.tmp` → `dest_path` so partial writes don't poison the cache.
async fn download(kind: ScriptKind, commit_or_ref: &str, dest_path: &Path) -> Result<()> {
    let url = format!(
        "https://raw.githubusercontent.com/IAMDS-GMBH/hermes-agent/{}/scripts/{}",
        commit_or_ref,
        kind.filename()
    );

    if let Some(parent) = dest_path.parent() {
        std::fs::create_dir_all(parent).with_context(|| {
            format!("creating bootstrap-cache parent dir {}", parent.display())
        })?;
    }

    let tmp_path = dest_path.with_extension({
        let ext = dest_path
            .extension()
            .and_then(|s| s.to_str())
            .unwrap_or("tmp");
        format!("{ext}.tmp")
    });

    let response = reqwest::Client::new()
        .get(&url)
        .header("User-Agent", "hermes-setup/0.0.1")
        .send()
        .await
        .with_context(|| format!("GET {url}"))?;

    if !response.status().is_success() {
        return Err(anyhow!(
            "Failed to download {}: HTTP {} from {}",
            kind.filename(),
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

    tokio::fs::rename(&tmp_path, dest_path)
        .await
        .with_context(|| {
            format!(
                "renaming {} → {}",
                tmp_path.display(),
                dest_path.display()
            )
        })?;

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn is_valid_commit_accepts_short_and_full_shas() {
        assert!(is_valid_commit("02d26981d3d4ad50e142399b8476f59ad5953ff0"));
        assert!(is_valid_commit("02d2698"));
        assert!(!is_valid_commit("02d269"));
        assert!(!is_valid_commit("not-a-sha"));
        assert!(!is_valid_commit(""));
    }

    #[test]
    fn sanitize_ref_replaces_slashes() {
        assert_eq!(sanitize_ref("bb/gui"), "bb_gui");
        assert_eq!(sanitize_ref("main"), "main");
        assert_eq!(sanitize_ref("release/1.2.3"), "release_1.2.3");
    }
}
