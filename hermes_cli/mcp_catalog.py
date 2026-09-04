"""MCP catalog — curated, Nous-approved MCP servers shipped with the repo.

Mirrors the optional-skills/ pattern: each catalog entry lives under
``optional-mcps/<name>/manifest.yaml`` and ships disabled. Users discover
entries via ``hermes mcp catalog`` or the interactive ``hermes mcp picker``,
and install them with ``hermes mcp install <name>`` (or by toggling in the
picker, which flows them through any required env/OAuth setup).

Catalog policy:
- Entries are added only by merging a PR into hermes-agent. Presence in the
  ``optional-mcps/`` directory = Nous approval. No community tier, no trust
  signals beyond "it's in the catalog".
- Manifests pin transport details (commands, args, refs). MCPs are never
  auto-updated; users explicitly re-run ``hermes mcp install <name>`` to
  pull a new manifest version after a repo update.
- Secrets prompted at install time go to ``~/.hermes/.env`` (the
  .env-is-for-secrets rule). Non-secret env vars also go to .env to keep
  one credential store.

See website/docs/user-guide/mcp-catalog.md for user docs.
See references/mcp-catalog.md (this repo's skill) for the manifest schema.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from hermes_cli._subprocess_compat import windows_hide_flags
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from hermes_constants import get_hermes_home, get_optional_mcps_dir
from hermes_cli.colors import Colors, color
from hermes_cli.config import (
    load_config,
    save_config,
    get_env_value,
    save_env_value,
)
from hermes_cli.cli_output import prompt as _prompt_input

_MANIFEST_VERSION = 1

# Substituted at install time inside `transport.command` / `transport.args`
# (and, since _run_bootstrap expands it too, inside `install.bootstrap`).
_INSTALL_DIR_VAR = "${INSTALL_DIR}"

# Bootstrap commands in manifests are written using Unix conventions
# (`python3`, `.venv/bin/...`) since that's the common case across the
# catalog. On Windows there's typically no `python3` on PATH (only `python`
# / the `py` launcher) and venvs place executables under `.venv/Scripts/`
# instead of `.venv/bin/` -- so bootstrap commands are rewritten for the
# current platform before being handed to the shell.
_VENV_BIN_UNIX = ".venv/bin/"
_VENV_BIN_WINDOWS = ".venv/Scripts/"


# ─── Data classes ────────────────────────────────────────────────────────────


@dataclass
class EnvVarSpec:
    name: str
    prompt: str
    required: bool = True
    secret: bool = True
    default: str = ""


@dataclass
class AuthSpec:
    type: str  # "api_key" | "oauth" | "none"
    env: List[EnvVarSpec] = field(default_factory=list)
    # OAuth-specific (case 2: third-party provider like Google)
    provider: Optional[str] = None
    scopes: List[str] = field(default_factory=list)
    env_var: Optional[str] = None
    # Free-text clarification shown above the install dialog's field list —
    # e.g. "choose Cloud OR Server auth, not both" or "blank PAT triggers an
    # OAuth login popup". Keep it short; the per-field `prompt` still carries
    # the primary guidance.
    notes: Optional[str] = None


@dataclass
class TransportSpec:
    type: str  # "stdio" | "http"
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    url: Optional[str] = None
    version: Optional[str] = None  # informational, pinned
    # Static headers for http transport (e.g. toolset/feature-flag headers
    # like X-MCP-Toolsets). Auth headers (Authorization) are layered on top
    # of these by _build_server_config, not declared here.
    headers: Dict[str, str] = field(default_factory=dict)


@dataclass
class InstallSpec:
    """Optional bootstrap step (git clone + dep install).

    Omit for one-shot launchable servers (npx, uvx).
    """
    type: str  # "git"
    url: str
    ref: str  # commit/tag/branch — pinned, never floats
    bootstrap: List[str] = field(default_factory=list)


@dataclass
class ToolsSpec:
    """Manifest-side tool-selection hints.

    Drives the pre-checked state of the install-time tool checklist, and acts
    as the fallback selection when probe fails. See install_entry() flow.
    """

    # If declared, these tool names are pre-checked in the checklist (or
    # applied directly when probe fails). If None, all probed tools are
    # pre-checked (or no filter is written when probe fails).
    default_enabled: Optional[List[str]] = None


@dataclass
class CatalogEntry:
    name: str
    description: str
    source: str
    transport: TransportSpec
    auth: AuthSpec
    tools: ToolsSpec = field(default_factory=ToolsSpec)
    install: Optional[InstallSpec] = None
    post_install: str = ""
    disabled: bool = False
    manifest_path: Path = field(default_factory=Path)


# ─── Manifest loader ─────────────────────────────────────────────────────────


class CatalogError(Exception):
    """Manifest parse/validation failure or install error."""


def _catalog_root() -> Path:
    """Return the optional-mcps/ directory shipped with this Hermes install."""
    # Prefer the env-var override / packaged location; fall back to the repo's
    # optional-mcps/ next to the package (source checkout).
    return get_optional_mcps_dir(Path(__file__).parent.parent / "optional-mcps")


def _parse_env_spec(raw: Any) -> EnvVarSpec:
    if not isinstance(raw, dict):
        raise CatalogError(f"env entry must be a mapping, got {type(raw).__name__}")
    name = raw.get("name") or ""
    if not name or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise CatalogError(f"invalid env var name: {name!r}")
    return EnvVarSpec(
        name=name,
        prompt=raw.get("prompt") or name,
        required=bool(raw.get("required", True)),
        secret=bool(raw.get("secret", True)),
        default=str(raw.get("default") or ""),
    )


def _parse_manifest(path: Path) -> CatalogEntry:
    """Read and validate a manifest.yaml. Raise CatalogError on any problem."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:
        raise CatalogError(f"failed to read {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise CatalogError(f"{path}: manifest must be a mapping")

    mv = data.get("manifest_version")
    if mv != _MANIFEST_VERSION:
        raise CatalogError(
            f"{path}: manifest_version {mv!r} unsupported "
            f"(this Hermes understands version {_MANIFEST_VERSION})"
        )

    name = data.get("name") or ""
    if not name or not re.match(r"^[A-Za-z0-9_-]+$", name):
        raise CatalogError(f"{path}: invalid or missing 'name'")

    description = str(data.get("description") or "").strip()
    if not description:
        raise CatalogError(f"{path}: 'description' required")

    source = str(data.get("source") or "").strip()

    transport_raw = data.get("transport") or {}
    if not isinstance(transport_raw, dict):
        raise CatalogError(f"{path}: 'transport' must be a mapping")
    t_type = transport_raw.get("type")
    if t_type not in ("stdio", "http"):
        raise CatalogError(f"{path}: transport.type must be 'stdio' or 'http'")
    args = transport_raw.get("args") or []
    if not isinstance(args, list):
        raise CatalogError(f"{path}: transport.args must be a list")
    headers_raw = transport_raw.get("headers") or {}
    if not isinstance(headers_raw, dict):
        raise CatalogError(f"{path}: transport.headers must be a mapping")
    transport = TransportSpec(
        type=t_type,
        command=transport_raw.get("command"),
        args=[str(a) for a in args],
        url=transport_raw.get("url"),
        version=transport_raw.get("version"),
        headers={str(k): str(v) for k, v in headers_raw.items()},
    )
    if t_type == "stdio" and not transport.command:
        raise CatalogError(f"{path}: stdio transport requires 'command'")
    if t_type == "http" and not transport.url:
        raise CatalogError(f"{path}: http transport requires 'url'")

    auth_raw = data.get("auth") or {"type": "none"}
    if not isinstance(auth_raw, dict):
        raise CatalogError(f"{path}: 'auth' must be a mapping")
    a_type = auth_raw.get("type") or "none"
    if a_type not in ("api_key", "oauth", "none"):
        raise CatalogError(f"{path}: auth.type must be 'api_key'|'oauth'|'none'")
    env_list_raw = auth_raw.get("env") or []
    if not isinstance(env_list_raw, list):
        raise CatalogError(f"{path}: auth.env must be a list")
    env_list = [_parse_env_spec(e) for e in env_list_raw]
    notes_raw = auth_raw.get("notes")
    auth = AuthSpec(
        type=a_type,
        env=env_list,
        provider=auth_raw.get("provider"),
        scopes=list(auth_raw.get("scopes") or []),
        env_var=auth_raw.get("env_var"),
        notes=str(notes_raw).strip() if notes_raw else None,
    )

    tools_raw = data.get("tools") or {}
    if not isinstance(tools_raw, dict):
        raise CatalogError(f"{path}: 'tools' must be a mapping")
    default_enabled = tools_raw.get("default_enabled")
    if default_enabled is not None:
        if not isinstance(default_enabled, list) or not all(
            isinstance(t, str) for t in default_enabled
        ):
            raise CatalogError(
                f"{path}: tools.default_enabled must be a list of strings"
            )
    tools_spec = ToolsSpec(default_enabled=default_enabled)

    install: Optional[InstallSpec] = None
    install_raw = data.get("install")
    if install_raw is not None:
        if not isinstance(install_raw, dict):
            raise CatalogError(f"{path}: 'install' must be a mapping")
        i_type = install_raw.get("type")
        if i_type != "git":
            raise CatalogError(f"{path}: install.type must be 'git' (got {i_type!r})")
        url = install_raw.get("url") or ""
        ref = install_raw.get("ref") or ""
        if not url or not ref:
            raise CatalogError(f"{path}: install.url and install.ref are required")
        bootstrap = install_raw.get("bootstrap") or []
        if not isinstance(bootstrap, list):
            raise CatalogError(f"{path}: install.bootstrap must be a list")
        install = InstallSpec(
            type=i_type,
            url=url,
            ref=ref,
            bootstrap=[str(c) for c in bootstrap],
        )

    return CatalogEntry(
        name=name,
        description=description,
        source=source,
        transport=transport,
        auth=auth,
        tools=tools_spec,
        install=install,
        post_install=str(data.get("post_install") or ""),
        disabled=bool(data.get("disabled", False)),
        manifest_path=path,
    )


def list_catalog() -> List[CatalogEntry]:
    """Return all valid catalog entries, sorted by name.

    Invalid manifests are skipped silently (CI tests catch them at PR time).
    Manifests with a future ``manifest_version`` are also skipped, but the
    skip is surfaced via :func:`catalog_diagnostics` so the picker / catalog
    UIs can tell the user their Hermes is out of date.
    """
    root = _catalog_root()
    if not root.exists():
        return []
    entries: List[CatalogEntry] = []
    _CATALOG_DIAGNOSTICS.clear()
    for child in sorted(root.iterdir()):
        manifest = child / "manifest.yaml"
        if not manifest.is_file():
            continue
        try:
            entries.append(_parse_manifest(manifest))
        except CatalogError as exc:
            msg = str(exc)
            # Recognize the future-manifest error specifically so the UI can
            # surface a more actionable nudge than "broken manifest".
            if "manifest_version" in msg and "unsupported" in msg:
                _CATALOG_DIAGNOSTICS.append((child.name, "future_manifest", msg))
            else:
                _CATALOG_DIAGNOSTICS.append((child.name, "invalid", msg))
            continue
    return entries


# Populated by list_catalog(). Inspected by the picker / catalog UIs so the
# user gets actionable feedback instead of a silently-shorter list.
_CATALOG_DIAGNOSTICS: List[tuple] = []


def catalog_diagnostics() -> List[tuple]:
    """Diagnostics from the most recent :func:`list_catalog` call.

    Returns a list of ``(entry_name, kind, message)`` tuples where ``kind``
    is one of:
      - ``future_manifest`` — manifest_version is newer than this Hermes
        understands. Update Hermes to install this entry.
      - ``invalid`` — manifest is malformed in some other way (caught by
        CI for shipped manifests; user-modified manifests can hit this).
    """
    return list(_CATALOG_DIAGNOSTICS)


def get_entry(name: str) -> Optional[CatalogEntry]:
    """Look up a single entry by name. ``official/<name>`` prefix accepted."""
    if name.startswith("official/"):
        name = name[len("official/"):]
    for entry in list_catalog():
        if entry.name == name:
            return entry
    return None


# ─── Status helpers ──────────────────────────────────────────────────────────


def installed_servers() -> Dict[str, dict]:
    """Return current ``mcp_servers`` block from config.yaml."""
    cfg = load_config()
    servers = cfg.get("mcp_servers") or {}
    return servers if isinstance(servers, dict) else {}


def is_installed(name: str) -> bool:
    return name in installed_servers()


def is_enabled(name: str) -> bool:
    servers = installed_servers()
    cfg = servers.get(name)
    if not cfg:
        return False
    enabled = cfg.get("enabled", True)
    if isinstance(enabled, str):
        return enabled.lower() in {"true", "1", "yes"}
    return bool(enabled)


# ─── Install ─────────────────────────────────────────────────────────────────


def _install_root() -> Path:
    """Where git-bootstrapped MCPs are cloned. Per-user, profile-aware."""
    root = get_hermes_home() / "mcp-installs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _adapt_bootstrap_command(cmd: str) -> str:
    """Rewrite a manifest bootstrap command for the current platform/runtime.

    Manifests are authored with Unix conventions (`python3`, `.venv/bin/...`).
    A literal `python3` is always replaced with the interpreter actually
    running Hermes (`sys.executable`), rather than relying on the child
    process's PATH to resolve `python3` correctly. This matters most for the
    Desktop app: launched from Finder/Explorer (not a login shell), it often
    only sees a minimal system PATH with no Homebrew/pyenv entries -- a bare
    `python3` there resolves to the OS-bundled Python (e.g. macOS's ancient
    CommandLineTools Python 3.9 with pip 21.2.4), which fails to resolve
    modern packages (``Could not find a version that satisfies the
    requirement mcp>=1.0.0``). ``sys.executable`` is guaranteed to be a
    working, modern-enough interpreter since it's the one running Hermes.

    On Windows, venv executables additionally live under `.venv/Scripts/`,
    not `.venv/bin/`.
    """
    # Replacement is a callable (not a plain string) so re.sub doesn't try to
    # interpret backslashes in a Windows sys.executable path (e.g.
    # `C:\Python311\python.exe`) as regex backreference escapes.
    replacement = f'"{sys.executable}"'
    adapted = re.sub(
        r"(?<![\w./-])python3(?![\w.-])", lambda _match: replacement, cmd
    )
    if sys.platform == "win32":
        adapted = _adapt_venv_bootstrap_for_windows(adapted)
    return adapted


def _adapt_venv_bootstrap_for_windows(cmd: str) -> str:
    """Rewrite ``.venv/bin/<exe>`` invocations so ``cmd.exe`` can run them.

    Bootstrap commands run through the shell. On Windows that is ``cmd.exe``,
    which does not accept forward slashes in the *program* position
    (``.venv/Scripts/pip`` fails with "'.venv' is not recognized"), and venv
    launchers need their ``.exe`` suffix. ``pip`` is additionally routed
    through ``python.exe -m pip`` — the ``pip.exe`` shim embeds the venv's
    absolute path and breaks when the install dir is later moved (AIS-286,
    SUP-20260903-101450).
    """
    def _rewrite(match: "re.Match[str]") -> str:
        prefix, exe = match.group(1), match.group(2)
        base = (prefix + _VENV_BIN_WINDOWS).replace("/", "\\")
        if exe in ("pip", "pip3"):
            return f'"{base}python.exe" -m pip'
        if exe in ("python", "python3"):
            return f'"{base}python.exe"'
        exe_name = exe if exe.lower().endswith(".exe") else f"{exe}.exe"
        return f'"{base}{exe_name}"'

    # ``prefix`` = whatever precedes ``.venv/bin/`` in the same token (an
    # absolute install dir or nothing); ``exe`` = the launcher name.
    return re.sub(r'"?([^\s"]*?)\.venv/bin/([A-Za-z0-9_.-]+)"?', _rewrite, cmd)


def _run_bootstrap(cwd: Path, commands: List[str]) -> None:
    """Execute bootstrap commands in *cwd*. Raise CatalogError on first failure.

    Commands are expanded for ``${INSTALL_DIR}`` -- the same placeholder used
    in ``transport.command``/``args`` -- since manifests reference it (e.g.
    ``pip install -r ${INSTALL_DIR}/optional-mcps/<name>/requirements.txt``)
    and it was previously never substituted here, silently expanding to an
    empty string via the shell and pointing `pip install -r` at a
    nonexistent path. Commands are then adapted for the current platform
    (see ``_adapt_bootstrap_command``). Each command runs through the shell
    (so `&&` etc. work). The output is streamed to the user's terminal for
    visibility.
    """
    for cmd in commands:
        expanded = cmd.replace(_INSTALL_DIR_VAR, str(cwd))
        expanded = _adapt_bootstrap_command(expanded)
        print(color(f"  $ {expanded}", Colors.DIM))
        proc = subprocess.run(expanded, cwd=str(cwd), shell=True, **_hidden_window_kwargs())
        if proc.returncode != 0:
            raise CatalogError(
                f"bootstrap step failed (exit {proc.returncode}): {expanded}"
            )


def _hidden_window_kwargs() -> Dict[str, Any]:
    """``creationflags`` that stop each install subprocess from flashing a
    console window on Windows (the dashboard runs ``hermes mcp install`` as a
    detached child, so every ``git``/``pip`` call would otherwise pop up its
    own window for a moment). No-op elsewhere."""
    flags = windows_hide_flags()
    return {"creationflags": flags} if flags else {}


def _do_git_install(entry: CatalogEntry) -> Path:
    """Clone the entry's repo into ``~/.hermes/mcp-installs/<name>`` and run
    bootstrap commands. Returns the install directory."""
    assert entry.install is not None and entry.install.type == "git"
    install = entry.install
    dest = _install_root() / entry.name

    git = shutil.which("git")

    if dest.exists():
        # Fresh checkout each install — manifest version is the source of truth,
        # so wipe + re-clone for determinism.
        print(color(f"  Removing existing install at {dest}", Colors.DIM))
        shutil.rmtree(dest)

    if not git:
        # Typical on a customer's Windows machine: no Git for Windows. GitHub
        # serves every branch/tag/SHA as a zip archive, which is all a catalog
        # install needs (no history is required; ``installed_commit`` simply
        # reports None). Non-GitHub sources still need git.
        archive_url = _github_archive_url(install.url, install.ref)
        if not archive_url:
            raise CatalogError(
                "git is required to install this MCP but was not found on PATH "
                f"(and {install.url} offers no archive download). Install Git "
                "(https://git-scm.com/downloads) and retry."
            )
        print(color(f"  git not found — downloading {archive_url} → {dest}", Colors.CYAN))
        _download_archive_install(archive_url, dest)
        if install.bootstrap:
            _run_bootstrap(dest, install.bootstrap)
        return dest

    print(color(f"  Cloning {install.url} ({install.ref}) → {dest}", Colors.CYAN))

    # `git clone --branch` only accepts branches and tags, NOT commit SHAs.
    # Detecting SHA-shaped refs upfront avoids a guaranteed stderr leak on
    # the fast path (the --branch attempt would always fail noisily for a
    # SHA ref before we fall back to full-clone-then-checkout).
    is_sha_ref = bool(re.fullmatch(r"[0-9a-f]{7,40}", install.ref))

    if not is_sha_ref:
        proc = subprocess.run(
            [git, "clone", "--depth", "1", "--branch", install.ref, install.url, str(dest)],
            **_hidden_window_kwargs(),
        )
        if proc.returncode == 0:
            pass
        else:
            # Branch/tag form failed (unlikely for valid manifests; possible if
            # the ref was deleted upstream). Fall through to the full-clone path.
            if dest.exists():
                shutil.rmtree(dest)
            is_sha_ref = True  # treat the same as a SHA ref from here

    if is_sha_ref:
        proc = subprocess.run([git, "clone", install.url, str(dest)], **_hidden_window_kwargs())
        if proc.returncode != 0:
            raise CatalogError(f"git clone failed for {install.url}")
        proc = subprocess.run([git, "-C", str(dest), "checkout", install.ref], **_hidden_window_kwargs())
        if proc.returncode != 0:
            raise CatalogError(f"git checkout {install.ref} failed")

    if install.bootstrap:
        _run_bootstrap(dest, install.bootstrap)

    return dest


def _github_archive_url(repo_url: str, ref: str) -> Optional[str]:
    """``https://github.com/<org>/<repo>(.git)`` + ref → codeload zip URL, else None."""
    m = re.match(r"^https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", (repo_url or "").strip())
    if not m or not ref:
        return None
    org, repo = m.group(1), m.group(2)
    return f"https://codeload.github.com/{org}/{repo}/zip/{ref}"


def _download_archive_install(archive_url: str, dest: Path, *, timeout: int = 180) -> Path:
    """Download a repository zip archive and unpack it to ``dest``.

    GitHub archives contain a single top-level ``<repo>-<ref>/`` folder; that
    folder becomes ``dest`` so the layout matches a ``git clone``.
    """
    import io
    import tempfile
    import urllib.request
    import zipfile

    req = urllib.request.Request(archive_url, headers={"User-Agent": "hermes-mcp-catalog"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https codeload URL built above
            data = resp.read()
    except Exception as exc:
        raise CatalogError(f"archive download failed for {archive_url}: {exc}") from exc
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise CatalogError(f"archive download for {archive_url} is not a zip file") from exc

    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hermes-mcp-archive-", dir=str(dest.parent)) as tmp:
        tmp_path = Path(tmp)
        for member in archive.infolist():
            # Zip-slip guard: every entry must stay inside the temp dir.
            target = (tmp_path / member.filename).resolve()
            if tmp_path.resolve() not in target.parents and target != tmp_path.resolve():
                raise CatalogError(f"archive entry escapes extraction dir: {member.filename}")
        archive.extractall(tmp_path)
        entries = [p for p in tmp_path.iterdir() if p.name != "__MACOSX"]
        source = entries[0] if len(entries) == 1 and entries[0].is_dir() else tmp_path
        shutil.move(str(source), str(dest))
    return dest


def installed_commit(install_dir: Path) -> Optional[str]:
    """Return the commit an install directory currently sits on.

    Recorded at install time so a later run can tell whether the clone in
    ``~/.hermes/mcp-installs/<name>`` still matches the manifest ref. Without
    it a catalog install is opaque: nothing on disk says which version of the
    server code is actually running, so a fix that shipped weeks ago can keep
    failing on a client with no way to notice.
    """
    git = shutil.which("git")
    if not git or not (install_dir / ".git").exists():
        return None
    proc = subprocess.run(
        [git, "-C", str(install_dir), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None

    return proc.stdout.strip() or None


def _expand_install_dir(value: str, install_dir: Optional[Path]) -> str:
    if _INSTALL_DIR_VAR not in value:
        return value
    if install_dir is None:
        raise CatalogError(
            f"manifest references {_INSTALL_DIR_VAR} but no install block exists"
        )
    return value.replace(_INSTALL_DIR_VAR, str(install_dir))


def _adapt_venv_executable_path(value: str) -> str:
    """Rewrite a manifest's `.venv/bin/...` transport command for Windows.

    Manifests are authored with Unix conventions. On Windows a venv's
    executables live under `.venv/Scripts/` (not `.venv/bin/`) and need an
    `.exe` suffix, since the MCP client spawns the transport command
    directly (no shell) and Windows requires an exact executable path.
    """
    if sys.platform != "win32" or _VENV_BIN_UNIX not in value:
        return value
    adapted = value.replace(_VENV_BIN_UNIX, _VENV_BIN_WINDOWS)
    if not adapted.lower().endswith(".exe"):
        adapted += ".exe"
    return adapted


def _prompt_env_vars(
    specs: List[EnvVarSpec], *, reprompt: bool = False, persist: bool = True,
) -> Dict[str, str]:
    """Walk the env spec list, prompting the user for each. Writes secrets and
    non-secrets alike to ~/.hermes/.env via save_env_value() when *persist*.

    If reprompt=False and a variable is already set, keeps existing value.
    If reprompt=True or variable is missing, prompts with existing value as default,
    so pressing Enter keeps existing token/value.

    Args:
        persist: Save collected values to the shared ~/.hermes/.env. Must be
            False when installing a *secondary* named instance of a
            multi-instance-capable entry (e.g. a second AtlassianMCP/
            TempoMCP instance) -- every instance shares the same env-var
            *names* (JIRA_URL, JIRA_PERSONAL_TOKEN, ...), so persisting a
            second instance's values to the shared .env would silently
            collide with the default instance's values. Callers installing
            a secondary instance must instead embed the collected values as
            literal strings directly in that instance's own
            ``mcp_servers.<name>.env`` block (see ``_build_server_config``'s
            ``literal_env`` parameter).
    """
    collected: Dict[str, str] = {}
    for spec in specs:
        existing = get_env_value(spec.name) if persist else None
        if existing and not reprompt:
            print(color(f"  ✓ {spec.name} already set in .env", Colors.GREEN))
            collected[spec.name] = existing
            continue
        default_val = existing or spec.default or None
        value = _prompt_input(
            spec.prompt,
            default=default_val,
            password=spec.secret,
        )
        if not value:
            if spec.required:
                raise CatalogError(f"{spec.name} is required but no value was provided")
            continue
        if persist:
            save_env_value(spec.name, value)
        collected[spec.name] = value
    return collected


def _github_device_code_login(
    entry: "CatalogEntry", collected: Dict[str, str]
) -> Optional[str]:
    """Run GitHub's OAuth device-code flow (Copilot client id)."""
    print(color("  Starting GitHub OAuth device code login...", Colors.CYAN))
    from hermes_cli.copilot_auth import copilot_device_code_login

    return copilot_device_code_login()


def _enable_m365_toolset_for_cli() -> tuple[bool, Optional[str]]:
    """Ensure MSOffice365MCP is installed and enabled in Hermes config.yaml."""
    try:
        entry = get_entry("MSOffice365MCP")
        if not entry:
            return False, "MSOffice365MCP catalog entry not found"
        install_entry(entry, enable=True, skip_auth_prompt=True)
        return True, None
    except Exception as exc:
        return False, str(exc)


# Backwards compatibility alias
_enable_outlook_toolset_for_cli = _enable_m365_toolset_for_cli


def _microsoft_device_code_login(
    entry: "CatalogEntry", collected: Dict[str, str]
) -> Optional[str]:
    """Run Microsoft's MSAL device-code flow, mirroring
    optional-mcps/MSOffice365MCP/server.py's ``_get_msal_app()``.

    Defaults to the same multi-tenant client id used by the MSOffice365MCP
    server when the manifest's collected env values (``M365_CLIENT_ID`` /
    ``M365_TENANT_ID``) are blank.
    """
    try:
        from tools.lazy_deps import ensure as _lazy_ensure, FeatureUnavailable
    except ImportError as exc:
        print(color(
            f"  ✗ The 'msal' package is required for Microsoft OAuth device "
            f"code login. Install it with: pip install msal ({exc})",
            Colors.YELLOW,
        ))
        return None

    try:
        _lazy_ensure("provider.msal", prompt=False)
        import msal
    except FeatureUnavailable as exc:
        print(color(
            f"  ✗ The 'msal' package is required for Microsoft OAuth device "
            f"code login. {exc}",
            Colors.YELLOW,
        ))
        return None

    client_id = collected.get("M365_CLIENT_ID") or "41c29967-8ee6-4fac-b484-e87460272bda"
    tenant_id = collected.get("M365_TENANT_ID") or "organizations"
    if tenant_id == "common":
        tenant_id = "organizations"

    from hermes_cli.m365_auth import (
        M365_LOGIN_SCOPES,
        build_admin_consent_url,
        classify_m365_auth_error,
        get_msal_app,
        save_msal_cache,
    )

    print(color("  Starting Microsoft OAuth device code login...", Colors.CYAN))
    app = get_msal_app(client_id=client_id, tenant_id=tenant_id)
    # Self-consent tier (AIS-286): identical to the dashboard button and the
    # chat tool. A manifest may narrow it further but never widen it, so a
    # non-admin install can't run into "Need admin approval".
    manifest_scopes = [sc for sc in (entry.auth.scopes or []) if sc in M365_LOGIN_SCOPES]
    flow = app.initiate_device_flow(scopes=manifest_scopes or list(M365_LOGIN_SCOPES))
    if "user_code" not in flow:
        classified = classify_m365_auth_error(flow)
        print(color(f"  ✗ Could not start Microsoft sign-in: {classified.message}", Colors.YELLOW))
        return None
    print(color(
        f"  Open {flow['verification_uri']} and enter code: {flow['user_code']}",
        Colors.CYAN,
    ))
    result = app.acquire_token_by_device_flow(flow)
    if result and "access_token" in result:
        save_msal_cache(app)
        _enable_m365_toolset_for_cli()
        return result["access_token"]
    classified = classify_m365_auth_error(result or "Microsoft sign-in did not complete")
    print(color(f"  ✗ Microsoft sign-in failed: {classified.message}", Colors.YELLOW))
    if classified.admin_consent_required:
        print(color(
            "  A tenant administrator must approve the app once for your organization:\n"
            f"  {build_admin_consent_url(client_id=client_id, tenant_id=tenant_id)}",
            Colors.YELLOW,
        ))
    return None


# Provider name -> handler that runs that provider's device-code login flow
# and returns the resulting access token (or None on failure/incompletion).
# Add new providers here rather than branching in install_entry().
_DEVICE_CODE_PROVIDERS: Dict[str, "Callable[[CatalogEntry, Dict[str, str]], Optional[str]]"] = {
    "github": _github_device_code_login,
    "microsoft": _microsoft_device_code_login,
}

# Human-readable display names for the messages printed around a device-code
# login (e.g. "Saved GitHub OAuth token..."). Falls back to the raw provider
# string (from AuthSpec.provider) for any provider not listed here.
_DEVICE_CODE_PROVIDER_LABELS: Dict[str, str] = {
    "github": "GitHub",
    "microsoft": "Microsoft",
}


def _build_server_config(
    entry: CatalogEntry,
    install_dir: Optional[Path],
    *,
    literal_env: Optional[Dict[str, str]] = None,
) -> dict:
    """Translate a manifest into the ``mcp_servers.<name>`` block format used
    by hermes_cli/mcp_config.py.

    Args:
        literal_env: When given, write these values as literal strings in
            the ``env`` block instead of ``${VAR}`` placeholders resolved
            against the shared ~/.hermes/.env at connect time. Required for
            a *secondary* named instance of a multi-instance-capable entry
            (e.g. a second AtlassianMCP/TempoMCP instance) -- every instance
            shares the same env-var names, so ``${VAR}`` interpolation would
            always resolve to the default instance's value, not this
            instance's own value. Falls back to today's ``${VAR}``
            interpolation for any declared env var not present in this dict.
    """
    from hermes_cli.config import get_env_value

    cfg: dict = {}
    t = entry.transport
    if t.type == "stdio":
        cfg["command"] = _adapt_venv_executable_path(
            _expand_install_dir(t.command or "", install_dir)
        )
        if t.args:
            cfg["args"] = [_expand_install_dir(a, install_dir) for a in t.args]
        if entry.auth and entry.auth.env:
            env_map = {}
            for item in entry.auth.env:
                literal_val = (literal_env or {}).get(item.name)
                if literal_val and str(literal_val).strip():
                    env_map[item.name] = literal_val
                    continue
                val = get_env_value(item.name)
                if not val and item.name == "JIRA_PERSONAL_TOKEN":
                    # JIRA_PAT was this field's name before it was renamed to match
                    # what mcp-atlassian actually reads for Server/DC PAT auth
                    # (JIRA_PAT was never a real mcp-atlassian env var, so it never
                    # worked). Fall back to any value users already saved under the
                    # old name so existing installs keep working without re-entry.
                    val = get_env_value("JIRA_PAT")
                if val and str(val).strip():
                    env_map[item.name] = f"${{{item.name}}}"
            if env_map:
                cfg["env"] = env_map
    elif t.type == "http":
        cfg["url"] = t.url
        headers = dict(t.headers) if t.headers else {}
        if entry.auth.type == "oauth":
            # If a token has already been obtained for this entry's env_var
            # (a PAT the user pasted in, or one collected via the device-code
            # login flow at install time), send it as a bearer header to the
            # remote server -- this is the only auth path a generic (not
            # GitHub-preregistered) MCP host can rely on for e.g. GitHub's
            # hosted MCP server. Fall back to native MCP OAuth 2.1 (handled
            # by the MCP client at first connect) only when no token/env_var
            # is configured at all.
            token_present = bool(
                entry.auth.env_var and get_env_value(entry.auth.env_var)
                and str(get_env_value(entry.auth.env_var)).strip()
            )
            if token_present:
                headers["Authorization"] = f"Bearer ${{{entry.auth.env_var}}}"
            else:
                cfg["auth"] = "oauth"
        if headers:
            cfg["headers"] = headers
    return cfg


def _read_prior_tool_selection(name: str) -> Optional[List[str]]:
    """Return the user's prior `tools.include` for *name*, if any.

    Used during reinstalls so the install-time checklist starts pre-checked
    with whatever the user already had. Tools no longer on the server are
    silently dropped at checklist-display time.
    """
    servers = installed_servers()
    cfg = servers.get(name) or {}
    tools_cfg = cfg.get("tools") or {}
    if not isinstance(tools_cfg, dict):
        return None
    include = tools_cfg.get("include")
    if isinstance(include, list) and all(isinstance(t, str) for t in include):
        return list(include)
    return None


def _probe_tools(name: str) -> Optional[List[tuple]]:
    """Connect to a freshly-configured MCP and list its tools.

    Returns a list of ``(tool_name, description)`` tuples on success, or
    ``None`` on any failure (server unreachable, OAuth not yet completed,
    backing service offline, etc.). Failures are intentionally swallowed
    here — the fallback path in :func:`_apply_tool_selection` handles them.
    """
    servers = installed_servers()
    server_cfg = servers.get(name)
    if not server_cfg:
        return None
    try:
        # Import lazily so the catalog module stays cheap to load.
        from hermes_cli.mcp_config import _probe_single_server

        tools = _probe_single_server(name, server_cfg)
        return list(tools) if tools is not None else []
    except Exception as exc:
        # Display the cause but never raise from the install path.
        print(color(f"  Probe failed: {exc}", Colors.YELLOW))
        return None


def _write_tools_include(name: str, include: Optional[List[str]]) -> None:
    """Persist or clear ``mcp_servers.<name>.tools.include``."""
    cfg = load_config()
    servers = cfg.setdefault("mcp_servers", {})
    server_entry = servers.get(name) or {}
    if include is None:
        # No filter — drop any existing tools block.
        server_entry.pop("tools", None)
    else:
        tools_block = server_entry.get("tools") or {}
        if not isinstance(tools_block, dict):
            tools_block = {}
        tools_block["include"] = list(include)
        tools_block.pop("exclude", None)
        server_entry["tools"] = tools_block
    servers[name] = server_entry
    cfg["mcp_servers"] = servers
    save_config(cfg)


def _apply_tool_selection(
    entry: CatalogEntry, *, prior_selection: Optional[List[str]],
    server_name: Optional[str] = None,
) -> None:
    """Probe the server and let the user pick which tools to enable.

    Probe-success path:
      - Curses checklist of all probed tools.
      - Pre-check uses (in priority order):
          1. *prior_selection* (reinstall: preserve what the user had)
          2. manifest's ``tools.default_enabled``
          3. all tools (default)
      - All-on selection clears any filter (no ``tools.include`` written).
      - Sub-selection writes ``tools.include``.

    Probe-fail path:
      - If manifest declares ``tools.default_enabled`` → apply directly.
      - Otherwise → leave config with no filter (all on when reachable).
      - Either way, point the user at ``hermes mcp configure <name>``.

    Args:
        server_name: The config.yaml ``mcp_servers`` key this entry was
            installed under, when it differs from ``entry.name`` (see
            ``install_entry``'s ``instance_name`` parameter). Falls back to
            ``entry.name`` when omitted.
    """
    server_name = server_name or entry.name
    # Reinstall keeps the user's prior selection but never below the manifest's
    # current defaults — otherwise tools added to ``default_enabled`` after the
    # first install would stay hidden forever (AIS-288). Runtime applies the
    # same union in tools.mcp_tool for installs that are not reinstalled.
    if prior_selection is not None and entry.tools.default_enabled:
        missing = [t for t in entry.tools.default_enabled if t not in prior_selection]
        if missing:
            print(color(f"  Adding {len(missing)} new default tool(s) from the manifest: {', '.join(missing)}", Colors.DIM))
            prior_selection = list(prior_selection) + missing
    import sys as _sys
    if not _sys.stdin.isatty():
        if prior_selection is not None:
            _write_tools_include(server_name, prior_selection)
        elif entry.tools.default_enabled:
            _write_tools_include(server_name, entry.tools.default_enabled)
        else:
            _write_tools_include(server_name, None)
        return

    print()
    print(color(f"  Probing '{server_name}' for available tools...", Colors.CYAN))
    probed = _probe_tools(server_name)

    # Probe failure path
    if probed is None:
        manifest_default = entry.tools.default_enabled
        if manifest_default:
            _write_tools_include(server_name, manifest_default)
            print(color(
                f"  Couldn\'t probe server. Applied manifest default "
                f"({len(manifest_default)} tools). "
                f"Run `hermes mcp configure {server_name}` after the server "
                "is reachable to refine.",
                Colors.YELLOW,
            ))
        else:
            _write_tools_include(server_name, None)
            print(color(
                f"  Couldn\'t probe server; installed with no tool filter "
                "(all tools enabled when reachable). "
                f"Run `hermes mcp configure {server_name}` after first "
                "connect to prune.",
                Colors.YELLOW,
            ))
        return

    if not probed:
        # Probe succeeded but server reported zero tools. Nothing to filter.
        _write_tools_include(server_name, None)
        print(color("  Server reported no tools.", Colors.YELLOW))
        return

    tool_names = [t[0] for t in probed]

    # Build the pre-checked set in priority order
    if prior_selection:
        pre_set = {n for n in prior_selection if n in tool_names}
    elif entry.tools.default_enabled:
        pre_set = {n for n in entry.tools.default_enabled if n in tool_names}
    else:
        pre_set = set(tool_names)

    pre_indices = {i for i, n in enumerate(tool_names) if n in pre_set}

    # Non-TTY: skip the checklist. Priority matches the interactive
    # pre-check priority: prior user selection > manifest default > all-on.
    import sys as _sys
    if not _sys.stdin.isatty():
        if prior_selection is not None:
            include = [n for n in prior_selection if n in tool_names]
            _write_tools_include(server_name, include)
        elif entry.tools.default_enabled:
            include = [n for n in entry.tools.default_enabled if n in tool_names]
            _write_tools_include(server_name, include)
        else:
            _write_tools_include(server_name, None)
        return

    print(color(
        f"  Found {len(probed)} tool(s). "
        f"Pre-checked: {len(pre_indices)}.",
        Colors.GREEN,
    ))

    from hermes_cli.curses_ui import curses_checklist

    labels = [
        f"{n}  —  {(d[:60] + '...') if len(d) > 60 else d}"
        for n, d in probed
    ]
    chosen_indices = curses_checklist(
        f"Select tools for '{server_name}' (SPACE toggle, ENTER confirm)",
        labels,
        pre_indices,
    )

    if not chosen_indices:
        # User unchecked everything; treat as "no tools" — write empty include
        # so the server is installed but contributes nothing until reconfigured.
        _write_tools_include(server_name, [])
        print(color(
            f"  No tools selected. Run `hermes mcp configure {server_name}` "
            "to change.",
            Colors.YELLOW,
        ))
        return

    if len(chosen_indices) == len(probed):
        # Everything selected — clear filter for the cleanest config shape.
        # NOTE: this means any tools the server adds later (e.g. a future MCP
        # version) will also be auto-enabled. To pin to the current set,
        # the user can re-run `hermes mcp configure <name>` and unselect a
        # tool to switch back to include-mode.
        _write_tools_include(server_name, None)
        print(color(
            f"  ✓ All {len(probed)} tools enabled (no filter — new tools "
            "the server adds later will be auto-enabled).",
            Colors.GREEN,
        ))
        return

    chosen_names = [tool_names[i] for i in sorted(chosen_indices)]
    _write_tools_include(server_name, chosen_names)
    print(color(
        f"  ✓ {len(chosen_names)}/{len(probed)} tools enabled.",
        Colors.GREEN,
    ))


def list_instances(catalog_name: str) -> List[str]:
    """Return the config.yaml ``mcp_servers`` keys backing *catalog_name*.

    Matches by exact name first, then by the same catalog-name-suffix
    convention used for tool-description-note resolution (see
    ``tools/mcp_tool.py::_lookup_tool_description_note``) — this lets a
    renamed/duplicate instance like "EVNAtlassianMCP" (backing the
    "AtlassianMCP" catalog entry) be discovered even though its config key
    doesn't match the catalog name exactly. Used to populate the "existing
    instances" dropdown for catalog entries that support multiple named
    instances (currently AtlassianMCP, TempoMCP).
    """
    servers = installed_servers()
    return [
        name for name in servers
        if name == catalog_name or name.endswith(catalog_name)
    ]


def install_entry(
    entry: CatalogEntry, *, enable: bool = True, reprompt: bool = False,
    skip_auth_prompt: bool = False, instance_name: Optional[str] = None,
    literal_env: Optional[Dict[str, str]] = None,
) -> None:
    """Install a catalog entry end-to-end.

    Steps:
        1. If ``install.type == git``, clone + run bootstrap commands.
        2. If ``auth.type == api_key``, prompt for env vars, save to .env.
        3. If ``auth.type == oauth`` (remote MCP / case 1), write the
           ``auth: oauth`` marker (MCP client handles browser on first connect
           in the non-pre-authenticated case).
        4. Translate the manifest into an ``mcp_servers.<name>`` block and
           save into config.yaml.
        5. Probe the server, present a curses checklist for tool selection,
           write ``tools.include`` (or no filter, depending on choice).
           If probe fails, fall back to the manifest's
           ``tools.default_enabled`` or all-on.
        6. Print post_install notes.

    Args:
        skip_auth_prompt: Skip step 2/3's ``_prompt_env_vars`` call entirely.
            Set by callers (the web dashboard's install endpoint) that
            already collected and saved credentials themselves via
            ``save_env_value``/``remove_env_value`` before calling this
            function. Without this, ``_prompt_env_vars`` would call
            ``input()`` for every still-unset optional field -- there's no
            interactive terminal behind a web request, so that call either
            raises (non-TTY stdin closed/redirected) or hangs indefinitely
            (stdin inherited from a non-terminal process), silently
            aborting the install before the mcp_servers.<name> config block
            is ever written.
        instance_name: Config key to install this entry under, when it
            differs from ``entry.name``. Lets one catalog entry (currently
            only offered in the UI for AtlassianMCP/TempoMCP) be installed
            more than once under different names — e.g. a second Jira
            Server/DC tenant configured as "EVNAtlassianMCP" alongside the
            default "AtlassianMCP". Falls back to ``entry.name`` when omitted
            so every other catalog entry keeps today's single-instance
            behavior unchanged.
        literal_env: Explicit env-var values (name -> value) to embed
            literally in this instance's own ``mcp_servers.<name>.env``
            block, bypassing ``${VAR}`` interpolation against the shared
            ~/.hermes/.env. Required whenever ``instance_name`` differs from
            ``entry.name`` (a *secondary* instance) since every instance
            declares the same env-var names -- interpolating against the
            shared .env would always resolve to the default instance's
            value. Used by the web dashboard's install endpoint, which
            collects a secondary instance's credentials from the request
            body instead of prompting/persisting to .env (see
            ``skip_auth_prompt``). When omitted for a secondary instance
            installed interactively (CLI path, ``skip_auth_prompt=False``),
            values collected via ``_prompt_env_vars(..., persist=False)``
            are used instead.
    """
    server_name = instance_name or entry.name
    is_secondary_instance = server_name != entry.name
    print()
    print(color(f"  Installing MCP '{server_name}'", Colors.CYAN + Colors.BOLD))
    if entry.description:
        print(color(f"  {entry.description}", Colors.DIM))
    if entry.source:
        print(color(f"  Source: {entry.source}", Colors.DIM))
    print()

    install_dir: Optional[Path] = None
    install_commit: Optional[str] = None
    if entry.install is not None:
        install_dir = _do_git_install(entry)
        install_commit = installed_commit(install_dir)

    # Auth
    collected_env: Dict[str, str] = dict(literal_env or {})
    if skip_auth_prompt:
        pass
    elif entry.auth.type == "api_key":
        print()
        print(color("  Configure credentials:", Colors.CYAN))
        if entry.auth.notes:
            print(color(f"  ℹ {entry.auth.notes}", Colors.DIM))
        collected_env.update(
            _prompt_env_vars(
                entry.auth.env, reprompt=reprompt, persist=not is_secondary_instance,
            )
        )
    elif entry.auth.type == "oauth":
        collected: Dict[str, str] = {}
        if entry.auth.env:
            print()
            print(color("  Configure credentials:", Colors.CYAN))
            if entry.auth.notes:
                print(color(f"  ℹ {entry.auth.notes}", Colors.DIM))
            collected = _prompt_env_vars(
                entry.auth.env, reprompt=reprompt, persist=not is_secondary_instance,
            )
            collected_env.update(collected)
        elif entry.auth.notes:
            print()
            print(color(f"  ℹ {entry.auth.notes}", Colors.DIM))

        # Whether we already have the actual access token (entry.auth.env_var)
        # -- not just any collected config field (e.g. Microsoft's manifest
        # also collects M365_CLIENT_ID/M365_TENANT_ID via entry.auth.env,
        # neither of which is the token itself).
        if entry.auth.env_var:
            has_token = bool(
                (collected.get(entry.auth.env_var) or get_env_value(entry.auth.env_var) or "").strip()
            )
        else:
            has_token = any(v and v.strip() for v in collected.values())

        import sys as _sys
        handler = _DEVICE_CODE_PROVIDERS.get(entry.auth.provider) if entry.auth.provider else None
        if not has_token and handler:
            label = _DEVICE_CODE_PROVIDER_LABELS.get(entry.auth.provider, entry.auth.provider)
            if _sys.stdin.isatty():
                token: Optional[str] = None
                try:
                    token = handler(entry, collected)
                except Exception as exc:
                    print(color(f"  ✗ {label} OAuth failed: {exc}", Colors.YELLOW))
                env_var = entry.auth.env_var
                if token and env_var:
                    from hermes_cli.config import save_env_value
                    save_env_value(env_var, token)
                    print(color(f"  ✓ Saved {label} OAuth token to ~/.hermes/.env", Colors.GREEN))
                else:
                    later = f" you can set {env_var} later." if env_var else " you can authenticate later."
                    print(color(f"  ⚠ {label} OAuth incomplete;{later}", Colors.YELLOW))
            else:
                print(color(f"  {label} OAuth required. Complete via OAuth/Settings in UI.", Colors.DIM))
        elif entry.auth.provider:
            print(color(
                f"  This MCP uses {entry.auth.provider} OAuth. Run "
                f"`hermes auth {entry.auth.provider}` if you have not "
                "already authenticated.",
                Colors.YELLOW,
            ))
        else:
            print(color(
                "  This MCP uses native OAuth 2.1; tokens will be acquired "
                "on first connection (browser flow).",
                Colors.DIM,
            ))
    # auth.type == "none": nothing to do.

    # ── Preserve any prior user tool selection across reinstalls ────────
    # Reading BEFORE we overwrite the entry below so a reinstall pre-checks
    # whatever the user picked last time.
    prior_selection = _read_prior_tool_selection(server_name)

    # Build and write the mcp_servers entry (without tools filter yet;
    # _apply_tool_selection() finalizes it below).
    server_cfg = _build_server_config(
        entry, install_dir,
        literal_env=collected_env if is_secondary_instance else None,
    )
    server_cfg["enabled"] = enable
    if entry.install is not None and install_dir is not None:
        # Provenance for `hermes mcp update`. The clone is never touched again
        # after install, so this is the only record of which code is running.
        server_cfg["install_source"] = {
            "url": entry.install.url,
            "ref": entry.install.ref,
            "commit": install_commit or "",
            "dir": str(install_dir),
            "installed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }

    cfg = load_config()
    cfg.setdefault("mcp_servers", {})[server_name] = server_cfg
    save_config(cfg)

    # ── Probe + tool selection ──────────────────────────────────────────
    _apply_tool_selection(entry, prior_selection=prior_selection, server_name=server_name)

    print()
    print(color(
        f"  ✓ Installed '{server_name}' "
        f"({'enabled' if enable else 'disabled'}). "
        f"Start a new Hermes session to load its tools.",
        Colors.GREEN,
    ))
    if entry.post_install:
        print()
        for line in entry.post_install.strip().splitlines():
            print(color(f"  {line}", Colors.DIM))
    print()


def uninstall_entry(name: str, *, purge_install_dir: bool = True) -> bool:
    """Remove a catalog-installed MCP from config and (optionally) wipe its
    clone directory. Returns True if anything was removed."""
    cfg = load_config()
    servers = cfg.get("mcp_servers") or {}
    removed = False
    if name in servers:
        del servers[name]
        if not servers:
            cfg.pop("mcp_servers", None)
        else:
            cfg["mcp_servers"] = servers
        save_config(cfg)
        removed = True

    if purge_install_dir:
        clone = _install_root() / name
        if clone.exists():
            shutil.rmtree(clone)
            removed = True

    return removed
