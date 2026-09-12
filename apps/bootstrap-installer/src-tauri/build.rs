use std::process::Command;

fn main() {
    // -----------------------------------------------------------------
    // Bake the install.ps1 pin into the binary at compile time.
    //
    // BUILD_PIN_COMMIT and BUILD_PIN_BRANCH are read by bootstrap.rs's
    // `option_env!()` macro to default the install-script reference.
    // Precedence (matches install.ps1's own arg precedence): commit > branch.
    //
    // Commit pin is now default. By default we pin to the checkout's current
    // HEAD commit so every built installer points to an immutable revision.
    // HERMES_BUILD_PIN_COMMIT still overrides this (accepts SHA/tag/branch).
    //
    // Commit pin resolution:
    //   1. HERMES_BUILD_PIN_COMMIT, if set and non-empty. Accepts a SHA, tag,
    //      or branch name; resolved to an immutable SHA via `git rev-parse`
    //      when possible, else used verbatim if it already looks like a SHA.
    //   2. Fallback: current checkout HEAD commit (`git rev-parse --verify HEAD`).
    //
    // Branch pin resolution:
    //   1. HERMES_BUILD_PIN_BRANCH, if set and non-empty.
    //   2. `git rev-parse --abbrev-ref HEAD` of the checkout this build.rs
    //      lives in — the current branch. (None on a detached HEAD.)
    //   3. Last-resort fallback handled below: if neither commit nor branch
    //      resolves, warn — the binary needs a runtime arg or dev-repo env.
    //
    // Build script reruns on git HEAD change so a new commit triggers
    // a rebuild without `cargo clean`.
    // -----------------------------------------------------------------

    let commit = resolve_commit_pin();
    let branch = resolve_branch_pin();
    let tag = resolve_tag_pin();

    if let Some(t) = &tag {
        // AIS-313: a release build pins the installer to its own release tag;
        // install.ps1/install.sh then install exactly that release from the
        // public release repository (no source-repository access needed).
        println!("cargo:rustc-env=BUILD_PIN_TAG={t}");
        println!("cargo:warning=hermes-bootstrap: pinning to release tag {t}");
    }

    if let Some(c) = &commit {
        println!("cargo:rustc-env=BUILD_PIN_COMMIT={c}");
        println!(
            "cargo:warning=hermes-bootstrap: pinning to commit {}",
            short(c)
        );
    }
    if let Some(b) = &branch {
        println!("cargo:rustc-env=BUILD_PIN_BRANCH={b}");
        match &commit {
            Some(_) => println!("cargo:warning=hermes-bootstrap: pinning to branch {b}"),
            None => println!(
                "cargo:warning=hermes-bootstrap: pinning to branch {b} (commit pin unavailable)"
            ),
        }
    }
    if commit.is_none() && branch.is_none() {
        // Fail loudly rather than silently produce a binary that errors
        // at runtime with "no install-script pin supplied". A build that
        // can't resolve a pin almost certainly indicates a misconfigured
        // build environment.
        println!(
            "cargo:warning=hermes-bootstrap: no pin resolved at build time; binary will fail at runtime without HERMES_SETUP_DEV_REPO_ROOT or runtime args"
        );
    }

    // Rerun build.rs when HEAD moves. With branch-follow as the default the
    // baked commit no longer changes per-commit, but a branch *switch* changes
    // the detected branch name, so we still re-trigger. When an explicit
    // HERMES_BUILD_PIN_COMMIT resolves a moving ref (tag/branch) to a SHA, a
    // HEAD move can also change that resolution. .git/HEAD changes on every
    // commit / branch switch / rebase.
    let git_dir = locate_git_dir();
    if let Some(gd) = &git_dir {
        println!("cargo:rerun-if-changed={}/HEAD", gd.display());
        // .git/HEAD often points at a ref (e.g. `ref: refs/heads/bb/gui`);
        // also watch the ref itself so a new commit on the same branch
        // re-triggers.
        if let Ok(head) = std::fs::read_to_string(gd.join("HEAD")) {
            if let Some(rest) = head.trim().strip_prefix("ref: ") {
                println!("cargo:rerun-if-changed={}/{}", gd.display(), rest);
            }
        }
    }
    println!("cargo:rerun-if-env-changed=HERMES_BUILD_PIN_COMMIT");
    println!("cargo:rerun-if-env-changed=HERMES_BUILD_PIN_BRANCH");

    // -----------------------------------------------------------------
    // Tauri windows manifest. See hermes-setup.manifest for rationale —
    // declares level="asInvoker" so Windows's installer-detection
    // heuristic doesn't refuse to launch us without UAC elevation.
    // -----------------------------------------------------------------
    #[cfg(target_os = "windows")]
    let attrs = {
        let manifest = include_str!("hermes-setup.manifest");
        let win = tauri_build::WindowsAttributes::new().app_manifest(manifest);
        tauri_build::Attributes::new().windows_attributes(win)
    };

    #[cfg(not(target_os = "windows"))]
    let attrs = tauri_build::Attributes::new();

    tauri_build::try_build(attrs).expect("failed to run tauri-build");
}

fn resolve_commit_pin() -> Option<String> {
    // Explicit override first.
    if let Ok(requested_raw) = std::env::var("HERMES_BUILD_PIN_COMMIT") {
        let requested = requested_raw.trim();
        if !requested.is_empty() {
            // Resolve the request (which may be a SHA, tag, or branch name) to
            // an immutable commit SHA. `^{commit}` dereferences tags.
            if let Ok(out) = Command::new("git")
                .args(["rev-parse", "--verify", &format!("{requested}^{{commit}}")])
                .output()
            {
                if out.status.success() {
                    if let Ok(s) = String::from_utf8(out.stdout) {
                        let s = s.trim().to_string();
                        if !s.is_empty() {
                            return Some(s);
                        }
                    }
                }
            }
            // Couldn't resolve via git (e.g. building outside checkout). Accept
            // literal only if it already looks like a SHA.
            if is_sha(requested) {
                return Some(requested.to_string());
            }
            panic!(
                "HERMES_BUILD_PIN_COMMIT={requested:?} could not be resolved to a commit \
                 (git rev-parse failed and it is not a valid SHA)"
            );
        }
    }

    // Default: pin to local HEAD commit.
    if let Ok(out) = Command::new("git")
        .args(["rev-parse", "--verify", "HEAD"])
        .output()
    {
        if out.status.success() {
            if let Ok(s) = String::from_utf8(out.stdout) {
                let s = s.trim().to_string();
                if !s.is_empty() {
                    return Some(s);
                }
            }
        }
    }
    None
}

/// True if `s` looks like an abbreviated-or-full git SHA (7..=40 hex chars).
fn is_sha(s: &str) -> bool {
    let len = s.len();
    (7..=40).contains(&len) && s.chars().all(|c| c.is_ascii_hexdigit())
}

/// `HERMES_BUILD_PIN_TAG` (vX.Y.Z or vX.Y.Z-rc.N): the release the shipped
/// installer installs. Unset for local developer builds.
fn resolve_tag_pin() -> Option<String> {
    let v = std::env::var("HERMES_BUILD_PIN_TAG").ok()?;
    let v = v.trim();
    if v.is_empty() {
        return None;
    }
    let body = v.strip_prefix('v').unwrap_or(v);
    let valid = {
        let mut parts = body.splitn(2, "-rc.");
        let core = parts.next().unwrap_or("");
        let rc = parts.next();
        let core_ok = core.split('.').count() == 3
            && core.split('.').all(|x| !x.is_empty() && x.chars().all(|c| c.is_ascii_digit()));
        let rc_ok = rc.map_or(true, |n| !n.is_empty() && n.chars().all(|c| c.is_ascii_digit()));
        core_ok && rc_ok
    };
    if !valid {
        panic!("HERMES_BUILD_PIN_TAG={v:?} is not a release tag (vX.Y.Z or vX.Y.Z-rc.N)");
    }
    Some(if v.starts_with('v') { v.to_string() } else { format!("v{v}") })
}

fn resolve_branch_pin() -> Option<String> {
    if let Ok(v) = std::env::var("HERMES_BUILD_PIN_BRANCH") {
        if !v.trim().is_empty() {
            return Some(v.trim().to_string());
        }
    }
    let out = Command::new("git")
        .args(["rev-parse", "--abbrev-ref", "HEAD"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8(out.stdout).ok()?.trim().to_string();
    // "HEAD" is what you get on a detached checkout — no meaningful branch
    // to pin to. The commit pin still applies; just don't emit a branch.
    if s.is_empty() || s == "HEAD" {
        None
    } else {
        Some(s)
    }
}

fn locate_git_dir() -> Option<std::path::PathBuf> {
    let out = Command::new("git")
        .args(["rev-parse", "--git-dir"])
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8(out.stdout).ok()?.trim().to_string();
    if s.is_empty() {
        return None;
    }
    Some(std::path::PathBuf::from(s))
}

fn short(commit: &str) -> &str {
    if commit.len() >= 12 {
        &commit[..12]
    } else {
        commit
    }
}
