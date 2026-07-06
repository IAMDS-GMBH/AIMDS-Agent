#!/bin/bash
# ============================================================================
# Hermes Agent Installer
# ============================================================================
# Installation script for Linux, macOS, and Android/Termux.
# Uses uv for desktop/server installs and Python's stdlib venv + pip on Termux.
#
# Usage:
#   curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
#
# Or with options:
#   curl -fsSL ... | bash -s -- --no-venv --skip-setup
#
# ============================================================================

set -e

# Guard against environment leakage when the installer is launched from another
# Python-driven tool session (e.g. Hermes terminal tool). A pre-set PYTHONPATH
# can force pip/entrypoints to import a different checkout than the one being
# installed, which makes fresh installs appear broken or stale.
if [ -n "${PYTHONPATH:-}" ]; then
    echo "⚠ Ignoring inherited PYTHONPATH during install to avoid module shadowing"
    unset PYTHONPATH
fi
if [ -n "${PYTHONHOME:-}" ]; then
    echo "⚠ Ignoring inherited PYTHONHOME during install"
    unset PYTHONHOME
fi

# Prevent uv from discovering config files (uv.toml, pyproject.toml) from the
# wrong user's home directory when running under sudo -u <user>.  See #21269.
export UV_NO_CONFIG=1

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color
BOLD='\033[1m'

# Configuration
REPO_URL_SSH="git@github.com:IAMDS-GMBH/hermes-agent.git"
REPO_URL_HTTPS="https://github.com/IAMDS-GMBH/hermes-agent.git"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
# INSTALL_DIR is resolved AFTER arg parsing and OS detection so we can pick an
# FHS-style layout for root installs.  Track whether the user gave us an
# explicit directory — if so we never override it.
if [ -n "${HERMES_INSTALL_DIR:-}" ]; then
    INSTALL_DIR="$HERMES_INSTALL_DIR"
    INSTALL_DIR_EXPLICIT=true
else
    INSTALL_DIR=""
    INSTALL_DIR_EXPLICIT=false
fi
PYTHON_VERSION="3.11"
NODE_VERSION="22"

# FHS-style root install layout (set by resolve_install_layout when applicable):
#   code at /usr/local/lib/hermes-agent, command at /usr/local/bin/hermes,
#   data still at /root/.hermes (HERMES_HOME).  Matches Claude Code / Codex CLI
#   and keeps Docker bind-mounted /root/ volumes lean.
ROOT_FHS_LAYOUT=false
DETECTED_BROWSER_EXECUTABLE=""

# Options
USE_VENV=true
RUN_SETUP=true
SKIP_BROWSER=false
NO_SKILLS=false
BRANCH="main"
INSTALL_COMMIT=""
ENSURE_DEPS=""
POSTINSTALL_MODE=false
MANIFEST_MODE=false
STAGE_NAME=""
JSON_OUTPUT=false
NON_INTERACTIVE=false
INCLUDE_DESKTOP=false

# Detect non-interactive mode (e.g. curl | bash)
# When stdin is not a terminal, read -p will fail with EOF,
# causing set -e to silently abort the entire script.
if [ -t 0 ]; then
    IS_INTERACTIVE=true
else
    IS_INTERACTIVE=false
fi

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-venv)
            USE_VENV=false
            shift
            ;;
        --skip-setup)
            RUN_SETUP=false
            shift
            ;;
        --skip-browser|--no-playwright)
            SKIP_BROWSER=true
            shift
            ;;
        --no-skills)
            NO_SKILLS=true
            shift
            ;;
        --branch|-Branch)
            BRANCH="$2"
            shift 2
            ;;
        --commit|-Commit)
            INSTALL_COMMIT="$2"
            shift 2
            ;;
        --manifest|-Manifest)
            MANIFEST_MODE=true
            shift
            ;;
        --stage|-Stage)
            STAGE_NAME="$2"
            shift 2
            ;;
        --json|-Json)
            JSON_OUTPUT=true
            shift
            ;;
        --non-interactive|-NonInteractive)
            NON_INTERACTIVE=true
            shift
            ;;
        --include-desktop|-IncludeDesktop)
            INCLUDE_DESKTOP=true
            shift
            ;;
        --dir)
            INSTALL_DIR="$2"
            INSTALL_DIR_EXPLICIT=true
            shift 2
            ;;
        --hermes-home)
            HERMES_HOME="$2"
            shift 2
            ;;
        --ensure)
            ENSURE_DEPS="$2"
            shift 2
            ;;
        --postinstall)
            POSTINSTALL_MODE=true
            shift
            ;;
        -h|--help)
            echo "Hermes Agent Installer"
            echo ""
            echo "Usage: install.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --no-venv      Don't create virtual environment"
            echo "  --skip-setup   Skip interactive setup wizard"
            echo "  --skip-browser Skip Playwright/Chromium install (browser tools won't work)"
            echo "  --no-skills    Start with a blank slate — seed no bundled skills, and"
            echo "                   write \$HERMES_HOME/.no-bundled-skills so future"
            echo "                   'hermes update' runs never inject bundled skills either"
            echo "  --branch NAME  Git branch to install (default: main)"
            echo "  --commit SHA   Pin checkout to a specific commit after clone/update"
            echo "  --manifest     Print desktop bootstrap stage manifest as JSON"
            echo "  --stage NAME   Run one desktop bootstrap stage"
            echo "  --json         Print a JSON result frame for --stage"
            echo "  --non-interactive  Skip stages that require user input"
            echo "  --include-desktop  Also build the desktop app (apps/desktop -> Hermes.app)"
            echo "  --dir PATH     Installation directory"
            echo "                   default (non-root):  ~/.hermes/hermes-agent"
            echo "                   default (root, Linux): /usr/local/lib/hermes-agent"
            echo "  --hermes-home PATH  Data directory (default: ~/.hermes, or \$HERMES_HOME)"
            echo "  -h, --help     Show this help"
            echo ""
            echo "Notes:"
            echo "  When running as root on Linux, Hermes installs the code under"
            echo "  /usr/local/lib/hermes-agent and links the command into"
            echo "  /usr/local/bin/hermes (FHS layout — matches Claude Code / Codex CLI)."
            echo "  Data, config, sessions, and logs still live in \$HERMES_HOME"
            echo "  (default /root/.hermes).  This keeps Docker bind-mounted volumes"
            echo "  small and ensures the command is on PATH for all shells."
            echo "  Existing installs at \$HERMES_HOME/hermes-agent are preserved in-place."
            echo "  --ensure DEPS  Install only specified deps (comma-separated)"
            echo "                   Supported: node, browser, ripgrep, ffmpeg"
            echo "                   Does NOT clone repo or create venv"
            echo "  --postinstall  Run post-install setup only (for pip users)"
            echo "                   Installs optional deps + runs hermes setup"
            echo "                   Does NOT clone repo or create venv"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ============================================================================
# Helper functions
# ============================================================================

print_banner() {
    echo ""
    echo -e "${MAGENTA}${BOLD}"
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│             ⚕ Hermes Agent Installer                    │"
    echo "├─────────────────────────────────────────────────────────┤"
    echo "│  An open source AI agent by Nous Research.              │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo -e "${NC}"
}

log_info() {
    echo -e "${CYAN}→${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}✗${NC} $1"
}

json_escape() {
    # Enough for short installer status strings; avoids requiring jq during
    # pre-install bootstrap.
    printf '%s' "$1" | tr '\n' ' ' | sed \
        -e 's/\\/\\\\/g' \
        -e 's/"/\\"/g'
}

# npm rewrites tracked package-lock.json files non-deterministically during
# `npm install` / `npm run pack`. On a managed install those diffs are never
# intentional, but they leave the checkout dirty — which forces `hermes update`
# to autostash on every run and makes branch switches fragile. Restore them so
# a fresh install ends with a clean tree. Best-effort; only touches lockfiles.
restore_dirty_lockfiles() {
    local repo="${1:-$INSTALL_DIR}"
    [ -n "$repo" ] && [ -d "$repo/.git" ] || return 0
    command -v git >/dev/null 2>&1 || return 0
    local dirty
    dirty=$(git -C "$repo" diff --name-only 2>/dev/null | grep 'package-lock\.json$' || true)
    [ -z "$dirty" ] && return 0
    echo "$dirty" | while IFS= read -r f; do
        [ -n "$f" ] && git -C "$repo" checkout -- "$f" 2>/dev/null || true
    done
}

emit_manifest() {
    # Stage-Desktop is included only with --include-desktop, mirroring
    # install.ps1: the signed bootstrap installer (Hermes-Setup) passes it so
    # a GUI install ends up with a launchable app; the Electron app's own
    # first-launch bootstrap and the CLI one-liner omit it (building the
    # desktop from inside the already-running app would clobber it).
    local desktop_stage=""
    if [ "$INCLUDE_DESKTOP" = true ]; then
        desktop_stage='{"name":"desktop","title":"Build desktop app","category":"runtime","needs_user_input":false},'
    fi
    printf '%s' '{"protocol_version":1,"stages":[{"name":"prerequisites","title":"System prerequisites","category":"runtime","needs_user_input":false},{"name":"repository","title":"Download Hermes Agent","category":"runtime","needs_user_input":false},{"name":"venv","title":"Create Python virtual environment","category":"runtime","needs_user_input":false},{"name":"python-deps","title":"Install Python dependencies","category":"runtime","needs_user_input":false},{"name":"node-deps","title":"Install browser-tool dependencies","category":"runtime","needs_user_input":false},{"name":"path","title":"Install hermes command","category":"runtime","needs_user_input":false},{"name":"config","title":"Prepare config and skills","category":"configuration","needs_user_input":false},{"name":"setup","title":"Configure API keys and settings","category":"configuration","needs_user_input":true},{"name":"gateway","title":"Configure gateway service","category":"configuration","needs_user_input":true},'"$desktop_stage"'{"name":"complete","title":"Finish install","category":"runtime","needs_user_input":false}]}'
    printf '\n'
}

stage_needs_user_input() {
    case "$1" in
        setup|gateway) return 0 ;;
        *) return 1 ;;
    esac
}

emit_stage_json() {
    local stage="$1"
    local ok="$2"
    local skipped="${3:-false}"
    local reason="${4:-}"
    local escaped_reason
    escaped_reason="$(json_escape "$reason")"
    if [ -n "$escaped_reason" ]; then
        printf '{"ok":%s,"stage":"%s","skipped":%s,"reason":"%s"}\n' "$ok" "$stage" "$skipped" "$escaped_reason"
    else
        printf '{"ok":%s,"stage":"%s","skipped":%s}\n' "$ok" "$stage" "$skipped"
    fi
}

prompt_yes_no() {
    local question="$1"
    local default="${2:-yes}"
    local prompt_suffix
    local answer=""

    # Use case patterns (not ${var,,}) so this works on bash 3.2 (macOS /bin/bash).
    case "$default" in
        [yY]|[yY][eE][sS]|[tT][rR][uU][eE]|1) prompt_suffix="[Y/n]" ;;
        *) prompt_suffix="[y/N]" ;;
    esac

    if [ "$NON_INTERACTIVE" = true ]; then
        answer=""
    elif [ "$IS_INTERACTIVE" = true ]; then
        read -r -p "$question $prompt_suffix " answer || answer=""
    elif [ -r /dev/tty ] && [ -w /dev/tty ]; then
        printf "%s %s " "$question" "$prompt_suffix" > /dev/tty
        IFS= read -r answer < /dev/tty || answer=""
    else
        answer=""
    fi

    answer="${answer#"${answer%%[![:space:]]*}"}"
    answer="${answer%"${answer##*[![:space:]]}"}"

    if [ -z "$answer" ]; then
        case "$default" in
            [yY]|[yY][eE][sS]|[tT][rR][uU][eE]|1) return 0 ;;
            *) return 1 ;;
        esac
    fi

    case "$answer" in
        [yY]|[yY][eE][sS]) return 0 ;;
        *) return 1 ;;
    esac
}

is_termux() {
    [ -n "${TERMUX_VERSION:-}" ] || [[ "${PREFIX:-}" == *"com.termux/files/usr"* ]]
}

# Decide where the repo checkout + venv live, and where the `hermes` command
# symlink goes.  Called after detect_os so $OS/$DISTRO are known.
#
# Defaults:
#   - Non-root, any OS:       INSTALL_DIR = $HERMES_HOME/hermes-agent
#                             command link in $HOME/.local/bin
#   - Termux (any uid):       INSTALL_DIR = $HERMES_HOME/hermes-agent
#                             command link in $PREFIX/bin (already on PATH)
#   - Root on Linux (new):    INSTALL_DIR = /usr/local/lib/hermes-agent
#                             command link in /usr/local/bin
#                             (unless a legacy install already exists at
#                              $HERMES_HOME/hermes-agent — then preserve it)
#
# Always no-op when the user set --dir or $HERMES_INSTALL_DIR.
resolve_install_layout() {
    if [ "$INSTALL_DIR_EXPLICIT" = true ]; then
        log_info "Install directory: $INSTALL_DIR (explicit)"
        return 0
    fi

    # Termux: package manager manages /data/data/..., keep code in HERMES_HOME.
    if is_termux; then
        INSTALL_DIR="$HERMES_HOME/hermes-agent"
        return 0
    fi

    # Root on Linux: prefer FHS layout unless a legacy install already exists.
    # macOS root installs keep the legacy layout because /usr/local/ on macOS
    # is Homebrew territory and we don't want to fight that.
    if [ "$OS" = "linux" ] && [ "$(id -u)" -eq 0 ]; then
        if [ -d "$HERMES_HOME/hermes-agent/.git" ]; then
            INSTALL_DIR="$HERMES_HOME/hermes-agent"
            log_info "Existing install detected at $INSTALL_DIR — keeping legacy layout"
            log_info "  (new root installs use /usr/local/lib/hermes-agent)"
            return 0
        fi
        INSTALL_DIR="/usr/local/lib/hermes-agent"
        ROOT_FHS_LAYOUT=true
        # Place uv-managed Python under /usr/local/share so the venv interpreter
        # is world-readable.  Default uv paths land in /root/.local/share/uv,
        # which non-root users can't traverse — leaving the shared
        # /usr/local/bin/hermes wrapper unable to exec the bad-interpreter venv
        # python.  See #21457.
        export UV_PYTHON_INSTALL_DIR="${UV_PYTHON_INSTALL_DIR:-/usr/local/share/uv/python}"
        export UV_PYTHON_BIN_DIR="${UV_PYTHON_BIN_DIR:-/usr/local/share/uv/bin}"
        log_info "Root install on Linux — using FHS layout"
        log_info "  Code:    $INSTALL_DIR"
        log_info "  Command: /usr/local/bin/hermes"
        log_info "  Data:    $HERMES_HOME (unchanged)"
        log_info "  uv Python: $UV_PYTHON_INSTALL_DIR (world-readable)"
        return 0
    fi

    # Default: non-root, non-Termux → legacy user-scoped layout.
    INSTALL_DIR="$HERMES_HOME/hermes-agent"
}

get_command_link_dir() {
    if is_termux && [ -n "${PREFIX:-}" ]; then
        echo "$PREFIX/bin"
    elif [ "$ROOT_FHS_LAYOUT" = true ]; then
        echo "/usr/local/bin"
    else
        echo "$HOME/.local/bin"
    fi
}

get_command_link_display_dir() {
    if is_termux && [ -n "${PREFIX:-}" ]; then
        echo '$PREFIX/bin'
    elif [ "$ROOT_FHS_LAYOUT" = true ]; then
        echo '/usr/local/bin'
    else
        echo '~/.local/bin'
    fi
}

get_hermes_command_path() {
    local link_dir
    link_dir="$(get_command_link_dir)"
    if [ -x "$link_dir/hermes" ]; then
        echo "$link_dir/hermes"
    else
        echo "hermes"
    fi
}

# ============================================================================
# System detection
# ============================================================================

detect_os() {
    case "$(uname -s)" in
        Linux*)
            if is_termux; then
                OS="android"
                DISTRO="termux"
            else
                OS="linux"
                if [ -f /etc/os-release ]; then
                    . /etc/os-release
                    DISTRO="$ID"
                else
                    DISTRO="unknown"
                fi
            fi
            ;;
        Darwin*)
            OS="macos"
            DISTRO="macos"
            ;;
        CYGWIN*|MINGW*|MSYS*)
            OS="windows"
            DISTRO="windows"
            log_error "Windows detected. Please use the PowerShell installer:"
            log_info "  iex (irm https://hermes-agent.nousresearch.com/install.ps1)"
            exit 1
            ;;
        *)
            OS="unknown"
            DISTRO="unknown"
            log_warn "Unknown operating system"
            ;;
    esac

    log_success "Detected: $OS ($DISTRO)"
}

# ============================================================================
# Dependency checks
# ============================================================================

install_uv() {
    if [ "$DISTRO" = "termux" ]; then
        log_info "Termux detected — using Python's stdlib venv + pip instead of uv"
        UV_CMD=""
        return 0
    fi

    # Hermes owns its own uv at $HERMES_HOME/bin/uv.  Always install there —
    # no PATH probing, no conda guards, no multi-location resolution chains.
    # The runtime update path (hermes_cli/managed_uv.py) looks in the same
    # place, so install.sh and `hermes update` stay in sync.
    local _managed_uv="$HERMES_HOME/bin/uv"

    if [ -x "$_managed_uv" ]; then
        UV_CMD="$_managed_uv"
        UV_VERSION=$($UV_CMD --version 2>/dev/null)
        log_success "Managed uv found ($UV_VERSION)"
        return 0
    fi

    log_info "Installing managed uv into $HERMES_HOME/bin ..."
    mkdir -p "$HERMES_HOME/bin"

    # Two-stage: download the installer, then run it.  Piping
    # `curl | sh` masks curl failures (sh exits 0 on empty stdin)
    # and conflates network errors with installer errors.
    local _uv_install_log _uv_installer
    _uv_install_log="$(mktemp 2>/dev/null || echo "/tmp/hermes-uv-install.$$.log")"
    _uv_installer="$(mktemp 2>/dev/null || echo "/tmp/hermes-uv-installer.$$.sh")"
    if ! curl -LsSf https://astral.sh/uv/install.sh -o "$_uv_installer" 2>"$_uv_install_log"; then
        log_error "Failed to download uv installer from https://astral.sh/uv/install.sh"
        log_info "curl output:"
        sed 's/^/    /' "$_uv_install_log" >&2
        log_info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        rm -f "$_uv_install_log" "$_uv_installer"
        exit 1
    fi
    # UV_UNMANAGED_INSTALL tells the astral installer to place the binary
    # directly into $HERMES_HOME/bin instead of ~/.local/bin.
    if UV_UNMANAGED_INSTALL="$HERMES_HOME/bin" sh "$_uv_installer" >>"$_uv_install_log" 2>&1; then
        rm -f "$_uv_installer"
        if [ -x "$_managed_uv" ]; then
            UV_CMD="$_managed_uv"
        else
            log_error "uv installer reported success but binary not found at $_managed_uv"
            log_info "Installer output:"
            sed 's/^/    /' "$_uv_install_log" >&2
            rm -f "$_uv_install_log"
            exit 1
        fi
        rm -f "$_uv_install_log"
        UV_VERSION=$($UV_CMD --version 2>/dev/null)
        log_success "Managed uv installed ($UV_VERSION)"
    else
        log_error "Failed to install uv"
        log_info "Installer output:"
        sed 's/^/    /' "$_uv_install_log" >&2
        log_info "Install manually: https://docs.astral.sh/uv/getting-started/installation/"
        rm -f "$_uv_install_log" "$_uv_installer"
        exit 1
    fi
}

check_python() {
    if [ "$DISTRO" = "termux" ]; then
        log_info "Checking Termux Python..."
        if command -v python >/dev/null 2>&1; then
            PYTHON_PATH="$(command -v python)"
            if "$PYTHON_PATH" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
                PYTHON_FOUND_VERSION="$("$PYTHON_PATH" --version 2>/dev/null)"
                log_success "Python found: $PYTHON_FOUND_VERSION"
                return 0
            fi
        fi

        log_info "Installing Python via pkg..."
        pkg install -y python >/dev/null
        PYTHON_PATH="$(command -v python)"
        PYTHON_FOUND_VERSION="$("$PYTHON_PATH" --version 2>/dev/null)"
        log_success "Python installed: $PYTHON_FOUND_VERSION"
        return 0
    fi

    log_info "Checking Python $PYTHON_VERSION..."

    # Let uv handle Python — it can download and manage Python versions
    # First check if a suitable Python is already available
    if PYTHON_PATH="$("$UV_CMD" python find "$PYTHON_VERSION" 2>/dev/null)"; then
        PYTHON_FOUND_VERSION="$("$PYTHON_PATH" --version 2>/dev/null)"
        log_success "Python found: $PYTHON_FOUND_VERSION"
        return 0
    fi

    # Python not found — use uv to install it (no sudo needed!)
    log_info "Python $PYTHON_VERSION not found, installing via uv..."
    if "$UV_CMD" python install "$PYTHON_VERSION"; then
        PYTHON_PATH="$("$UV_CMD" python find "$PYTHON_VERSION")"
        PYTHON_FOUND_VERSION="$("$PYTHON_PATH" --version 2>/dev/null)"
        log_success "Python installed: $PYTHON_FOUND_VERSION"
    else
        log_error "Failed to install Python $PYTHON_VERSION"
        log_info "Install Python $PYTHON_VERSION manually, then re-run this script"
        exit 1
    fi
}

# Best-effort automatic git provisioning, mirroring install.ps1's Install-Git
# (which downloads PortableGit on Windows). git is required to clone the repo,
# and a fresh "normie" machine with no developer tools won't have it. Returns 0
# if git is available afterwards, non-zero otherwise (caller prints manual
# instructions and aborts).
attempt_install_git() {
    case "$OS" in
        macos)
            # Prefer Homebrew — fully headless when present.
            if command -v brew >/dev/null 2>&1; then
                log_info "Installing Git via Homebrew..."
                brew install git >/dev/null 2>&1 || true
                command -v git >/dev/null 2>&1 && return 0
            fi
            # Fall back to Apple Command Line Tools, which provide git AND the
            # compiler some Python wheels need. `xcode-select --install` pops a
            # system dialog (Apple gates CLT behind it — it cannot be fully
            # silent without MDM), so we trigger it and poll for git to appear.
            if command -v xcode-select >/dev/null 2>&1; then
                log_info "Requesting Apple Command Line Tools (provides git + compiler)..."
                log_info "If a macOS dialog appears, click \"Install\" and accept the license."
                xcode-select --install >/dev/null 2>&1 || true
                local waited=0
                local timeout=900
                while [ "$waited" -lt "$timeout" ]; do
                    if command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1; then
                        return 0
                    fi
                    sleep 5
                    waited=$((waited + 5))
                    if [ $((waited % 60)) -eq 0 ]; then
                        log_info "Still waiting for Command Line Tools install ($((waited / 60))m)..."
                    fi
                done
            fi
            return 1
            ;;
        linux)
            local sudo_cmd=""
            if [ "$(id -u 2>/dev/null || echo 1000)" -ne 0 ]; then
                command -v sudo >/dev/null 2>&1 && sudo_cmd="sudo"
            fi
            case "$DISTRO" in
                ubuntu|debian)
                    log_info "Installing Git via apt..."
                    $sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get update -qq >/dev/null 2>&1 || true
                    $sudo_cmd env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git >/dev/null 2>&1 || true
                    ;;
                fedora)
                    log_info "Installing Git via dnf..."
                    $sudo_cmd dnf install -y git >/dev/null 2>&1 || true
                    ;;
                arch)
                    log_info "Installing Git via pacman..."
                    $sudo_cmd pacman -S --noconfirm git >/dev/null 2>&1 || true
                    ;;
                *)
                    return 1
                    ;;
            esac
            command -v git >/dev/null 2>&1 && return 0
            return 1
            ;;
    esac
    return 1
}

check_git() {
    log_info "Checking Git..."

    # On fresh macOS /usr/bin/git is a stub that exits non-zero until CLT is installed.
    if command -v git &> /dev/null && git --version &> /dev/null; then
        GIT_VERSION=$(git --version | awk '{print $3}')
        log_success "Git $GIT_VERSION found"
        return 0
    fi

    log_error "Git not found"

    if [ "$DISTRO" = "termux" ]; then
        log_info "Installing Git via pkg..."
        pkg install -y git >/dev/null
        if command -v git >/dev/null 2>&1; then
            GIT_VERSION=$(git --version | awk '{print $3}')
            log_success "Git $GIT_VERSION installed"
            return 0
        fi
    fi

    # Try to install it automatically before giving up (parity with install.ps1).
    log_info "Attempting to install Git automatically..."
    if attempt_install_git; then
        GIT_VERSION=$(git --version | awk '{print $3}')
        log_success "Git $GIT_VERSION installed"
        return 0
    fi

    log_warn "Could not install Git automatically. Please install it manually:"

    case "$OS" in
        linux)
            case "$DISTRO" in
                ubuntu|debian)
                    log_info "  sudo apt update && sudo apt install git"
                    ;;
                fedora)
                    log_info "  sudo dnf install git"
                    ;;
                arch)
                    log_info "  sudo pacman -S git"
                    ;;
                *)
                    log_info "  Use your package manager to install git"
                    ;;
            esac
            ;;
        android)
            log_info "  pkg install git"
            ;;
        macos)
            log_info "  xcode-select --install"
            log_info "  Or: brew install git"
            ;;
    esac

    exit 1
}

# The desktop build runs Vite ^8, which refuses to start on Node outside
# `^20.19 || >=22.12` — older Node lacks `node:util.styleText`, so `vite build`
# crashes with a SyntaxError that surfaces only as the opaque "Build desktop
# app … exit code 1" install failure. Returns 0 when the given `node --version`
# string clears that floor; anything below it is replaced with the Hermes-
# managed Node $NODE_VERSION LTS.
node_satisfies_build() {
    local ver="${1#v}"
    local major="${ver%%.*}"
    local minor="${ver#*.}"; minor="${minor%%.*}"
    case "$major" in ''|*[!0-9]*) return 1 ;; esac
    case "$minor" in ''|*[!0-9]*) minor=0 ;; esac
    if [ "$major" -eq 20 ] && [ "$minor" -ge 19 ]; then return 0; fi
    if [ "$major" -ge 22 ] && { [ "$major" -gt 22 ] || [ "$minor" -ge 12 ]; }; then return 0; fi
    return 1
}

check_node() {
    log_info "Checking Node.js (for browser tools)..."

    if command -v node &> /dev/null && node_satisfies_build "$(node --version)"; then
        log_success "Node.js $(node --version) found"
        HAS_NODE=true
        return 0
    fi

    # Prefer a Hermes-managed Node from a previous run over a too-old system one.
    if [ -x "$HERMES_HOME/node/bin/node" ] && node_satisfies_build "$("$HERMES_HOME/node/bin/node" --version)"; then
        export PATH="$HERMES_HOME/node/bin:$PATH"
        log_success "Node.js $("$HERMES_HOME/node/bin/node" --version) found (Hermes-managed)"
        HAS_NODE=true
        return 0
    fi

    if command -v node &> /dev/null; then
        log_warn "Node.js $(node --version) is too old for the desktop build (need ^20.19 or >=22.12) — installing Hermes-managed Node $NODE_VERSION LTS..."
    elif [ "$DISTRO" = "termux" ]; then
        log_info "Node.js not found — installing Node.js via pkg..."
    else
        log_info "Node.js not found — installing Node.js $NODE_VERSION LTS..."
    fi
    install_node
}

install_node() {
    if [ "$DISTRO" = "termux" ]; then
        log_info "Installing Node.js via pkg..."
        if pkg install -y nodejs >/dev/null; then
            local installed_ver
            installed_ver=$(node --version 2>/dev/null)
            log_success "Node.js $installed_ver installed via pkg"
            HAS_NODE=true
        else
            log_warn "Failed to install Node.js via pkg"
            HAS_NODE=false
        fi
        return 0
    fi

    local arch=$(uname -m)
    local node_arch
    case "$arch" in
        x86_64)        node_arch="x64"    ;;
        aarch64|arm64) node_arch="arm64"  ;;
        armv7l)        node_arch="armv7l" ;;
        *)
            log_warn "Unsupported architecture ($arch) for Node.js auto-install"
            log_info "Install manually: https://nodejs.org/en/download/"
            HAS_NODE=false
            return 0
            ;;
    esac

    local node_os
    case "$OS" in
        linux) node_os="linux"  ;;
        macos) node_os="darwin" ;;
        *)
            log_warn "Unsupported OS for Node.js auto-install"
            HAS_NODE=false
            return 0
            ;;
    esac

    # Resolve the latest v22.x.x tarball name from the index page
    local index_url="https://nodejs.org/dist/latest-v${NODE_VERSION}.x/"
    local tarball_name
    tarball_name=$(curl -fsSL "$index_url" \
        | grep -oE "node-v${NODE_VERSION}\.[0-9]+\.[0-9]+-${node_os}-${node_arch}\.tar\.xz" \
        | head -1)

    # Fallback to .tar.gz if .tar.xz not available
    if [ -z "$tarball_name" ]; then
        tarball_name=$(curl -fsSL "$index_url" \
            | grep -oE "node-v${NODE_VERSION}\.[0-9]+\.[0-9]+-${node_os}-${node_arch}\.tar\.gz" \
            | head -1)
    fi

    if [ -z "$tarball_name" ]; then
        log_warn "Could not find Node.js $NODE_VERSION binary for $node_os-$node_arch"
        log_info "Install manually: https://nodejs.org/en/download/"
        HAS_NODE=false
        return 0
    fi

    local download_url="${index_url}${tarball_name}"
    local tmp_dir
    tmp_dir=$(mktemp -d)

    log_info "Downloading $tarball_name..."
    if ! curl -fsSL "$download_url" -o "$tmp_dir/$tarball_name"; then
        log_warn "Download failed"
        rm -rf "$tmp_dir"
        HAS_NODE=false
        return 0
    fi

    log_info "Extracting to ~/.hermes/node/..."
    if [[ "$tarball_name" == *.tar.xz ]]; then
        tar xf "$tmp_dir/$tarball_name" -C "$tmp_dir"
    else
        tar xzf "$tmp_dir/$tarball_name" -C "$tmp_dir"
    fi

    local extracted_dir
    extracted_dir=$(ls -d "$tmp_dir"/node-v* 2>/dev/null | head -1)

    if [ ! -d "$extracted_dir" ]; then
        log_warn "Extraction failed"
        rm -rf "$tmp_dir"
        HAS_NODE=false
        return 0
    fi

    # Place into ~/.hermes/node/ and symlink binaries into the same bin dir
    # the hermes command uses (get_command_link_dir): /usr/local/bin for root
    # FHS installs, $PREFIX/bin on Termux, ~/.local/bin otherwise.
    rm -rf "$HERMES_HOME/node"
    mkdir -p "$HERMES_HOME"
    mv "$extracted_dir" "$HERMES_HOME/node"
    rm -rf "$tmp_dir"

    local node_link_dir
    node_link_dir="$(get_command_link_dir)"
    mkdir -p "$node_link_dir"
    ln -sf "$HERMES_HOME/node/bin/node" "$node_link_dir/node"
    ln -sf "$HERMES_HOME/node/bin/npm"  "$node_link_dir/npm"
    ln -sf "$HERMES_HOME/node/bin/npx"  "$node_link_dir/npx"

    export PATH="$HERMES_HOME/node/bin:$PATH"

    local installed_ver
    installed_ver=$("$HERMES_HOME/node/bin/node" --version 2>/dev/null)
    log_success "Node.js $installed_ver installed to ~/.hermes/node/"
    HAS_NODE=true
}

check_network_prerequisites() {
    log_info "Checking internet connectivity for package install and web tools..."

    local url
    local failed=false
    local checks=("https://pypi.org/simple/" "https://duckduckgo.com/")

    if ! command -v curl >/dev/null 2>&1; then
        log_warn "curl not found; skipping connectivity probes"
        return 0
    fi

    for url in "${checks[@]}"; do
        if ! curl -fsSI --max-time 8 "$url" >/dev/null 2>&1; then
            failed=true
            log_warn "Could not reach $url"
        fi
    done

    if [ "$failed" = false ]; then
        log_success "Internet connectivity looks good"
        return 0
    fi

    if [ "$DISTRO" = "termux" ]; then
        log_warn "Termux network prerequisites may be incomplete."
        log_info "Try: pkg install -y ca-certificates curl && pkg update"
        log_info "If mirrors are stale: termux-change-repo"
        log_info "Then test: curl -I https://pypi.org/simple/ && curl -I https://duckduckgo.com/"
    else
        log_warn "Network checks failed. Hermes install may complete, but web search and dependency downloads can fail."
        log_info "Verify internet/DNS and retry if pip install fails."
    fi
}

install_system_packages() {
    # Detect what's missing
    HAS_RIPGREP=false
    HAS_FFMPEG=false
    local need_ripgrep=false
    local need_ffmpeg=false

    log_info "Checking ripgrep (fast file search)..."
    if command -v rg &> /dev/null; then
        log_success "$(rg --version | head -1) found"
        HAS_RIPGREP=true
    else
        need_ripgrep=true
    fi

    log_info "Checking ffmpeg (TTS voice messages)..."
    if command -v ffmpeg &> /dev/null; then
        local ffmpeg_ver=$(ffmpeg -version 2>/dev/null | head -1 | awk '{print $3}')
        log_success "ffmpeg $ffmpeg_ver found"
        HAS_FFMPEG=true
    else
        need_ffmpeg=true
    fi

    # Termux always needs the Android build toolchain for the tested pip path,
    # even when ripgrep/ffmpeg are already present.
    if [ "$DISTRO" = "termux" ]; then
        local termux_pkgs=(clang rust make pkg-config libffi openssl ca-certificates curl)
        if [ "$need_ripgrep" = true ]; then
            termux_pkgs+=("ripgrep")
        fi
        if [ "$need_ffmpeg" = true ]; then
            termux_pkgs+=("ffmpeg")
        fi

        log_info "Installing Termux packages: ${termux_pkgs[*]}"
        if pkg install -y "${termux_pkgs[@]}" >/dev/null; then
            [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
            [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
            log_success "Termux build dependencies installed"
            return 0
        fi

        log_warn "Could not auto-install all Termux packages"
        log_info "Install manually: pkg install ${termux_pkgs[*]}"
        return 0
    fi

    # Nothing to install — done
    if [ "$need_ripgrep" = false ] && [ "$need_ffmpeg" = false ]; then
        return 0
    fi

    # Build a human-readable description + package list
    local desc_parts=()
    local pkgs=()
    if [ "$need_ripgrep" = true ]; then
        desc_parts+=("ripgrep for faster file search")
        pkgs+=("ripgrep")
    fi
    if [ "$need_ffmpeg" = true ]; then
        desc_parts+=("ffmpeg for TTS voice messages")
        pkgs+=("ffmpeg")
    fi
    local description
    description=$(IFS=" and "; echo "${desc_parts[*]}")

    # ── macOS: brew ──
    if [ "$OS" = "macos" ]; then
        if command -v brew &> /dev/null; then
            log_info "Installing ${pkgs[*]} via Homebrew..."
            if brew install "${pkgs[@]}"; then
                [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
                [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
                return 0
            fi
        fi
        log_warn "Could not auto-install (brew not found or install failed)"
        log_info "Install manually: brew install ${pkgs[*]}"
        return 0
    fi

    # ── Linux: resolve package manager command ──
    local pkg_install=""
    case "$DISTRO" in
        ubuntu|debian) pkg_install="apt install -y"   ;;
        fedora)        pkg_install="dnf install -y"   ;;
        arch)          pkg_install="pacman -S --noconfirm" ;;
    esac

    if [ -n "$pkg_install" ]; then
        local install_cmd="$pkg_install ${pkgs[*]}"

        # Prevent needrestart/whiptail dialogs from blocking non-interactive installs
        case "$DISTRO" in
            ubuntu|debian) export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a ;;
        esac

        # Already root — just install
        if [ "$(id -u)" -eq 0 ]; then
            log_info "Installing ${pkgs[*]}..."
            if $install_cmd; then
                [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
                [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
                return 0
            fi
        # Passwordless sudo — just install
        elif command -v sudo &> /dev/null && sudo -n true 2>/dev/null; then
            log_info "Installing ${pkgs[*]}..."
            if sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a $install_cmd; then
                [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
                [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
                return 0
            fi
        # sudo needs password — ask once for everything
        elif command -v sudo &> /dev/null; then
            if [ "$IS_INTERACTIVE" = true ]; then
                echo ""
                log_info "sudo is needed ONLY to install optional system packages (${pkgs[*]}) via your package manager."
                log_info "Hermes Agent itself does not require or retain root access."
                if prompt_yes_no "Install ${description}? (requires sudo)" "no"; then
                    if sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a $install_cmd; then
                        [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
                        [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
                        return 0
                    fi
                fi
            elif (: </dev/tty) 2>/dev/null; then
                # Non-interactive (e.g. curl | bash) but a terminal is available.
                # Read the prompt from /dev/tty (same approach the setup wizard uses).
                # Probe by actually opening /dev/tty: a bare existence test passes
                # in Docker builds where the device node is in the mount namespace
                # but opening fails with ENXIO. See #16746.
                echo ""
                log_info "sudo is needed ONLY to install optional system packages (${pkgs[*]}) via your package manager."
                log_info "Hermes Agent itself does not require or retain root access."
                if prompt_yes_no "Install ${description}?" "yes"; then
                    if sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a $install_cmd < /dev/tty; then
                        [ "$need_ripgrep" = true ] && HAS_RIPGREP=true && log_success "ripgrep installed"
                        [ "$need_ffmpeg" = true ]  && HAS_FFMPEG=true  && log_success "ffmpeg installed"
                        return 0
                    fi
                fi
            else
                log_warn "Non-interactive mode and no terminal available — cannot install system packages"
                log_info "Install manually after setup completes: sudo $install_cmd"
            fi
        fi
    fi

    # ── Fallback for ripgrep: cargo ──
    if [ "$need_ripgrep" = true ] && [ "$HAS_RIPGREP" = false ]; then
        if command -v cargo &> /dev/null; then
            log_info "Trying cargo install ripgrep (no sudo needed)..."
            if cargo install ripgrep; then
                log_success "ripgrep installed via cargo"
                HAS_RIPGREP=true
            fi
        fi
    fi

    # ── Show manual instructions for anything still missing ──
    if [ "$HAS_RIPGREP" = false ] && [ "$need_ripgrep" = true ]; then
        log_warn "ripgrep not installed (file search will use grep fallback)"
        show_manual_install_hint "ripgrep"
    fi
    if [ "$HAS_FFMPEG" = false ] && [ "$need_ffmpeg" = true ]; then
        log_warn "ffmpeg not installed (TTS voice messages will be limited)"
        show_manual_install_hint "ffmpeg"
    fi
}

show_manual_install_hint() {
    local pkg="$1"
    log_info "To install $pkg manually:"
    case "$OS" in
        linux)
            case "$DISTRO" in
                ubuntu|debian) log_info "  sudo apt install $pkg" ;;
                fedora)        log_info "  sudo dnf install $pkg" ;;
                arch)          log_info "  sudo pacman -S $pkg"   ;;
                *)             log_info "  Use your package manager or visit the project homepage" ;;
            esac
            ;;
        android)
            log_info "  pkg install $pkg"
            ;;
        macos) log_info "  brew install $pkg" ;;
    esac
}

# ============================================================================
# Installation
# ============================================================================

clone_repo() {
    log_info "Installing to $INSTALL_DIR..."

    # An interrupted previous clone leaves a .git with no initial commit, where
    # the update path's `git stash` / `git checkout` abort with "You do not
    # have the initial commit yet" and fail the install (#40998). Move such a
    # partial checkout aside -- never delete it, in case it holds something the
    # user wants -- so the fresh-clone path below can proceed.
    if [ -d "$INSTALL_DIR/.git" ] && ! git -C "$INSTALL_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
        backup_dir="${INSTALL_DIR}.broken-$(date -u +%Y%m%d-%H%M%S)"
        log_warn "Existing checkout at $INSTALL_DIR has no commits (interrupted clone)."
        log_warn "Moving it aside to $backup_dir before re-cloning."
        mv "$INSTALL_DIR" "$backup_dir"
    fi

    if [ -d "$INSTALL_DIR" ]; then
        if [ -d "$INSTALL_DIR/.git" ]; then
            log_info "Existing installation found, updating..."
            cd "$INSTALL_DIR"

            local autostash_ref=""
            if [ -n "$(git status --porcelain)" ]; then
                local stash_name
                stash_name="hermes-install-autostash-$(date -u +%Y%m%d-%H%M%S)"
                log_info "Local changes detected, stashing before update..."
                git stash push --include-untracked -m "$stash_name"
                autostash_ref="stash@{0}"
            fi

            # Fetch only the target branch. A bare `git fetch origin` pulls
            # every ref, and this repo carries thousands of auto-generated
            # branches — on a non-single-branch checkout that turns each update
            # into a multi-minute download that can stall the installer.
            git remote set-branches origin "$BRANCH" 2>/dev/null || true
            git fetch origin "$BRANCH"
            git checkout "$BRANCH"
            git pull --ff-only origin "$BRANCH"

            if [ -n "$autostash_ref" ]; then
                # Managed install checkout: default to NOT restoring stashed
                # local changes after update. Auto-restore reintroduces stale
                # patched files on every update (reported repeatedly in desktop
                # installs). Users can still restore explicitly from git stash.
                local restore_now="no"
                if [ -t 0 ] && [ -t 1 ]; then
                    echo
                    log_warn "Local changes were stashed before updating."
                    log_warn "Restoring them may reapply local customizations onto the updated codebase."
                    printf "Restore local changes now? [y/N] "
                    read -r restore_answer
                    case "$restore_answer" in
                        y|Y|yes|YES|Yes) restore_now="yes" ;;
                        *) restore_now="no" ;;
                    esac
                fi

                if [ "$restore_now" = "yes" ]; then
                    log_info "Restoring local changes..."
                    if git stash apply "$autostash_ref"; then
                        git stash drop "$autostash_ref" >/dev/null
                        log_warn "Local changes were restored on top of the updated codebase."
                        log_warn "Review git diff / git status if Hermes behaves unexpectedly."
                    else
                        log_error "Update succeeded, but restoring local changes failed. Your changes are still preserved in git stash."
                        log_info "Resolve manually with: git stash apply $autostash_ref"
                        exit 1
                    fi
                else
                    log_info "Skipped restoring local changes."
                    log_info "Your changes are still preserved in git stash."
                    log_info "Restore manually with: git stash apply $autostash_ref"
                fi
            fi
        else
            log_error "Directory exists but is not a git repository: $INSTALL_DIR"
            log_info "Remove it or choose a different directory with --dir"
            exit 1
        fi
    else
        # Try SSH first (for private repo access), fall back to HTTPS
        # GIT_SSH_COMMAND disables interactive prompts and sets a short timeout
        # so SSH fails fast instead of hanging when no key is configured.
        log_info "Trying SSH clone..."
        if GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=5" \
           git clone --depth 1 --branch "$BRANCH" "$REPO_URL_SSH" "$INSTALL_DIR" 2>/dev/null; then
            log_success "Cloned via SSH"
        else
            rm -rf "$INSTALL_DIR" 2>/dev/null  # Clean up partial SSH clone
            log_info "SSH failed, trying HTTPS..."
            if git clone --depth 1 --branch "$BRANCH" "$REPO_URL_HTTPS" "$INSTALL_DIR"; then
                log_success "Cloned via HTTPS"
            else
                log_error "Failed to clone repository"
                exit 1
            fi
        fi
    fi

    cd "$INSTALL_DIR"

    if [ -n "$INSTALL_COMMIT" ]; then
        log_info "Pinning checkout to commit $INSTALL_COMMIT..."
        if ! git cat-file -e "$INSTALL_COMMIT^{commit}" 2>/dev/null; then
            git fetch origin "$INSTALL_COMMIT" || true
        fi
        git checkout --detach "$INSTALL_COMMIT"
    fi

    log_success "Repository ready"
}

setup_venv() {
    if [ "$USE_VENV" = false ]; then
        log_info "Skipping virtual environment (--no-venv)"
        return 0
    fi

    if [ "$DISTRO" = "termux" ]; then
        log_info "Creating virtual environment with Termux Python..."

        if [ -d "venv" ]; then
            log_info "Virtual environment already exists, recreating..."
            rm -rf venv
        fi

        "$PYTHON_PATH" -m venv venv
        log_success "Virtual environment ready ($(./venv/bin/python --version 2>/dev/null))"
        return 0
    fi

    log_info "Creating virtual environment with Python $PYTHON_VERSION..."

    if [ -d "venv" ]; then
        log_info "Virtual environment already exists, recreating..."
        rm -rf venv
    fi

    # uv creates the venv and pins the Python version in one step
    $UV_CMD venv venv --python "$PYTHON_VERSION"

    # Neutralize any inherited UV_PYTHON (e.g. UV_PYTHON=3.14 left in the
    # user's shell env). uv honours UV_PYTHON over an existing venv for the
    # later `uv sync` / `uv pip install` tiers, so without this it would
    # silently delete this 3.11 venv and recreate it at the inherited
    # version — building Rust transitives that have no wheel for that
    # version from source via maturin, which fails. Pinning UV_PYTHON to the
    # interpreter we just created forces every subsequent uv command onto it.
    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        export UV_PYTHON="$INSTALL_DIR/venv/bin/python"
    fi

    log_success "Virtual environment ready (Python $PYTHON_VERSION)"
}

install_deps() {
    log_info "Installing dependencies..."

    # Sync version from git tag into pyproject.toml + hermes_cli/__init__.py
    # before the package is installed so `importlib.metadata.version("hermes-agent")`
    # returns the correct release tag at runtime.
    if command -v git >/dev/null 2>&1 && git -C "$INSTALL_DIR" rev-parse --git-dir >/dev/null 2>&1; then
        # Shallow clones don't fetch tags — fetch the nearest tag explicitly.
        git -C "$INSTALL_DIR" fetch --tags --depth=1 origin 2>/dev/null || true
        local _git_tag
        _git_tag=$(git -C "$INSTALL_DIR" describe --tags --abbrev=0 2>/dev/null || true)
        if [ -n "$_git_tag" ]; then
            local _py="${INSTALL_DIR}/venv/bin/python"
            if [ ! -x "$_py" ]; then _py="$PYTHON_PATH"; fi
            log_info "Syncing version from git tag $_git_tag ..."
            "$_py" "$INSTALL_DIR/scripts/set_version.py" "$_git_tag" 2>/dev/null || true
        fi
    fi

    # Re-pin UV_PYTHON to the venv interpreter. setup_venv already does this,
    # but the bootstrap runs install stages (`venv`, `python-deps`) as separate
    # processes, so an export from setup_venv does NOT survive into a separate
    # python-deps invocation. Re-deriving it here covers that path. Without it,
    # an inherited UV_PYTHON=3.14 makes the uv sync/pip tiers below recreate the
    # venv at 3.14 and fail the maturin source build (no cp314 wheels yet).
    if [ "$DISTRO" != "termux" ] && [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        export UV_PYTHON="$INSTALL_DIR/venv/bin/python"
    fi

    if [ "$DISTRO" = "termux" ]; then
        if [ "$USE_VENV" = true ]; then
            export VIRTUAL_ENV="$INSTALL_DIR/venv"
            PIP_PYTHON="$INSTALL_DIR/venv/bin/python"
        else
            PIP_PYTHON="$PYTHON_PATH"
        fi

        if [ -z "${ANDROID_API_LEVEL:-}" ]; then
            ANDROID_API_LEVEL="$(getprop ro.build.version.sdk 2>/dev/null || true)"
            if [ -z "$ANDROID_API_LEVEL" ]; then
                ANDROID_API_LEVEL=24
            fi
            export ANDROID_API_LEVEL
            log_info "Using ANDROID_API_LEVEL=$ANDROID_API_LEVEL for Android wheel builds"
        fi

        "$PIP_PYTHON" -m pip install --upgrade pip setuptools wheel >/dev/null

        # On Android, psutil's setup.py rejects sys.platform == 'android' before
        # it ever invokes the C build, so the next pip install would fail at
        # "platform android is not supported".  Prebuild psutil from the official
        # sdist with a one-line marker patch (Linux source path is fine on
        # Android).  Stopgap until psutil#2762 ships upstream.
        if "$PIP_PYTHON" -c 'import sys; raise SystemExit(0 if sys.platform == "android" else 1)' 2>/dev/null; then
            log_info "Android Python detected: prebuilding psutil compatibility shim..."
            if ! "$PIP_PYTHON" "$INSTALL_DIR/scripts/install_psutil_android.py" --pip "$PIP_PYTHON -m pip"; then
                log_warn "psutil Android prebuild failed — package install will likely fail next."
                log_info "Workaround: manually rerun 'python scripts/install_psutil_android.py' once your toolchain is set up."
            fi
        fi

        # Try the broad Termux profile first (best-effort "install all" for Android),
        # then fall back to the conservative Termux baseline, then base package.
        if ! "$PIP_PYTHON" -m pip install -e '.[termux-all]' -c constraints-termux.txt; then
            log_warn "Termux broad profile (.[termux-all]) failed, trying baseline Termux profile..."
            if ! "$PIP_PYTHON" -m pip install -e '.[termux]' -c constraints-termux.txt; then
                log_warn "Termux baseline profile (.[termux]) failed, trying base install..."
                if ! "$PIP_PYTHON" -m pip install -e '.' -c constraints-termux.txt; then
                    log_error "Package installation failed on Termux."
                    log_info "Ensure these packages are installed: pkg install clang rust make pkg-config libffi openssl ca-certificates curl"
                    log_info "Then re-run: cd $INSTALL_DIR && python -m pip install -e '.[termux-all]' -c constraints-termux.txt"
                    exit 1
                fi
            fi
        fi

        log_success "Main package installed"
        if ! ensure_ddgs_for_web_search; then
            log_error "ddgs installation failed. web_search backend 'ddgs' will not work."
            exit 1
        fi
        ensure_office_document_dependencies
        log_info "Termux note: matrix e2ee and local faster-whisper extras are excluded from .[termux-all] due to upstream Android wheel/toolchain blockers."
        log_info "Termux note: browser/WhatsApp tooling is not installed by default; see the Termux guide for optional follow-up steps."

        log_success "All dependencies installed"
        return 0
    fi

    if [ "$USE_VENV" = true ]; then
        # Tell uv to install into our venv (no need to activate)
        export VIRTUAL_ENV="$INSTALL_DIR/venv"
    fi

    # On Debian/Ubuntu (including WSL), some Python packages need build tools.
    # Check and offer to install them if missing.
    if [ "$DISTRO" = "ubuntu" ] || [ "$DISTRO" = "debian" ]; then
        local need_build_tools=false
        for pkg in gcc python3-dev libffi-dev; do
            if ! dpkg -s "$pkg" &>/dev/null; then
                need_build_tools=true
                break
            fi
        done
        if [ "$need_build_tools" = true ]; then
            log_info "Some build tools may be needed for Python packages..."
            if command -v sudo &> /dev/null; then
                if sudo -n true 2>/dev/null; then
                    sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y -qq build-essential python3-dev libffi-dev >/dev/null 2>&1 || true
                    log_success "Build tools installed"
                else
                    log_info "sudo is needed ONLY to install build tools (build-essential, python3-dev, libffi-dev) via apt."
                    log_info "Hermes Agent itself does not require or retain root access."
                    if prompt_yes_no "Install build tools?" "yes"; then
                        sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a apt-get install -y -qq build-essential python3-dev libffi-dev >/dev/null 2>&1 || true
                        log_success "Build tools installed"
                    fi
                fi
            fi
        fi
    fi

    # Install the main package in editable mode with all extras.
    #
    # Hash-verified install (Tier 0) — when uv.lock is present, prefer
    # `uv sync --locked`. The lockfile records SHA256 hashes for every
    # transitive, so a compromised transitive (different hash than what
    # we shipped) is REJECTED by the resolver. This is the *only* path
    # that protects against the "direct dep is fine, but the dep's dep
    # got worm-poisoned overnight" failure mode. All `uv pip install`
    # tiers below re-resolve transitives fresh from PyPI without any
    # hash verification — they exist to keep installs working when the
    # lockfile is stale, missing, or out-of-sync with the current
    # extras spec, NOT because they're equivalent in posture.
    if [ -f "uv.lock" ]; then
        log_info "Trying tier: hash-verified (uv.lock) ..."
        log_info "(this resolves + downloads the curated [all] set — first run on a"
        log_info " fresh venv can take 1-5 minutes; uv prints progress below)"
        # Stream uv's progress directly to the user instead of swallowing
        # it with `2>"$(mktemp)"`.  Two reasons:
        #   1. `--extra all --locked` against a fresh venv has to pull
        #      every transitive — silencing stderr makes the install
        #      look frozen for minutes on slow networks. Users see
        #      "Trying tier: hash-verified ..." and assume it's hung.
        #   2. The previous `2>"$(mktemp)"` substituted the path at
        #      command-build time but never saved it, so on failure the
        #      uv error message was unreachable — the user just got the
        #      generic "lockfile may be stale" warning.
        #
        # Critical flag choice: `--extra all`, NOT `--all-extras`.
        #   --all-extras = every [project.optional-dependencies] key.
        #                  This bypasses the curated `[all]` extra
        #                  entirely and pulls e.g. [matrix] (which
        #                  needs python-olm + make on Windows) and
        #                  [rl] (git+https deps that fail offline).
        #   --extra all  = install just the `[all]` extra's contents.
        #                  This respects the curation in pyproject.toml.
        # uv's own progress UI handles TTY detection and downgrades
        # gracefully when stdout/stderr aren't terminals.
        if UV_PROJECT_ENVIRONMENT="$INSTALL_DIR/venv" $UV_CMD sync --extra all --locked; then
            log_success "Main package installed (hash-verified via uv.lock)"
            if ! ensure_ddgs_for_web_search; then
                log_error "ddgs installation failed. web_search backend 'ddgs' will not work."
                exit 1
            fi
            log_success "All dependencies installed"
            return 0
        fi
        log_warn "uv.lock sync failed (see uv output above), falling back to PyPI resolve..."
    else
        log_info "uv.lock not found — falling back to PyPI resolve (no hash verification)"
    fi

    # Multi-tier fallback. The point of the tiers is that ONE compromised
    # PyPI package (a worm-poisoned release that gets quarantined, like
    # mistralai 2.4.6 in May 2026) shouldn't be able to silently demote a
    # fresh install all the way down to "core only" — the user should keep
    # everything else they signed up for.
    #
    # Tier 1: [all] — the curated extra in pyproject.toml.
    # Tier 2: [all] minus the currently-broken extras list (_BROKEN_EXTRAS).
    #         Edit _BROKEN_EXTRAS below when something on PyPI breaks; this
    #         lets users keep the rest of [all] when one transitive is
    #         unavailable. The list of [all]'s contents is parsed from
    #         pyproject.toml at runtime — there is NO hand-mirrored copy
    #         to drift out of sync. If you want to change what [all]
    #         contains, edit pyproject.toml only.
    # Tier 3: bare `.` — last-resort so at least the core CLI launches.
    #         Skipped tiers like "PyPI-only extras (no git deps)" used to
    #         exist to dodge [rl] / [matrix] git+sdist deps; those are no
    #         longer in [all] post-2026-05-12 lazy-install migration, so
    #         a separate PyPI-only tier had no remaining content.
    local _BROKEN_EXTRAS=()  # populate when an extra becomes unresolvable

    # Parse [project.optional-dependencies].all from pyproject.toml.
    # tomllib is stdlib on Python 3.11+ which uv's bootstrap guarantees.
    # Falls back to a hand list if parse fails — defensive only.
    local _ALL_EXTRAS_CSV
    _ALL_EXTRAS_CSV="$(
        "$PYTHON_PATH" - <<'PY' 2>/dev/null
import re, sys, tomllib
try:
    with open("pyproject.toml", "rb") as fh:
        data = tomllib.load(fh)
    specs = data["project"]["optional-dependencies"]["all"]
    extras = []
    for s in specs:
        m = re.search(r"hermes-agent\[([\w-]+)\]", s)
        if m:
            extras.append(m.group(1))
    print(",".join(extras))
except Exception as e:
    print("", file=sys.stderr)
    sys.exit(1)
PY
    )"
    if [ -z "$_ALL_EXTRAS_CSV" ]; then
        log_warn "Could not parse [all] from pyproject.toml; falling back to .[all] only."
        _ALL_EXTRAS_CSV=""
    fi

    # Build "[all] minus broken" spec by filtering the parsed list.
    local _SAFE_SPEC=".[all]"
    if [ -n "$_ALL_EXTRAS_CSV" ] && [ "${#_BROKEN_EXTRAS[@]}" -gt 0 ]; then
        local _SAFE_EXTRAS=()
        local _e _b _skip
        IFS=',' read -ra _ALL_EXTRAS_ARR <<< "$_ALL_EXTRAS_CSV"
        for _e in "${_ALL_EXTRAS_ARR[@]}"; do
            _skip=false
            for _b in "${_BROKEN_EXTRAS[@]}"; do
                if [ "$_e" = "$_b" ]; then _skip=true; break; fi
            done
            if [ "$_skip" = false ]; then _SAFE_EXTRAS+=("$_e"); fi
        done
        _SAFE_SPEC=".[$(IFS=,; echo "${_SAFE_EXTRAS[*]}")]"
    fi

    ALL_INSTALL_LOG=$(mktemp)
    local _installed=false
    local _tier_name=""

    install_tier() {
        local name="$1"; local spec="$2"
        log_info "Trying tier: $name ..."
        if $UV_CMD pip install -e "$spec" 2>"$ALL_INSTALL_LOG"; then
            log_success "Main package installed ($name)"
            _installed=true
            _tier_name="$name"
            return 0
        fi
        log_warn "Tier '$name' failed. Top of pip output:"
        head -5 "$ALL_INSTALL_LOG" | sed 's/^/    /' >&2
        return 1
    }

    install_tier "all" ".[all]" \
        || install_tier "all minus known-broken (${_BROKEN_EXTRAS[*]:-none})" "$_SAFE_SPEC" \
        || install_tier "core only (no extras)" "."

    rm -f "$ALL_INSTALL_LOG"

    if [ "$_installed" = false ]; then
        log_error "Package installation failed even with no extras."
        log_info "Check that build tools are installed: sudo apt install build-essential python3-dev"
        log_info "Then re-run: cd $INSTALL_DIR && uv pip install -e '.[all]'"
        exit 1
    fi

    if [ "$_tier_name" != "all (with RL/matrix extras)" ]; then
        log_warn "Note: installed via fallback tier ($_tier_name)."
        log_info "Some optional features may be missing. After resolving any"
        log_info "PyPI/network issue, re-run: $UV_CMD pip install -e '.[all]'"
    fi

    log_success "Main package installed"
    if ! ensure_ddgs_for_web_search; then
        log_error "ddgs installation failed. web_search backend 'ddgs' will not work."
        exit 1
    fi
    ensure_office_document_dependencies

    log_success "All dependencies installed"
}

ensure_ddgs_for_web_search() {
    local python_exe=""
    if [ "$USE_VENV" = true ] && [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        python_exe="$INSTALL_DIR/venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        python_exe="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        python_exe="$(command -v python)"
    fi

    if [ -z "$python_exe" ]; then
        log_warn "Skipping ddgs install check: no Python interpreter found"
        return 0
    fi

    if "$python_exe" -c "import ddgs" >/dev/null 2>&1; then
        return 0
    fi

    log_info "Installing ddgs for web_search backend..."
    if ! $UV_CMD pip install --python "$python_exe" "ddgs>=9,<10"; then
        return 1
    fi

    "$python_exe" -c "import ddgs" >/dev/null 2>&1
}

ensure_office_document_dependencies() {
    # Requested by desktop bootstrap users: make Word/PowerPoint helper skills
    # usable immediately in the installer-created virtualenv.
    if [ "$USE_VENV" != true ]; then
        return 0
    fi
    if [ "$DISTRO" = "termux" ]; then
        return 0
    fi

    local python_exe="$INSTALL_DIR/venv/bin/python"
    if [ ! -x "$python_exe" ]; then
        log_warn "Skipping office dependency check: $python_exe not found"
        return 0
    fi

    local missing=false
    local installer_cmd=""
    local import_name=""
    local spec=""
    local label=""

    if [ -n "${UV_CMD:-}" ]; then
        installer_cmd="$UV_CMD pip install --python \"$python_exe\""
    elif command -v uv >/dev/null 2>&1; then
        installer_cmd="uv pip install --python \"$python_exe\""
    else
        installer_cmd="\"$python_exe\" -m pip install"
    fi

    for entry in \
        "docx|python-docx>=1,<2|Word (.docx)" \
        "pptx|python-pptx>=1,<2|PowerPoint (.pptx)" \
        "markitdown|markitdown[pptx]>=0.1,<1|Office text extraction" \
        "PIL|Pillow>=10,<12|PowerPoint thumbnails" \
        "openpyxl|openpyxl>=3,<4|Excel workbook processing" \
        "pandas|pandas>=2,<3|Spreadsheet/tabular conversion" \
        "pypdf|pypdf>=5,<6|PDF merge/split/manipulation" \
        "fitz|pymupdf>=1.24,<2|PDF metadata/inspection" \
        "fpdf|fpdf2>=2.8,<3|Word/PDF text rendering fallback" \
        "weasyprint|weasyprint>=62,<63|HTML to PDF conversion" \
        "cairosvg|cairosvg>=2.7,<3|SVG conversion" \
        "pillow_heif|pillow-heif>=0.16,<1|HEIC image conversion"; do
        IFS='|' read -r import_name spec label <<< "$entry"
        if ! "$python_exe" -c "import ${import_name}" >/dev/null 2>&1; then
            missing=true
            log_info "Installing ${label} dependency: ${spec}"
            if ! eval "$installer_cmd \"$spec\""; then
                log_warn "Failed to install ${spec}. ${label} features may be unavailable until installed manually."
            fi
        fi
    done

    if [ "$missing" = true ]; then
        log_info "Office dependency check completed."
    fi
    return 0
}

backfill_skill_dependencies_on_repo_sync() {
    # Reinstall/repo-sync path: if a managed venv already exists, re-check
    # installer skill deps and backfill any missing packages without waiting
    # for a full dependency reinstall stage.
    if [ "$USE_VENV" != true ]; then
        return 0
    fi
    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        ensure_office_document_dependencies || true
    fi
}

setup_path() {
    log_info "Setting up hermes command..."

    if [ "$USE_VENV" = true ]; then
        HERMES_BIN="$INSTALL_DIR/venv/bin/hermes"
    else
        HERMES_BIN="$(which hermes 2>/dev/null || echo "")"
        if [ -z "$HERMES_BIN" ]; then
            log_warn "hermes not found on PATH after install"
            return 0
        fi
    fi

    # Verify the entry point script was actually generated
    if [ ! -x "$HERMES_BIN" ]; then
        log_warn "hermes entry point not found at $HERMES_BIN"
        log_info "This usually means the pip install didn't complete successfully."
        if [ "$DISTRO" = "termux" ]; then
            log_info "Try: cd $INSTALL_DIR && python -m pip install -e '.[termux-all]' -c constraints-termux.txt"
        else
            log_info "Try: cd $INSTALL_DIR && uv pip install -e '.[all]'"
        fi
        return 0
    fi

    local command_link_dir
    local command_link_display_dir
    command_link_dir="$(get_command_link_dir)"
    command_link_display_dir="$(get_command_link_display_dir)"

    # Create a user-facing shim for the hermes command.
    # We intentionally clear PYTHONPATH/PYTHONHOME here so inherited env vars
    # can't make this launcher import modules from another checkout.
    mkdir -p "$command_link_dir"
    # Older installs created this path as a symlink to $HERMES_BIN. Without
    # the rm, `cat >` follows the symlink and overwrites the venv pip entry
    # point with this shim — making `exec "$HERMES_BIN"` self-recurse. (#21454)
    rm -f "$command_link_dir/hermes"
    cat > "$command_link_dir/hermes" <<EOF
#!/usr/bin/env bash
unset PYTHONPATH
unset PYTHONHOME
exec "$HERMES_BIN" "\$@"
EOF
    chmod +x "$command_link_dir/hermes"
    log_success "Installed hermes launcher → $command_link_display_dir/hermes"

    if [ "$DISTRO" = "termux" ]; then
        export PATH="$command_link_dir:$PATH"
        log_info "$command_link_display_dir is the native Termux command path"
        log_success "hermes command ready"
        return 0
    fi

    # FHS layout: /usr/local/bin is normally on PATH for login shells (via
    # /etc/profile pathmunge), but on RHEL/CentOS/Rocky/Alma 8+ non-login
    # interactive root shells (su, sudo -s, tmux panes, some web terminals)
    # only source /etc/bashrc, which does NOT add /usr/local/bin — and
    # /root/.bash_profile doesn't either.  So verify with `command -v` and
    # fall back to writing a PATH guard into /root/.bashrc when needed.
    if [ "$ROOT_FHS_LAYOUT" = true ]; then
        export PATH="$command_link_dir:$PATH"
        # Probe a fresh non-login interactive bash the way the user will use it.
        # `bash -i -c` sources ~/.bashrc but NOT ~/.bash_profile or /etc/profile,
        # which is the exact scenario where RHEL root loses /usr/local/bin.
        if env -i HOME="$HOME" TERM="${TERM:-dumb}" bash -i -c 'command -v hermes' \
                >/dev/null 2>&1; then
            log_info "/usr/local/bin is already on PATH for all shells"
            log_success "hermes command ready"
            return 0
        fi

        log_info "hermes not on PATH in non-login shells (common on RHEL-family)"
        PATH_LINE='export PATH="/usr/local/bin:$PATH"'
        PATH_COMMENT='# Hermes Agent — ensure /usr/local/bin is on PATH (RHEL non-login shells)'
        for SHELL_CONFIG in "$HOME/.bashrc" "$HOME/.bash_profile"; do
            [ -f "$SHELL_CONFIG" ] || continue
            if ! grep -v '^[[:space:]]*#' "$SHELL_CONFIG" 2>/dev/null \
                    | grep -qE 'PATH=.*(/usr/local/bin|\$command_link_dir)'; then
                echo "" >> "$SHELL_CONFIG"
                echo "$PATH_COMMENT" >> "$SHELL_CONFIG"
                echo "$PATH_LINE" >> "$SHELL_CONFIG"
                log_success "Added /usr/local/bin to PATH in $SHELL_CONFIG"
            fi
        done
        log_success "hermes command ready"
        return 0
    fi

    # Check if ~/.local/bin is on PATH; if not, add it to shell config.
    # Detect the user's actual login shell (not the shell running this script,
    # which is always bash when piped from curl).
    if ! echo "$PATH" | tr ':' '\n' | grep -q "^$command_link_dir$"; then
        SHELL_CONFIGS=()
        IS_FISH=false
        LOGIN_SHELL="$(basename "${SHELL:-/bin/bash}")"
        case "$LOGIN_SHELL" in
            zsh)
                [ -f "$HOME/.zshrc" ] && SHELL_CONFIGS+=("$HOME/.zshrc")
                [ -f "$HOME/.zprofile" ] && SHELL_CONFIGS+=("$HOME/.zprofile")
                # If neither exists, create ~/.zshrc (common on fresh macOS installs)
                if [ ${#SHELL_CONFIGS[@]} -eq 0 ]; then
                    touch "$HOME/.zshrc"
                    SHELL_CONFIGS+=("$HOME/.zshrc")
                fi
                ;;
            bash)
                [ -f "$HOME/.bashrc" ] && SHELL_CONFIGS+=("$HOME/.bashrc")
                [ -f "$HOME/.bash_profile" ] && SHELL_CONFIGS+=("$HOME/.bash_profile")
                ;;
            fish)
                # fish uses ~/.config/fish/config.fish and fish_add_path — not export PATH=
                IS_FISH=true
                FISH_CONFIG="$HOME/.config/fish/config.fish"
                mkdir -p "$(dirname "$FISH_CONFIG")"
                touch "$FISH_CONFIG"
                ;;
            *)
                [ -f "$HOME/.bashrc" ] && SHELL_CONFIGS+=("$HOME/.bashrc")
                [ -f "$HOME/.zshrc" ] && SHELL_CONFIGS+=("$HOME/.zshrc")
                ;;
        esac
        # Also ensure ~/.profile has it (sourced by login shells on
        # Ubuntu/Debian/WSL even when ~/.bashrc is skipped)
        [ "$IS_FISH" = "false" ] && [ -f "$HOME/.profile" ] && SHELL_CONFIGS+=("$HOME/.profile")

        PATH_LINE='export PATH="$HOME/.local/bin:$PATH"'

        for SHELL_CONFIG in "${SHELL_CONFIGS[@]}"; do
            if ! grep -v '^[[:space:]]*#' "$SHELL_CONFIG" 2>/dev/null | grep -qE 'PATH=.*\.local/bin'; then
                echo "" >> "$SHELL_CONFIG"
                echo "# Hermes Agent — ensure ~/.local/bin is on PATH" >> "$SHELL_CONFIG"
                echo "$PATH_LINE" >> "$SHELL_CONFIG"
                log_success "Added ~/.local/bin to PATH in $SHELL_CONFIG"
            fi
        done

        # fish uses fish_add_path instead of export PATH=...
        if [ "$IS_FISH" = "true" ]; then
            if ! grep -q 'fish_add_path.*\.local/bin' "$FISH_CONFIG" 2>/dev/null; then
                echo "" >> "$FISH_CONFIG"
                echo "# Hermes Agent — ensure ~/.local/bin is on PATH" >> "$FISH_CONFIG"
                echo 'fish_add_path "$HOME/.local/bin"' >> "$FISH_CONFIG"
                log_success "Added ~/.local/bin to PATH in $FISH_CONFIG"
            fi
        fi

        if [ "$IS_FISH" = "false" ] && [ ${#SHELL_CONFIGS[@]} -eq 0 ]; then
            log_warn "Could not detect shell config file to add ~/.local/bin to PATH"
            log_info "Add manually: $PATH_LINE"
        fi
    else
        log_info "~/.local/bin already on PATH"
    fi

    # Export for current session so hermes works immediately
    export PATH="$command_link_dir:$PATH"

    log_success "hermes command ready"
}

# Apply credentials from bootstrap env vars (HERMES_BOOTSTRAP_API_KEY, etc.)
# Called during config setup when installer was launched with credentials.
apply_bootstrap_credentials() {
    # Always ensure bootstrap installs have web tooling defaults, even when
    # no API key was supplied.
    ensure_bootstrap_tool_config

    local selected_endpoint
    local selected_provider_slug
    local effective_api_key
    local effective_base_url
    local main_base_url
    local staging_base_url
    local dev_base_url
    local staging_api_key
    local dev_api_key

    selected_endpoint="$(printf '%s' "${HERMES_BOOTSTRAP_SELECTED_ENDPOINT:-main}" | tr '[:upper:]' '[:lower:]')"
    main_base_url="${HERMES_BOOTSTRAP_BASE_URL:-}"
    staging_base_url="${HERMES_BOOTSTRAP_STAGING_BASE_URL:-}"
    dev_base_url="${HERMES_BOOTSTRAP_DEV_BASE_URL:-}"
    staging_api_key="${HERMES_BOOTSTRAP_STAGING_API_KEY:-}"
    dev_api_key="${HERMES_BOOTSTRAP_DEV_API_KEY:-}"

    case "$selected_endpoint" in
        staging)
            selected_provider_slug="iamds-litellm-staging"
            effective_api_key="$staging_api_key"
            effective_base_url="$staging_base_url"
            ;;
        dev)
            selected_provider_slug="iamds-litellm-dev"
            effective_api_key="$dev_api_key"
            effective_base_url="$dev_base_url"
            ;;
        *)
            selected_endpoint="main"
            selected_provider_slug="iamds-litellm"
            effective_api_key="${HERMES_BOOTSTRAP_API_KEY:-}"
            effective_base_url="$main_base_url"
            ;;
    esac

    # Fallback to main endpoint if selected variant is incomplete.
    if [ -z "${effective_api_key:-}" ] || [ -z "${effective_base_url:-}" ]; then
        selected_endpoint="main"
        selected_provider_slug="iamds-litellm"
        effective_api_key="${HERMES_BOOTSTRAP_API_KEY:-}"
        effective_base_url="$main_base_url"
    fi

    # If no usable API key provided, skip — use interactive mode or defaults
    if [ -z "${effective_api_key:-}" ]; then
        return 0
    fi

    log_info "Configuring from bootstrap credentials (selected endpoint: ${selected_endpoint})..."

    # Update config.yaml with substituted values
    if [ -f "$HERMES_HOME/config.yaml" ]; then
        # Replace model.default
        if [ -n "${HERMES_BOOTSTRAP_MODEL:-}" ]; then
            sed -i.bak "s/^  default: .*/  default: ${HERMES_BOOTSTRAP_MODEL}/" "$HERMES_HOME/config.yaml" || true
        fi

        # Replace model.base_url from selected endpoint root
        if [ -n "${effective_base_url:-}" ]; then
            base_root="${effective_base_url%/}"
            case "$base_root" in
                */litellm/v1) base_root="${base_root%/litellm/v1}" ;;
                */litellm/mcp) base_root="${base_root%/litellm/mcp}" ;;
            esac
            llm_gateway_url="${base_root}/litellm/v1"
            mcp_server_url="${base_root}/litellm/mcp"
            if [ -n "${HERMES_BOOTSTRAP_MEMORY_API_URL:-}" ]; then
                mcp_server_url="${HERMES_BOOTSTRAP_MEMORY_API_URL%/}"
            fi

            sed -i.bak "s/^  provider: .*/  provider: ${selected_provider_slug}/" "$HERMES_HOME/config.yaml" || true
            # Escape URL for sed (forward slashes need escaping)
            escaped_url=$(printf '%s\n' "${llm_gateway_url}" | sed 's/[\/&]/\\&/g')
            sed -i.bak "s|  base_url: .*|  base_url: ${escaped_url}|" "$HERMES_HOME/config.yaml" || true

            # Upsert mcp_servers.memory with Bearer auth using API key.
            # Keep other existing MCP servers untouched.
            local cfg_python
            cfg_python="python3"
            if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
                cfg_python="$INSTALL_DIR/venv/bin/python"
            fi
            local mcp_server_name
            mcp_server_name="$("$cfg_python" - <<'PYEOF'
try:
    from hermes_cli.config import get_primary_mcp_server_name
    print(get_primary_mcp_server_name())
except Exception:
    print("memory")
PYEOF
)"
            mcp_server_name="${mcp_server_name:-memory}"
            "$cfg_python" - "$HERMES_HOME/config.yaml" "${mcp_server_url}" "${effective_api_key}" "${mcp_server_name}" <<'PYEOF'
import re
import sys

path, mcp_url, api_key, mcp_name = sys.argv[1], sys.argv[2], sys.argv[3], (sys.argv[4] or "memory")
text = open(path, encoding="utf-8").read()
memory_block = (
    f"  {mcp_name}:\n"
    f"    url: {mcp_url}\n"
    "    headers:\n"
    f"      Authorization: \"Bearer {api_key}\"\n"
    "    timeout: 180\n"
    "    connect_timeout: 60\n"
    "    trusted: true\n"
)

root = re.search(r"(?ms)^mcp_servers:\n(.*?)(?=^\S|\Z)", text)
if root:
    body = root.group(1)
    try:
        from hermes_cli.config import SINGLE_MCP_SERVER_NAME
    except Exception:
        SINGLE_MCP_SERVER_NAME = ""
    names = sorted(
        {
            n
            for n in ("memory", mcp_name, SINGLE_MCP_SERVER_NAME)
            if isinstance(n, str) and n.strip()
        }
    )
    pattern = r"(?ms)^  (?:" + "|".join(re.escape(n) for n in names) + r"):\n(?:    .*\n)*"
    body = re.sub(pattern, "", body)
    new_root = "mcp_servers:\n" + memory_block + body
    text = text[:root.start()] + new_root + text[root.end():]
else:
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\nmcp_servers:\n" + memory_block

open(path, "w", encoding="utf-8").write(text)
PYEOF

            # Upsert staging/dev named providers only when their keys are set.
            # This keeps model provider dropdown free of unconfigured variants.
            "$cfg_python" - "$HERMES_HOME/config.yaml" "${staging_base_url:-}" "${dev_base_url:-}" "${staging_api_key:-}" "${dev_api_key:-}" <<'PYEOF'
import sys

config_path, staging_base, dev_base, staging_key, dev_key = sys.argv[1:6]

try:
    from hermes_cli.config import load_config, save_config
except Exception:
    raise SystemExit(0)

cfg = load_config() or {}
providers = cfg.get("providers")
if not isinstance(providers, dict):
    providers = {}

def _normalize_base(raw: str) -> str:
    base = (raw or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/litellm/v1"):
        return base
    if base.endswith("/litellm/mcp"):
        return base[: -len("/litellm/mcp")] + "/litellm/v1"
    return base + "/litellm/v1"

def _set_provider(slug: str, label: str, base: str, key_env: str, key_value: str) -> None:
    normalized = _normalize_base(base)
    if not normalized or not (key_value or "").strip():
        providers.pop(slug, None)
        return
    existing = providers.get(slug) if isinstance(providers.get(slug), dict) else {}
    existing.update({
        "name": label,
        "base_url": normalized,
        "key_env": key_env,
        "transport": "codex_responses",
    })
    providers[slug] = existing

_set_provider(
    "iamds-litellm-staging",
    "IAMDS LiteLLM (Staging)",
    staging_base,
    "IAMDS_LITELLM_STAGING_API_KEY",
    staging_key,
)
_set_provider(
    "iamds-litellm-dev",
    "IAMDS LiteLLM (Dev)",
    dev_base,
    "IAMDS_LITELLM_DEV_API_KEY",
    dev_key,
)

cfg["providers"] = providers
save_config(cfg)
PYEOF
        fi

        # Clean up backup file
        rm -f "$HERMES_HOME/config.yaml.bak"
        log_success "Updated config.yaml with bootstrap credentials"
    fi

    # Never patch core Python sources from installer credentials anymore.
    # Previous bootstrap revisions injected custom blocks into models.py /
    # model_switch.py which pinned stale model lists and broke live LiteLLM
    # discovery on reinstalls. If we detect those legacy injected markers,
    # restore the canonical files from git.
    if [ -d "$INSTALL_DIR/.git" ] && command -v git >/dev/null 2>&1; then
        local _legacy_patch_detected=0
        if grep -q 'Custom: pin openai-api to fetched models' "$INSTALL_DIR/hermes_cli/models.py" 2>/dev/null; then
            _legacy_patch_detected=1
        fi
        if grep -q 'Custom: copilot suppressed' "$INSTALL_DIR/hermes_cli/model_switch.py" 2>/dev/null; then
            _legacy_patch_detected=1
        fi
        if [ "$_legacy_patch_detected" -eq 1 ]; then
            if git -C "$INSTALL_DIR" checkout -- hermes_cli/models.py hermes_cli/model_switch.py 2>/dev/null; then
                log_success "Restored canonical model discovery files (removed legacy bootstrap patches)"
            else
                log_warn "Detected legacy bootstrap model patches but could not restore canonical files from git"
            fi
        fi
    fi

    # Update .env with bootstrap credentials/runtime endpoints.
    # On update/reinstall flows the effective API key may be empty; appending
    # OPENAI_API_KEY= would shadow the user's existing key with a blank value.
    if [ -n "${effective_api_key:-}" ] && [ -f "$HERMES_HOME/.env" ]; then
        # Write API key
        {
            echo "# Added by bootstrap installer"
            echo "IAMDS_LITELLM_API_KEY=${effective_api_key}"
            # IAMDS_LITELLM_BASE_URL is the canonical iamds-litellm endpoint var.
            # Keep OPENAI_BASE_URL in sync for backward compatibility.
            if [ -n "${llm_gateway_url:-}" ]; then
                echo "IAMDS_LITELLM_BASE_URL=${llm_gateway_url}"
                echo "OPENAI_BASE_URL=${llm_gateway_url}"
            fi
            if [ -n "${staging_api_key:-}" ]; then
                echo "IAMDS_LITELLM_STAGING_API_KEY=${staging_api_key}"
            fi
            if [ -n "${dev_api_key:-}" ]; then
                echo "IAMDS_LITELLM_DEV_API_KEY=${dev_api_key}"
            fi
        } >> "$HERMES_HOME/.env"

        log_success "Configured .env with bootstrap API key/base URL"
    elif [ -z "${effective_api_key:-}" ]; then
        log_info "Bootstrap API key not provided; keeping existing IAMDS_LITELLM_API_KEY in ~/.hermes/.env"
    fi
}

ensure_bootstrap_tool_config() {
    local config_path cfg_python
    config_path="$HERMES_HOME/config.yaml"
    [ -f "$config_path" ] || return 0

    cfg_python="python3"
    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        cfg_python="$INSTALL_DIR/venv/bin/python"
    fi

    "$cfg_python" - "$config_path" <<'PYEOF' || return 0
import sys
import yaml

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = yaml.safe_load(fh) or {}

web = data.setdefault("web", {})
if not web.get("backend"):
    web["backend"] = "ddgs"
web.setdefault("search_backend", "")
web.setdefault("extract_backend", "")
web.setdefault("use_gateway", False)

platform_toolsets = data.setdefault("platform_toolsets", {})
cli_toolsets = platform_toolsets.get("cli")
if isinstance(cli_toolsets, str):
    cli_toolsets = [cli_toolsets]
elif not isinstance(cli_toolsets, list):
    cli_toolsets = []
if "web" not in cli_toolsets:
    cli_toolsets.append("web")
platform_toolsets["cli"] = cli_toolsets

skills = data.setdefault("skills", {})
litellm_hub = skills.setdefault("litellm_hub", {})
if not litellm_hub.get("base_url"):
    litellm_hub["base_url"] = "http://localhost:4000"
litellm_hub.setdefault("api_key", "")
litellm_hub.setdefault("timeout", 20)

with open(path, "w", encoding="utf-8") as fh:
    yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=False)
PYEOF

    log_success "Ensured bootstrap tool config (web backend, platform_toolsets.cli, litellm_hub)"
}

resolve_aimds_installer_dir() {
    if [ -n "${HERMES_BOOTSTRAP_AIMDS_SETUP_DIR:-}" ] && [ -d "${HERMES_BOOTSTRAP_AIMDS_SETUP_DIR}/installer" ]; then
        printf '%s\n' "${HERMES_BOOTSTRAP_AIMDS_SETUP_DIR}/installer"
        return 0
    fi

    if [ -d "$INSTALL_DIR/installer" ]; then
        printf '%s\n' "$INSTALL_DIR/installer"
        return 0
    fi

    if [ -d "$INSTALL_DIR/../aimds-setup/installer" ]; then
        printf '%s\n' "$INSTALL_DIR/../aimds-setup/installer"
        return 0
    fi

    # Bundled bootstrap scripts are materialized into ~/.hermes/bootstrap-cache.
    # Mirror AIMDS assets there and resolve relative to this script path.
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"
    if [ -d "$script_dir/installer" ]; then
        printf '%s\n' "$script_dir/installer"
        return 0
    fi

    return 1
}

sync_aimds_custom_assets() {
    local installer_dir skills_src hidden_skills_src scripts_src
    installer_dir="$(resolve_aimds_installer_dir 2>/dev/null || true)"
    [ -n "$installer_dir" ] || return 0

    skills_src="$installer_dir/skills"
    hidden_skills_src="$installer_dir/skills-hidden"
    scripts_src="$installer_dir/scripts"

    if [ -d "$skills_src" ] && [ -n "$(find "$skills_src" -mindepth 1 -print -quit 2>/dev/null)" ]; then
        log_info "Syncing AIMDS custom skills from $skills_src ..."
        mkdir -p "$HERMES_HOME/skills"
        if command -v rsync >/dev/null 2>&1; then
            rsync -a "$skills_src/" "$HERMES_HOME/skills/"
        else
            cp -R "$skills_src/"* "$HERMES_HOME/skills/" 2>/dev/null || true
        fi
        log_success "AIMDS custom skills synced"
    fi

    # Copy staged-but-inactive AIMDS skills/docs into .archive so they ship with
    # the installer but do not register as active skills yet.
    if [ -d "$hidden_skills_src" ] && [ -n "$(find "$hidden_skills_src" -mindepth 1 -print -quit 2>/dev/null)" ]; then
        log_info "Syncing AIMDS hidden skill pack from $hidden_skills_src ..."
        mkdir -p "$HERMES_HOME/skills/.archive/aimds-loadout"
        if command -v rsync >/dev/null 2>&1; then
            rsync -a "$hidden_skills_src/" "$HERMES_HOME/skills/.archive/aimds-loadout/"
        else
            cp -R "$hidden_skills_src/"* "$HERMES_HOME/skills/.archive/aimds-loadout/" 2>/dev/null || true
        fi
        log_success "AIMDS hidden skill pack synced to ~/.hermes/skills/.archive/aimds-loadout"
    fi

    if [ -d "$scripts_src" ] && [ -n "$(find "$scripts_src" -mindepth 1 -print -quit 2>/dev/null)" ]; then
        log_info "Syncing AIMDS installer helper tools from $scripts_src ..."
        mkdir -p "$HERMES_HOME/tools/aimds-installer"
        if command -v rsync >/dev/null 2>&1; then
            rsync -a "$scripts_src/" "$HERMES_HOME/tools/aimds-installer/"
        else
            cp -R "$scripts_src/"* "$HERMES_HOME/tools/aimds-installer/" 2>/dev/null || true
        fi
        log_success "AIMDS installer helper tools synced"
    fi
}

copy_config_templates() {
    log_info "Setting up configuration files..."

    local hermes_work_dir memory_fs_dir leveldb_dir seed_script
    hermes_work_dir="$HOME/Documents/HermesWorkingDirectory"
    memory_fs_dir="$HOME/Documents/HermesMemory"

    # Create ~/.hermes directory structure (config at top level, code in subdir)
    mkdir -p "$HERMES_HOME"/{cron,sessions,logs,pairing,hooks,image_cache,audio_cache,memories,skills}
    mkdir -p "$hermes_work_dir"
    mkdir -p "$HOME/Documents"

    # Expose local memory files in Documents/HermesMemory for user visibility.
    # We use a symlink so filesystem memory and ~/.hermes/memories stay in sync.
    if [ ! -e "$memory_fs_dir" ]; then
        ln -s "$HERMES_HOME/memories" "$memory_fs_dir" 2>/dev/null || true
    fi

    # Create .env at ~/.hermes/.env (top level, easy to find)
    if [ ! -f "$HERMES_HOME/.env" ]; then
        if [ -f "$INSTALL_DIR/.env.example" ]; then
            cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env"
            log_success "Created ~/.hermes/.env from template"
        else
            touch "$HERMES_HOME/.env"
            log_success "Created ~/.hermes/.env"
        fi
    else
        log_info "~/.hermes/.env already exists, keeping it"
    fi
    # Restrict .env permissions — this file holds API keys and tokens.
    # 0600 ensures only the file owner can read/write, matching standard
    # practice for credential files (.netrc, .aws/credentials, .ssh/config).
    chmod 600 "$HERMES_HOME/.env"
    configure_browser_env_from_system_browser

    # Create config.yaml at ~/.hermes/config.yaml (top level, easy to find)
    if [ ! -f "$HERMES_HOME/config.yaml" ]; then
        if [ -f "$INSTALL_DIR/cli-config.yaml.example" ]; then
            cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
            log_success "Created ~/.hermes/config.yaml from template"
        fi
    else
        log_info "~/.hermes/config.yaml already exists, keeping it"
    fi

    # Optionally merge AIMDS custom skills/tools when AIMDS installer assets
    # are available alongside this checkout.
    sync_aimds_custom_assets

    # Keep parity with AIMDS installer defaults: use a stable Documents working
    # directory rather than repo-relative ".".
    if [ -f "$HERMES_HOME/config.yaml" ]; then
        escaped_cwd=$(printf '%s\n' "$hermes_work_dir" | sed 's/[\/&]/\\&/g')
        if grep -qE '^[[:space:]]+cwd:' "$HERMES_HOME/config.yaml"; then
            sed -i.bak "s|^  cwd: .*|  cwd: $escaped_cwd|" "$HERMES_HOME/config.yaml" || true
            rm -f "$HERMES_HOME/config.yaml.bak"
        elif grep -qE '^terminal:[[:space:]]*$' "$HERMES_HOME/config.yaml"; then
            awk -v cwd="$hermes_work_dir" '
                BEGIN { inserted=0 }
                /^terminal:[[:space:]]*$/ {
                    print
                    if (!inserted) {
                        print "  cwd: " cwd
                        inserted=1
                    }
                    next
                }
                { print }
            ' "$HERMES_HOME/config.yaml" > "$HERMES_HOME/config.yaml.tmp" && mv "$HERMES_HOME/config.yaml.tmp" "$HERMES_HOME/config.yaml"
        else
            {
                printf '\nterminal:\n'
                printf '  cwd: %s\n' "$hermes_work_dir"
            } >> "$HERMES_HOME/config.yaml"
        fi
    fi

    # Seed Electron localStorage so Hermes Desktop picks up the working
    # directory on first launch.
    leveldb_dir="$HOME/Library/Application Support/Hermes/Local Storage/leveldb"
    seed_script="$INSTALL_DIR/installer/scripts/seed-workspace-cwd.py"
    if [ -f "$seed_script" ] && command -v python3 >/dev/null 2>&1; then
        rm -rf "$leveldb_dir"
        python3 "$seed_script" "$leveldb_dir" "$hermes_work_dir" >/dev/null 2>&1 || true
    fi

    # Keep parity with AIMDS installer mechanics: always overwrite SOUL.md
    # so reruns keep persona defaults in sync.
    cat > "$HERMES_HOME/SOUL.md" << 'SOUL_EOF'
# Hermes Agent Persona

You are Hermes, an AI agent originally developed by Nous Research and extended and deployed by IAMDS GmbH for internal use.

## Context

- You are running inside the IAMDS internal tooling stack.
- The underlying model infrastructure is managed by IAMDS GmbH via a LiteLLM proxy.
- When users ask about Hermes, clarify that this is the IAMDS-customized version — not the vanilla Nous Research release.
- IAMDS GmbH is a German company. Users may speak to you in German, English, or Spanish. Always respond in the language the user is writing in.

## Behavior

- Be concise, direct, and professional.
- Avoid unnecessary filler phrases.
- When speaking (voice mode), use short natural sentences — avoid bullet points, markdown, or lists in spoken responses.
- You are aware of the IAMDS product suite, which includes tools built on top of Azure, OpenAI, and Anthropic APIs.
SOUL_EOF
    log_success "Wrote ~/.hermes/SOUL.md (edit to customize personality)"

    log_success "Configuration directory ready: ~/.hermes/"

    # Seed bundled skills into ~/.hermes/skills/ (manifest-based, one-time per skill)
    if [ "$NO_SKILLS" = true ]; then
        # Blank-slate install: write the opt-out marker and skip seeding.
        # skills_sync.py and `hermes update` both honor this marker, so the
        # default profile stays empty across future updates too.
        printf '%s\n' \
            "This profile opted out of bundled-skill seeding (installed with --no-skills)." \
            "Delete this file to re-enable sync on the next 'hermes update'." \
            > "$HERMES_HOME/.no-bundled-skills" 2>/dev/null || true
        log_info "Skipping bundled skills (--no-skills). Wrote $HERMES_HOME/.no-bundled-skills"
        log_info "  Future 'hermes update' runs will not inject bundled skills. Delete the marker to opt back in."
    else
        log_info "Syncing bundled skills to ~/.hermes/skills/ ..."
        if "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/tools/skills_sync.py" 2>/dev/null; then
            log_success "Skills synced to ~/.hermes/skills/"
        else
            # Fallback: simple directory copy if Python sync fails
            if [ -d "$INSTALL_DIR/skills" ] && [ ! "$(ls -A "$HERMES_HOME/skills/" 2>/dev/null | grep -v '.bundled_manifest')" ]; then
                cp -r "$INSTALL_DIR/skills/"* "$HERMES_HOME/skills/" 2>/dev/null || true
                log_success "Skills copied to ~/.hermes/skills/"
            fi
        fi

        # Keep AIMDS runtime skill repository focused: remove blocked default
        # bundled categories from the active skills tree.
        for blocked_cat in autonomous-ai-agents data-science email general mlops smart-home social-media software-development; do
            rm -rf "$HERMES_HOME/skills/$blocked_cat"
        done

        # Remove explicitly deactivated skills so they are not surfaced in
        # desktop skill lists and cannot be loaded by the runtime.
        for blocked_skill_dir in \
            "creative/architecture-diagram" \
            "creative/ascii-video" \
            "creative/baoyu-infographic" \
            "creative/manim-video" \
            "creative/popular-web-designs" \
            "creative/pretext" \
            "creative/songwriting-and-ai-music" \
            "creative/touchdesigner-mcp" \
            "media/songsee" \
            "dogfood" \
            "yuanbao" \
            "github"; do
            rm -rf "$HERMES_HOME/skills/$blocked_skill_dir"
        done

    fi

    # Apply bootstrap credentials if provided by the installer UI
    apply_bootstrap_credentials
}

check_configured_mcp_servers() {
    # Best-effort smoke check for configured MCP servers. Warns about
    # unreachable/misconfigured entries without blocking install completion.
    local config_path="$HERMES_HOME/config.yaml"
    if [ ! -f "$config_path" ]; then
        log_info "Skipping MCP server check: $config_path not found"
        return 0
    fi

    local py=""
    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        py="$INSTALL_DIR/venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        py="$(command -v python)"
    fi
    if [ -z "$py" ]; then
        log_warn "Skipping MCP server check: no Python interpreter found"
        return 0
    fi

    log_info "Checking configured MCP servers..."
    local report rc
    report="$("$py" - "$config_path" <<'PYEOF'
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

config_path = Path(sys.argv[1])
if not config_path.exists():
    print(json.dumps({"ok": True, "checked": 0, "failed": 0, "results": []}))
    raise SystemExit(0)

try:
    import yaml
except Exception as exc:
    print(json.dumps({
        "ok": True,
        "checked": 0,
        "failed": 0,
        "results": [],
        "note": "Skipping MCP check: PyYAML unavailable ({})".format(exc),
    }))
    raise SystemExit(0)

try:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
except Exception as exc:
    print(json.dumps({
        "ok": True,
        "checked": 0,
        "failed": 0,
        "results": [],
        "note": "Skipping MCP check: cannot parse config.yaml ({})".format(exc),
    }))
    raise SystemExit(0)

servers = cfg.get("mcp_servers")
if not isinstance(servers, dict) or not servers:
    print(json.dumps({"ok": True, "checked": 0, "failed": 0, "results": []}))
    raise SystemExit(0)

def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in {'"', "'"}:
            val = val[1:-1]
        os.environ.setdefault(key, val)

_load_dotenv(config_path.with_name(".env"))
_env_var_pattern = re.compile(r"\$\{([^}]+)\}")

def _expand_env(value):
    if isinstance(value, str):
        return _env_var_pattern.sub(lambda m: os.environ.get(m.group(1), m.group(0)), value)
    return value

results = []
failed = 0
for name, spec in servers.items():
    if not isinstance(spec, dict):
        results.append({"name": str(name), "ok": False, "reason": "invalid server config (not a mapping)"})
        failed += 1
        continue
    cmd = str(spec.get("command") or "").strip()
    url = str(spec.get("url") or "").strip()
    connect_timeout = float(spec.get("connect_timeout") or 5)
    connect_timeout = max(1.0, min(connect_timeout, 15.0))
    checks = []
    ok = True

    if cmd:
        resolved = shutil.which(cmd) if ("/" not in cmd and "\\" not in cmd) else (cmd if Path(cmd).exists() else None)
        if resolved:
            checks.append("command ok ({})".format(resolved))
        else:
            checks.append("command missing ({})".format(cmd))
            ok = False

    if url:
        headers = {}
        raw_headers = spec.get("headers")
        if isinstance(raw_headers, dict):
            for k, v in raw_headers.items():
                if k is None or v is None:
                    continue
                headers[str(k)] = str(_expand_env(v))
        if not any(str(k).lower() == "accept" for k in headers):
            headers["Accept"] = "text/event-stream"
        req = urllib.request.Request(str(_expand_env(url)), headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=connect_timeout) as resp:
                checks.append("url ok (HTTP {})".format(getattr(resp, 'status', 200)))
        except urllib.error.HTTPError as exc:
            # Any HTTP response proves network/path reachability for smoke-check
            # purposes, even if the endpoint rejects this probe method.
            checks.append("url reachable (HTTP {})".format(exc.code))
        except Exception as exc:
            checks.append("url unreachable ({})".format(exc))
            ok = False

    if not cmd and not url:
        checks.append("no command/url defined")
        ok = False

    if not ok:
        failed += 1
    results.append({"name": str(name), "ok": ok, "reason": "; ".join(checks)})

print(json.dumps({"ok": failed == 0, "checked": len(results), "failed": failed, "results": results}))
PYEOF
)"
    rc=$?
    if [ "$rc" -ne 0 ] || [ -z "$report" ]; then
        log_warn "MCP server check failed to run; skipping"
        return 0
    fi

    local checked failed
    checked="$("$py" - "$report" <<'PYEOF'
import json, sys
data = json.loads(sys.argv[1])
print(data.get("checked", 0))
PYEOF
)"
    failed="$("$py" - "$report" <<'PYEOF'
import json, sys
data = json.loads(sys.argv[1])
print(data.get("failed", 0))
PYEOF
)"
    local note
    note="$("$py" - "$report" <<'PYEOF'
import json, sys
data = json.loads(sys.argv[1])
print(data.get("note", ""))
PYEOF
)"
    if [ -n "$note" ]; then
        log_info "$note"
    fi

    if [ "$checked" = "0" ]; then
        log_info "No MCP servers configured"
        return 0
    fi

    "$py" - "$report" <<'PYEOF' | while IFS=':' read -r level name reason; do
import json, sys
data = json.loads(sys.argv[1])
for row in data.get("results", []):
    prefix = "ok" if row.get("ok") else "warn"
    print(f"{prefix}:{row.get('name','?')}:{row.get('reason','')}")
PYEOF
        if [ "$level" = "ok" ]; then
            log_success "  MCP '$name': $reason"
        else
            log_warn "  MCP '$name': $reason"
        fi
    done

    if [ "$failed" != "0" ]; then
        log_warn "MCP server check: $failed/$checked failed"
    else
        log_success "MCP server check passed ($checked configured)"
    fi
    return 0
}

reload_configured_mcp_servers() {
    # Best-effort MCP reload at install finalization. Mirrors runtime reload.mcp
    # server actions (shutdown + discover) but has no active session snapshot to
    # refresh during install.
    local config_path="$HERMES_HOME/config.yaml"
    if [ ! -f "$config_path" ]; then
        log_info "Skipping MCP reload: $config_path not found"
        return 0
    fi

    local py=""
    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        py="$INSTALL_DIR/venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        py="$(command -v python)"
    fi
    if [ -z "$py" ]; then
        log_warn "Skipping MCP reload: no Python interpreter found"
        return 0
    fi

    log_info "Reloading MCP servers..."
    local report rc
    report="$(
        cd "$INSTALL_DIR" && "$py" - "$config_path" <<'PYEOF'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
if not config_path.exists():
    print(json.dumps({"ok": True, "reloaded": False, "reason": "config missing"}))
    raise SystemExit(0)

try:
    import yaml
except Exception as exc:
    print(json.dumps({"ok": True, "reloaded": False, "reason": "PyYAML unavailable ({})".format(exc)}))
    raise SystemExit(0)

try:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
except Exception as exc:
    print(json.dumps({"ok": True, "reloaded": False, "reason": "cannot parse config.yaml ({})".format(exc)}))
    raise SystemExit(0)

servers = cfg.get("mcp_servers")
if not isinstance(servers, dict) or not servers:
    print(json.dumps({"ok": True, "reloaded": False, "reason": "no configured servers"}))
    raise SystemExit(0)

from tools.mcp_tool import discover_mcp_tools, shutdown_mcp_servers
shutdown_mcp_servers()
discover_mcp_tools()
print(json.dumps({"ok": True, "reloaded": True, "count": len(servers)}))
PYEOF
    )"
    rc=$?

    if [ "$rc" -ne 0 ]; then
        log_warn "MCP reload failed (exit $rc)"
        return 0
    fi

    if [ -z "$report" ]; then
        log_warn "MCP reload produced no output"
        return 0
    fi

    local _summary
    _summary="$(
        printf '%s' "$report" | "$py" - <<'PYEOF' 2>/dev/null || true
import json
import sys
txt = sys.stdin.read()
try:
    data = json.loads(txt)
except Exception:
    print("")
    raise SystemExit(0)
if data.get("reloaded"):
    print("reloaded {}".format(data.get("count", 0)))
elif data.get("reason"):
    print("skipped: {}".format(data.get("reason")))
else:
    print("skipped")
PYEOF
    )"

    if [ -n "$_summary" ]; then
        if [ "${_summary#reloaded }" != "$_summary" ]; then
            log_ok "MCP reload complete (${_summary#reloaded } configured)"
        else
            log_info "MCP reload $_summary"
        fi
    else
        log_info "MCP reload completed"
    fi
    return 0
}

find_system_browser() {
    # Prefer a user-specified browser path, then common Linux/macOS Chrome and
    # Chromium command names.  Arch-family distributions commonly ship plain
    # `chromium`, while Debian-family systems often use `chromium-browser`.
    if [ -n "${AGENT_BROWSER_EXECUTABLE_PATH:-}" ]; then
        if [ -x "$AGENT_BROWSER_EXECUTABLE_PATH" ]; then
            echo "$AGENT_BROWSER_EXECUTABLE_PATH"
            return 0
        fi
        if command -v "$AGENT_BROWSER_EXECUTABLE_PATH" >/dev/null 2>&1; then
            command -v "$AGENT_BROWSER_EXECUTABLE_PATH"
            return 0
        fi
    fi

    local candidate
    for candidate in google-chrome google-chrome-stable chromium chromium-browser chrome; do
        if command -v "$candidate" >/dev/null 2>&1; then
            command -v "$candidate"
            return 0
        fi
    done

    if [ "$(uname)" = "Darwin" ]; then
        for app in \
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
            "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
            if [ -x "$app" ]; then
                echo "$app"
                return 0
            fi
        done
    fi

    return 1
}

run_browser_install_with_timeout() {
    local timeout_seconds="$1"
    shift

    if command -v timeout >/dev/null 2>&1; then
        timeout "$timeout_seconds" "$@"
    else
        "$@"
    fi
}

configure_browser_env_from_system_browser() {
    local env_file="$HERMES_HOME/.env"
    local browser_path="${DETECTED_BROWSER_EXECUTABLE:-}"

    if [ -z "$browser_path" ]; then
        browser_path="$(find_system_browser 2>/dev/null || true)"
    fi

    if [ -z "$browser_path" ]; then
        return 0
    fi

    mkdir -p "$HERMES_HOME"
    if [ ! -f "$env_file" ]; then
        touch "$env_file"
    fi

    if grep -q '^AGENT_BROWSER_EXECUTABLE_PATH=' "$env_file" 2>/dev/null; then
        log_info "AGENT_BROWSER_EXECUTABLE_PATH already configured"
        return 0
    fi

    {
        echo ""
        echo "# Hermes Agent browser tools — use the system Chrome/Chromium binary."
        echo "AGENT_BROWSER_EXECUTABLE_PATH=$browser_path"
    } >> "$env_file"
    log_success "Configured browser tools to use $browser_path"
}

install_node_deps() {
    if [ "$HAS_NODE" = false ]; then
        log_info "Skipping Node.js dependencies (Node not installed)"
        return 0
    fi

    if [ "$DISTRO" = "termux" ]; then
        log_info "Skipping automatic Node/browser dependency setup on Termux"
        log_info "Browser automation is not part of the tested Termux install path yet."
        log_info "If you want to experiment manually later, run: cd $INSTALL_DIR && npm install"
        return 0
    fi

    if [ -f "$INSTALL_DIR/package.json" ]; then
        log_info "Installing Node.js dependencies (browser tools)..."
        cd "$INSTALL_DIR"
        npm install --silent 2>/dev/null || {
            log_warn "npm install failed (browser tools may not work)"
        }
        log_success "Node.js dependencies installed"

        # Install Playwright browser + system dependencies.
        # Playwright's --with-deps only supports apt-based systems natively.
        # For Arch/Manjaro we install the system libs via pacman first.
        # Other systems must install Chromium dependencies manually.
        if [ "$SKIP_BROWSER" = true ]; then
            log_info "Skipping Playwright/Chromium install (--skip-browser)"
            log_info "Browser tools will be unavailable until you run manually:"
            log_info "  cd $INSTALL_DIR && npx playwright install chromium"
            log_info "On apt-based systems, an admin also needs to run:"
            log_info "  sudo npx playwright install-deps chromium"
        else
        log_info "Installing browser engine (Playwright Chromium)..."
        DETECTED_BROWSER_EXECUTABLE="$(find_system_browser 2>/dev/null || true)"
        if [ -n "$DETECTED_BROWSER_EXECUTABLE" ]; then
            log_success "Found system Chrome/Chromium at $DETECTED_BROWSER_EXECUTABLE"
            log_info "Skipping Playwright browser download; Hermes will use the system browser."
        else
            case "$DISTRO" in
                ubuntu|debian|raspbian|pop|linuxmint|elementary|zorin|kali|parrot)
                    # Use --with-deps only when sudo is available non-interactively
                    # (root, or a user with passwordless sudo). Non-sudo users
                    # — typical for systemd service accounts and unprivileged
                    # operator users — would otherwise get blocked on an
                    # interactive sudo prompt that they can't satisfy. Fall back
                    # to the browser-only install in that case, and print the
                    # exact command the admin needs to run separately.
                    if [ "$(id -u)" -eq 0 ] || (command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null); then
                        log_info "Installing Playwright Chromium with system dependencies..."
                        cd "$INSTALL_DIR" && run_browser_install_with_timeout 600 npx playwright install --with-deps chromium 2>/dev/null || {
                            log_warn "Playwright browser installation failed — browser tools will not work."
                            log_warn "Try running manually: cd $INSTALL_DIR && npx playwright install --with-deps chromium"
                        }
                    else
                        log_warn "No sudo available — skipping system-library install (--with-deps)."
                        log_info "Ask an administrator to run, one time, as root:"
                        log_info "  sudo npx playwright install-deps chromium"
                        log_info "  (from $INSTALL_DIR, after Node.js deps are installed)"
                        log_info "Installing Chromium binary into this user's Playwright cache..."
                        cd "$INSTALL_DIR" && run_browser_install_with_timeout 600 npx playwright install chromium 2>/dev/null || {
                            log_warn "Playwright browser installation failed — browser tools will not work."
                            log_warn "Try running manually: cd $INSTALL_DIR && npx playwright install chromium"
                        }
                    fi
                    ;;
                arch|manjaro|cachyos|endeavouros|garuda)
                    if command -v pacman &> /dev/null; then
                        log_info "Arch-family distro detected — installing Chromium system dependencies via pacman..."
                        if command -v sudo &> /dev/null && sudo -n true 2>/dev/null; then
                            sudo NEEDRESTART_MODE=a pacman -S --noconfirm --needed \
                                nss atk at-spi2-core cups libdrm libxkbcommon mesa pango cairo alsa-lib >/dev/null 2>&1 || true
                        elif [ "$(id -u)" -eq 0 ]; then
                            pacman -S --noconfirm --needed \
                                nss atk at-spi2-core cups libdrm libxkbcommon mesa pango cairo alsa-lib >/dev/null 2>&1 || true
                        else
                            log_warn "Cannot install browser deps without sudo. Run manually:"
                            log_warn "  sudo pacman -S nss atk at-spi2-core cups libdrm libxkbcommon mesa pango cairo alsa-lib"
                        fi
                    fi
                    cd "$INSTALL_DIR" && run_browser_install_with_timeout 600 npx playwright install chromium 2>/dev/null || {
                        log_warn "Playwright browser installation failed — browser tools will not work."
                    }
                    ;;
                fedora|rhel|centos|rocky|alma)
                    log_warn "Playwright does not support automatic dependency installation on RPM-based systems."
                    log_info "Install Chromium system dependencies manually before using browser tools:"
                    log_info "  sudo dnf install nss atk at-spi2-core cups-libs libdrm libxkbcommon mesa-libgbm pango cairo alsa-lib"
                    cd "$INSTALL_DIR" && run_browser_install_with_timeout 600 npx playwright install chromium 2>/dev/null || {
                        log_warn "Playwright browser installation failed — install dependencies above and retry."
                    }
                    ;;
                opensuse*|sles)
                    log_warn "Playwright does not support automatic dependency installation on zypper-based systems."
                    log_info "Install Chromium system dependencies manually before using browser tools:"
                    log_info "  sudo zypper install mozilla-nss libatk-1_0-0 at-spi2-core cups-libs libdrm2 libxkbcommon0 Mesa-libgbm1 pango cairo libasound2"
                    cd "$INSTALL_DIR" && run_browser_install_with_timeout 600 npx playwright install chromium 2>/dev/null || {
                        log_warn "Playwright browser installation failed — install dependencies above and retry."
                    }
                    ;;
                *)
                    log_warn "Playwright does not support automatic dependency installation on $DISTRO."
                    log_info "Install Chromium/browser system dependencies for your distribution, then run:"
                    log_info "  cd $INSTALL_DIR && npx playwright install chromium"
                    log_info "Browser tools will not work until dependencies are installed."
                    cd "$INSTALL_DIR" && run_browser_install_with_timeout 600 npx playwright install chromium 2>/dev/null || true
                    ;;
            esac
        fi
        fi
        log_success "Browser engine setup complete"
    fi

    # Install TUI dependencies
    if [ -f "$INSTALL_DIR/ui-tui/package.json" ]; then
        log_info "Installing TUI dependencies..."
        cd "$INSTALL_DIR/ui-tui"
        npm install --silent 2>/dev/null || {
            log_warn "TUI npm install failed (hermes --tui may not work)"
        }
        log_success "TUI dependencies installed"
    fi

    # Keep the checkout clean so `hermes update` doesn't autostash every run.
    restore_dirty_lockfiles "$INSTALL_DIR"
}

run_setup_wizard() {
    if [ "$RUN_SETUP" = false ]; then
        log_info "Skipping setup wizard (--skip-setup)"
        return 0
    fi

    # The setup wizard reads from /dev/tty, so it works even when the
    # install script itself is piped (curl | bash). Only skip if no
    # terminal is available at all (e.g. Docker build, CI).
    #
    # Probe by actually opening /dev/tty: a bare existence test passes
    # in Docker builds where the device node is in the mount namespace
    # but opening fails with ENXIO, so the wizard would proceed and
    # then crash on `< /dev/tty` below.
    if ! (: </dev/tty) 2>/dev/null; then
        log_info "Setup wizard skipped (no terminal available). Run 'hermes setup' after install."
        return 0
    fi

    echo ""
    log_info "Starting setup wizard..."
    echo ""

    cd "$INSTALL_DIR"

    # Run hermes setup using the venv Python directly (no activation needed).
    # Redirect stdin from /dev/tty so interactive prompts work when piped from curl.
    if [ "$USE_VENV" = true ]; then
        "$INSTALL_DIR/venv/bin/python" -m hermes_cli.main setup < /dev/tty
    else
        python -m hermes_cli.main setup < /dev/tty
    fi
}

maybe_start_gateway() {
    # Check if any messaging platform tokens were configured
    ENV_FILE="$HERMES_HOME/.env"
    if [ ! -f "$ENV_FILE" ]; then
        return 0
    fi

    HAS_MESSAGING=false
    for VAR in TELEGRAM_BOT_TOKEN DISCORD_BOT_TOKEN SLACK_BOT_TOKEN SLACK_APP_TOKEN WHATSAPP_ENABLED; do
        VAL=$(grep "^${VAR}=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2-)
        if [ -n "$VAL" ] && [ "$VAL" != "your-token-here" ]; then
            HAS_MESSAGING=true
            break
        fi
    done

    if [ "$HAS_MESSAGING" = false ]; then
        return 0
    fi

    echo ""
    log_info "Messaging platform token detected!"
    log_info "The gateway needs to be running for Hermes to send/receive messages."

    # If WhatsApp is enabled and no session exists yet, run foreground first for QR scan
    WHATSAPP_VAL=$(grep "^WHATSAPP_ENABLED=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2-)
    WHATSAPP_SESSION="$HERMES_HOME/whatsapp/session/creds.json"
    if [ "$WHATSAPP_VAL" = "true" ] && [ ! -f "$WHATSAPP_SESSION" ]; then
        if [ "$IS_INTERACTIVE" = true ]; then
            echo ""
            log_info "WhatsApp is enabled but not yet paired."
            log_info "Running 'hermes whatsapp' to pair via QR code..."
            echo ""
            if prompt_yes_no "Pair WhatsApp now?" "yes"; then
                HERMES_CMD="$(get_hermes_command_path)"
                $HERMES_CMD whatsapp || true
            fi
        else
            log_info "WhatsApp pairing skipped (non-interactive). Run 'hermes whatsapp' to pair."
        fi
    fi

    # Probe by actually opening /dev/tty: a bare existence test passes
    # in Docker builds where the device node is in the mount namespace
    # but opening fails with ENXIO. See #16746.
    if ! (: </dev/tty) 2>/dev/null; then
        log_info "Gateway setup skipped (no terminal available). Run 'hermes gateway install' later."
        return 0
    fi

    echo ""
    local should_install_gateway=false
    if [ "$DISTRO" = "termux" ]; then
        if prompt_yes_no "Would you like to start the gateway in the background?" "yes"; then
            should_install_gateway=true
        fi
    else
        if prompt_yes_no "Would you like to install the gateway as a background service?" "yes"; then
            should_install_gateway=true
        fi
    fi

    if [ "$should_install_gateway" = true ]; then
        HERMES_CMD="$(get_hermes_command_path)"

        if [ "$DISTRO" != "termux" ] && command -v systemctl &> /dev/null; then
            log_info "Installing systemd service..."
            if $HERMES_CMD gateway install 2>/dev/null; then
                log_success "Gateway service installed"
                if $HERMES_CMD gateway start 2>/dev/null; then
                    log_success "Gateway started! Your bot is now online."
                else
                    log_warn "Service installed but failed to start. Try: hermes gateway start"
                fi
            else
                log_warn "Systemd install failed. You can start manually: hermes gateway"
            fi
        else
            if [ "$DISTRO" = "termux" ]; then
                log_info "Termux detected — starting gateway in best-effort background mode..."
            else
                log_info "systemd not available — starting gateway in background..."
            fi
            nohup $HERMES_CMD gateway > "$HERMES_HOME/logs/gateway.log" 2>&1 &
            GATEWAY_PID=$!
            log_success "Gateway started (PID $GATEWAY_PID). Logs: ~/.hermes/logs/gateway.log"
            log_info "To stop: kill $GATEWAY_PID"
            log_info "To restart later: hermes gateway"
            if [ "$DISTRO" = "termux" ]; then
                log_warn "Android may stop background processes when Termux is suspended or the system reclaims resources."
            fi
        fi
    else
        log_info "Skipped. Start the gateway later with: hermes gateway"
    fi
}

print_success() {
    echo ""
    echo -e "${GREEN}${BOLD}"
    echo "┌─────────────────────────────────────────────────────────┐"
    echo "│              ✓ Installation Complete!                   │"
    echo "└─────────────────────────────────────────────────────────┘"
    echo -e "${NC}"
    echo ""

    # Show file locations
    echo -e "${CYAN}${BOLD}📁 Your files:${NC}"
    echo ""
    echo -e "   ${YELLOW}Config:${NC}    $HERMES_HOME/config.yaml"
    echo -e "   ${YELLOW}API Keys:${NC}  $HERMES_HOME/.env"
    echo -e "   ${YELLOW}Data:${NC}      $HERMES_HOME/cron/, sessions/, logs/"
    echo -e "   ${YELLOW}Code:${NC}      $INSTALL_DIR"
    echo ""

    echo -e "${CYAN}─────────────────────────────────────────────────────────${NC}"
    echo ""
    echo -e "${CYAN}${BOLD}🚀 Commands:${NC}"
    echo ""
    echo -e "   ${GREEN}hermes${NC}              Start chatting"
    echo -e "   ${GREEN}hermes setup${NC}        Configure API keys & settings"
    echo -e "   ${GREEN}hermes config${NC}       View/edit configuration"
    echo -e "   ${GREEN}hermes config edit${NC}  Open config in editor"
    echo -e "   ${GREEN}hermes gateway install${NC} Install gateway service (messaging + cron)"
    echo -e "   ${GREEN}hermes update${NC}       Update to latest version"
    echo ""

    echo -e "${CYAN}─────────────────────────────────────────────────────────${NC}"
    echo ""
    if [ "$DISTRO" = "termux" ]; then
        echo -e "${YELLOW}⚡ 'hermes' was linked into $(get_command_link_display_dir), which is already on PATH in Termux.${NC}"
        echo ""
    elif [ "$ROOT_FHS_LAYOUT" = true ]; then
        echo -e "${YELLOW}⚡ 'hermes' was linked into /usr/local/bin and is ready to use — no shell reload needed.${NC}"
        echo ""
    else
        echo -e "${YELLOW}⚡ Reload your shell to use 'hermes' command:${NC}"
        echo ""
        LOGIN_SHELL="$(basename "${SHELL:-/bin/bash}")"
        if [ "$LOGIN_SHELL" = "zsh" ]; then
            echo "   source ~/.zshrc"
        elif [ "$LOGIN_SHELL" = "bash" ]; then
            echo "   source ~/.bashrc"
        elif [ "$LOGIN_SHELL" = "fish" ]; then
            echo "   source ~/.config/fish/config.fish"
        else
            echo "   source ~/.bashrc   # or ~/.zshrc"
        fi
        echo ""
    fi

    # Show Node.js warning if auto-install failed
    if [ "$HAS_NODE" = false ]; then
        echo -e "${YELLOW}"
        echo "Note: Node.js could not be installed automatically."
        echo "Browser tools need Node.js. Install manually:"
        if [ "$DISTRO" = "termux" ]; then
            echo "  pkg install nodejs"
        else
            echo "  https://nodejs.org/en/download/"
        fi
        echo -e "${NC}"
    fi

    # Show ripgrep note if not installed
    if [ "$HAS_RIPGREP" = false ]; then
        echo -e "${YELLOW}"
        echo "Note: ripgrep (rg) was not found. File search will use"
        echo "grep as a fallback. For faster search in large codebases,"
        if [ "$DISTRO" = "termux" ]; then
            echo "install ripgrep: pkg install ripgrep"
        else
            echo "install ripgrep: sudo apt install ripgrep (or brew install ripgrep)"
        fi
        echo -e "${NC}"
    fi
}

ensure_browser() {
    if ! command -v node >/dev/null 2>&1; then
        local node_bin="$HERMES_HOME/node/bin/node"
        if [ -x "$node_bin" ]; then
            export PATH="$HERMES_HOME/node/bin:$PATH"
        else
            log_error "Node.js not found. Run with --ensure node first."
            return 1
        fi
    fi

    local npm_bin
    npm_bin="$(command -v npm 2>/dev/null || echo "$HERMES_HOME/node/bin/npm")"
    if [ ! -x "$npm_bin" ]; then
        log_error "npm not found"
        return 1
    fi

    log_info "Installing agent-browser..."
    local log_file
    log_file="$(mktemp)"
    if ! "$npm_bin" install -g --prefix "$HERMES_HOME/node" --silent --ignore-scripts \
        "agent-browser@^0.26.0" \
        "@askjo/camofox-browser@^1.5.2" \
        >"$log_file" 2>&1; then
        log_error "npm install failed:"
        cat "$log_file" >&2
        rm -f "$log_file"
        return 1
    fi
    rm -f "$log_file"
    export PATH="$HERMES_HOME/node/bin:$PATH"

    local sys_browser
    sys_browser="$(find_system_browser 2>/dev/null || true)"
    if [ -n "$sys_browser" ]; then
        configure_browser_env_from_system_browser "$sys_browser"
        log_info "System browser detected -- skipping Chromium download"
        return 0
    fi

    log_info "Installing Chromium via agent-browser install..."
    local ab_bin="$HERMES_HOME/node/bin/agent-browser"
    if [ -x "$ab_bin" ]; then
        "$ab_bin" install 2>/dev/null || {
            log_warn "Chromium install failed. Browser tools may not work without a system browser."

            # OS-specific hints (detect_os sets $DISTRO)
            case "${DISTRO:-unknown}" in
                ubuntu|debian)
                    log_info "Try: sudo apt-get install -y chromium-browser"
                    ;;
                arch)
                    log_info "Try: sudo pacman -S chromium"
                    ;;
                fedora|rhel|centos)
                    log_info "Try: sudo dnf install -y chromium"
                    ;;
            esac
        }
    else
        log_warn "agent-browser not found at $ab_bin"
    fi

    return 0
}

ensure_mode() {
    detect_os

    IFS=',' read -ra DEPS <<< "$ENSURE_DEPS"
    for dep in "${DEPS[@]}"; do
        dep="$(echo "$dep" | tr -d '[:space:]')"
        case "$dep" in
            node)
                check_node
                ;;
            browser)
                check_node
                if [ "$HAS_NODE" = true ]; then
                    ensure_browser
                fi
                ;;
            ripgrep)
                if ! command -v rg &>/dev/null; then
                    HAS_RIPGREP=false
                    HAS_FFMPEG=true
                    install_system_packages
                fi
                ;;
            ffmpeg)
                if ! command -v ffmpeg &>/dev/null; then
                    HAS_FFMPEG=false
                    HAS_RIPGREP=true
                    install_system_packages
                fi
                ;;
            *)
                log_warn "Unknown dependency: $dep"
                ;;
        esac
    done
}

postinstall_mode() {
    print_banner
    detect_os

    log_info "Post-install mode: setting up Hermes for pip install"

    check_node
    check_network_prerequisites
    install_system_packages

    if [ "$HAS_NODE" = true ] && [ "$SKIP_BROWSER" = false ]; then
        ensure_browser
    fi

    HERMES_CMD="$(command -v hermes 2>/dev/null || echo "")"
    if [ -n "$HERMES_CMD" ]; then
        log_info "Running hermes setup..."
        "$HERMES_CMD" setup
    else
        log_warn "hermes command not found on PATH"
        log_info "Try: python -m hermes_cli.main setup"
    fi
}

# Clear the cached Electron download + any half-written unpacked output so the
# next `npm run pack` re-downloads and re-stages from scratch. A corrupt zip in
# the per-user Electron download cache - most often a partial/resumed download
# that leaves concatenated junk - makes electron-builder's `unpack-electron`
# extract a tree MISSING the electron binary, so the `electron`->`Hermes` rename
# dies with ENOENT and every re-run repeats the broken extraction forever. This
# is the bash sibling of install.ps1's Clear-ElectronBuildCache and the Python
# _purge_electron_build_cache() used by `hermes desktop`; install.sh was the only
# build path lacking it. Echoes the removed paths (one per line); best-effort.
clear_electron_build_cache() {
    local desktop_dir="$1"
    local removed=""

    # Per-user Electron download cache dirs, honoring the overrides @electron/get
    # respects, then the platform defaults (macOS: ~/Library/Caches/electron,
    # Linux: $XDG_CACHE_HOME/electron or ~/.cache/electron).
    local cache_dirs=()
    [ -n "${electron_config_cache:-}" ] && cache_dirs+=("$electron_config_cache")
    [ -n "${ELECTRON_CACHE:-}" ] && cache_dirs+=("$ELECTRON_CACHE")
    if [ "$OS" = "macos" ]; then
        cache_dirs+=("$HOME/Library/Caches/electron")
    else
        [ -n "${XDG_CACHE_HOME:-}" ] && cache_dirs+=("$XDG_CACHE_HOME/electron")
        cache_dirs+=("$HOME/.cache/electron")
    fi

    local dir zip
    for dir in "${cache_dirs[@]}"; do
        [ -d "$dir" ] || continue
        # Recurse: the bad copy may be the top-level zip OR a copy inside an
        # @electron/get hash subdir.
        while IFS= read -r zip; do
            [ -n "$zip" ] || continue
            if rm -f "$zip" 2>/dev/null; then
                removed="$removed$zip
"
            fi
        done <<EOF
$(find "$dir" -type f -name 'electron-*.zip' 2>/dev/null)
EOF
    done

    # A half-written unpacked dir from an interrupted prior pack poisons the
    # rename even after the zip is fixed (mac-arm64-unpacked / linux-unpacked).
    local release_dir="$desktop_dir/release"
    if [ -d "$release_dir" ]; then
        local unpacked
        while IFS= read -r unpacked; do
            [ -n "$unpacked" ] || continue
            if rm -rf "$unpacked" 2>/dev/null; then
                removed="$removed$unpacked
"
            fi
        done <<EOF
$(find "$release_dir" -maxdepth 1 -type d -name '*-unpacked' 2>/dev/null)
EOF
    fi

    printf '%s' "$removed"
}

# Run the desktop pack in $1 (the apps/desktop dir). `npm run pack` = tsc +
# vite build + electron-builder --dir, producing an unpacked app for the
# current OS. Signing auto-discovery is disabled so electron-builder falls back
# to an ad-hoc signature instead of grabbing an unrelated Developer ID from the
# keychain (a real signed/notarized .dmg needs Apple credentials — a separate
# release concern). Optional $2 = an ELECTRON_MIRROR base URL for this attempt,
# used as a fallback when the default GitHub release download is blocked.
_desktop_pack() {
    local desktop_dir="$1"
    local mirror="${2:-}"
    if [ -n "$mirror" ]; then
        ( cd "$desktop_dir" && ELECTRON_MIRROR="$mirror" CSC_IDENTITY_AUTO_DISCOVERY=false npm run pack )
    else
        ( cd "$desktop_dir" && CSC_IDENTITY_AUTO_DISCOVERY=false npm run pack )
    fi
}

# Some upstream branches may not yet ship desktop German locale wiring.
# Patch it before building the desktop app so the language picker always
# includes "Deutsch" and default locale resolves to "de".
ensure_desktop_german_locale() {
    local desktop_dir="$1"
    local i18n_dir="$desktop_dir/src/i18n"
    local py=""

    [ -d "$i18n_dir" ] || return 0

    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        py="$INSTALL_DIR/venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        py="$(command -v python)"
    else
        log_warn "Python unavailable; skipping desktop German locale patch"
        return 0
    fi

    "$py" - "$i18n_dir" <<'PY'
from pathlib import Path
import re
import sys

i18n = Path(sys.argv[1])
changed = False

def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")

types_path = i18n / "types.ts"
languages_path = i18n / "languages.ts"
catalog_path = i18n / "catalog.ts"
en_path = i18n / "en.ts"
de_path = i18n / "de.ts"

# Ensure de.ts exists. If upstream doesn't have a dedicated German file yet,
# clone English as a safe fallback so runtime keys remain complete.
if not de_path.exists() and en_path.exists():
    de_content = read(en_path).replace("export const en: Translations = {", "export const de: Translations = {", 1)
    write(de_path, de_content)
    changed = True

if types_path.exists():
    t = read(types_path)
    m = re.search(r"export type Locale = ([^\n]+)", t)
    if m and "'de'" not in m.group(1):
        updated = f"export type Locale = 'de' | {m.group(1)}"
        t2 = t[: m.start()] + updated + t[m.end() :]
        if t2 != t:
            write(types_path, t2)
            changed = True

if languages_path.exists():
    t = read(languages_path)
    t2 = t

    t2 = t2.replace("export const DEFAULT_LOCALE: Locale = 'en'", "export const DEFAULT_LOCALE: Locale = 'de'")

    if "id: 'de'" not in t2:
        t2 = t2.replace(
            "export const LOCALE_OPTIONS = [\n",
            "export const LOCALE_OPTIONS = [\n"
            "  {\n"
            "    id: 'de',\n"
            "    name: 'Deutsch',\n"
            "    englishName: 'German',\n"
            "    configValue: 'de'\n"
            "  },\n",
            1,
        )

    if "\n  de: 'de'," not in t2:
        t2 = t2.replace(
            "const LOCALE_ALIASES: Record<string, Locale> = {\n",
            "const LOCALE_ALIASES: Record<string, Locale> = {\n"
            "  de: 'de',\n"
            "  'de-de': 'de',\n"
            "  de_de: 'de',\n"
            "  'de-at': 'de',\n"
            "  de_at: 'de',\n"
            "  'de-ch': 'de',\n"
            "  de_ch: 'de',\n",
            1,
        )

    if t2 != t:
        write(languages_path, t2)
        changed = True

if catalog_path.exists():
    t = read(catalog_path)
    t2 = t
    if "import { de } from './de'" not in t2:
        t2 = t2.replace("import { en } from './en'\n", "import { de } from './de'\nimport { en } from './en'\n", 1)
    if re.search(r"export const TRANSLATIONS: Record<Locale, Translations> = \{\n", t2) and "\n  de,\n" not in t2:
        t2 = t2.replace(
            "export const TRANSLATIONS: Record<Locale, Translations> = {\n",
            "export const TRANSLATIONS: Record<Locale, Translations> = {\n  de,\n",
            1,
        )
    if t2 != t:
        write(catalog_path, t2)
        changed = True

print("patched" if changed else "already")
PY
}

# Creates/patches the LiteLLM Hub gateway module and RPC methods in the
# cloned upstream repo. Idempotent.
ensure_litellm_hub_gateway() {
    local repo_dir="$1"
    local py=""

    [ -d "$repo_dir" ] || return 0

    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        py="$INSTALL_DIR/venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        py="$(command -v python)"
    else
        log_warn "Python unavailable; skipping LiteLLM Hub gateway patch"
        return 0
    fi

    "$py" - "$repo_dir" <<'PY'
from pathlib import Path
import sys

repo = Path(sys.argv[1])
changed = False

def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write(p: Path, content: str) -> None:
    p.write_text(content, encoding="utf-8")

# ---------------------------------------------------------------------------
# 1. Create/overwrite agent/litellm_hub_client.py with provider-fallback logic
# ---------------------------------------------------------------------------
hub_client = repo / "agent" / "litellm_hub_client.py"
hub_client_content = '''\
"""Helpers for LiteLLM public hub endpoints (Skill/Agent/Model Hub)."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import httpx

_HUB_PATH_ALIASES = {
    "agents": "agent_hub",
    "skills": "skill_hub",
}


def resolve_litellm_hub_settings() -> Dict[str, Any]:
    """Resolve LiteLLM hub settings from config.yaml with env fallback.

    Resolution order for base_url:
    1. skills.litellm_hub.base_url (explicit hub override)
    2. LITELLM_PROXY_URL env var
    3. IAMDS_LITELLM_BASE_URL env var
    4. OPENAI_BASE_URL env var (legacy fallback)
    4. First provider entry with a base_url (the model provider is usually LiteLLM)
    """
    from hermes_cli.config import load_config

    cfg = load_config() or {}
    skills_cfg = cfg.get("skills", {}) if isinstance(cfg, dict) else {}
    hub_cfg = skills_cfg.get("litellm_hub", {}) if isinstance(skills_cfg, dict) else {}

    base_url = str(
        hub_cfg.get("base_url")
        or os.getenv("LITELLM_PROXY_URL")
        or os.getenv("IAMDS_LITELLM_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or _first_provider_base_url(cfg)
        or ""
    ).strip().rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    api_key = str(
        hub_cfg.get("api_key")
        or os.getenv("LITELLM_KEY")
        or os.getenv("OPENAI_API_KEY")
        or _first_provider_api_key(cfg)
        or ""
    ).strip()
    timeout_raw = hub_cfg.get("timeout", 20)
    try:
        timeout = max(1, int(timeout_raw))
    except (TypeError, ValueError):
        timeout = 20

    return {"base_url": base_url, "api_key": api_key, "timeout": timeout}


def _first_provider_base_url(cfg: Dict[str, Any]) -> str:
    providers = cfg.get("providers", {}) if isinstance(cfg, dict) else {}
    if not isinstance(providers, dict):
        return ""
    for entry in providers.values():
        if not isinstance(entry, dict):
            continue
        for key in ("base_url", "url", "api"):
            val = entry.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().rstrip("/")
    return ""


def _first_provider_api_key(cfg: Dict[str, Any]) -> str:
    providers = cfg.get("providers", {}) if isinstance(cfg, dict) else {}
    if not isinstance(providers, dict):
        return ""
    for entry in providers.values():
        if not isinstance(entry, dict):
            continue
        val = entry.get("api_key")
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def fetch_litellm_hub_json(
    public_path: str,
    *,
    require_auth: bool,
    settings: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """Fetch JSON from ``/public/<public_path>`` on a LiteLLM proxy."""
    settings = settings or resolve_litellm_hub_settings()
    base_url = str(settings.get("base_url", "")).strip().rstrip("/")
    api_key = str(settings.get("api_key", "")).strip()
    timeout = settings.get("timeout", 20)

    if not base_url:
        return None, (
            "LiteLLM hub is not configured. "
            "Set skills.litellm_hub.base_url in config.yaml "
            "or configure a provider with a base_url."
        )
    if require_auth and not api_key:
        return None, (
            "This LiteLLM hub endpoint requires authentication. "
            "Set skills.litellm_hub.api_key (or LITELLM_KEY)."
        )

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    public_endpoint = _HUB_PATH_ALIASES.get(public_path.strip("/"), public_path.strip("/"))
    url = f"{base_url}/public/{public_endpoint}"
    try:
        resp = httpx.get(
            url,
            headers=headers or None,
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        return None, f"Failed to reach LiteLLM hub at {url}: {exc}"

    if resp.status_code == 401:
        return None, "LiteLLM hub request failed: unauthorized (401). Check API key."
    if resp.status_code == 403:
        return None, "LiteLLM hub request failed: forbidden (403). Check API key scope."
    if resp.status_code != 200:
        return None, f"LiteLLM hub request failed: HTTP {resp.status_code} from {url}."

    try:
        return resp.json(), None
    except ValueError:
        return None, f"LiteLLM hub returned non-JSON response from {url}."
'''

# Always write — ensures updated resolver even if file already exists
existing = read(hub_client) if hub_client.exists() else ""
if hub_client_content != existing:
    hub_client.parent.mkdir(parents=True, exist_ok=True)
    write(hub_client, hub_client_content)
    changed = True

# ---------------------------------------------------------------------------
# 2. Patch tui_gateway/server.py — append litellm_hub methods if missing
# ---------------------------------------------------------------------------
server_py = repo / "tui_gateway" / "server.py"
if server_py.exists():
    t = read(server_py)
    if 'litellm_hub.agents' not in t:
        # Find a good insertion point: just before @method("plugins.manage") if present,
        # else append before the last non-blank line.
        insertion = (
            '\n\n@method("litellm_hub.agents")\n'
            'def _(rid, params: dict) -> dict:\n'
            '    """Fetch LiteLLM Agent Hub entries."""\n'
            '    try:\n'
            '        from agent.litellm_hub_client import fetch_litellm_hub_json\n'
            '        data, error = fetch_litellm_hub_json("agents", require_auth=False)\n'
            '        if error:\n'
            '            return _err(rid, 5026, error)\n'
            '        agents = data if isinstance(data, list) else (data.get("agents", []) if data else [])\n'
            '        return _ok(rid, {"agents": agents})\n'
            '    except Exception as e:\n'
            '        return _err(rid, 5027, str(e))\n'
            '\n\n'
            '@method("litellm_hub.skills")\n'
            'def _(rid, params: dict) -> dict:\n'
            '    """Fetch LiteLLM Skill Hub entries."""\n'
            '    try:\n'
            '        from agent.litellm_hub_client import fetch_litellm_hub_json\n'
            '        data, error = fetch_litellm_hub_json("skills", require_auth=False)\n'
            '        if error:\n'
            '            return _err(rid, 5028, error)\n'
            '        skills = data if isinstance(data, list) else (data.get("skills", []) if data else [])\n'
            '        return _ok(rid, {"skills": skills})\n'
            '    except Exception as e:\n'
            '        return _err(rid, 5029, str(e))\n'
        )
        anchor = '@method("plugins.manage")'
        if anchor in t:
            t2 = t.replace(anchor, insertion.lstrip('\n') + '\n' + anchor, 1)
        else:
            t2 = t.rstrip() + insertion
        write(server_py, t2)
        changed = True

print("patched" if changed else "already")
PY

    # macOS-specific guardrail: some managed install paths end up with a stale or
    # unexpected agent/litellm_hub_client.py after patching. Keep the repository
    # canonical on macOS by restoring this file from git right after the gateway
    # patch step.
    if [ "$OS" = "macos" ] && [ -d "$repo_dir/.git" ]; then
        if git -C "$repo_dir" checkout -- agent/litellm_hub_client.py 2>/dev/null; then
            log_info "macOS guardrail: restored agent/litellm_hub_client.py from git"
        else
            log_warn "macOS guardrail: failed to restore agent/litellm_hub_client.py from git"
        fi
    fi
}

# Patches the cloned desktop source to add the Discover / Hub sidebar section.
# Idempotent; safe to call even if the upstream already has the changes.
ensure_desktop_hub_sidebar() {
    local desktop_dir="$1"
    local app_dir="$desktop_dir/src/app"
    local py=""

    [ -d "$app_dir" ] || return 0

    if [ -x "$INSTALL_DIR/venv/bin/python" ]; then
        py="$INSTALL_DIR/venv/bin/python"
    elif command -v python3 >/dev/null 2>&1; then
        py="$(command -v python3)"
    elif command -v python >/dev/null 2>&1; then
        py="$(command -v python)"
    else
        log_warn "Python unavailable; skipping desktop Hub sidebar patch"
        return 0
    fi

    "$py" - "$app_dir" <<'PY'
from pathlib import Path
import re
import sys

app_dir = Path(sys.argv[1])
i18n_dir = app_dir.parent / "i18n"
changed = False

def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")

def write(p: Path, content: str) -> None:
    p.write_text(content, encoding="utf-8")

def patch(p: Path, old: str, new: str) -> bool:
    if not p.exists():
        return False
    t = read(p)
    if old in t:
        write(p, t.replace(old, new, 1))
        return True
    return False  # already patched or not found

# ---------------------------------------------------------------------------
# 1. Create hub/index.tsx if missing
# ---------------------------------------------------------------------------
hub_dir = app_dir / "hub"
hub_file = hub_dir / "index.tsx"
if not hub_file.exists():
    hub_dir.mkdir(parents=True, exist_ok=True)
    hub_file.write_text(
        r"""import type * as React from 'react'
import { useEffect, useMemo, useState } from 'react'

import { PageLoader } from '@/components/page-loader'
import { Badge } from '@/components/ui/badge'
import { Codicon } from '@/components/ui/codicon'
import { TextTab, TextTabMeta } from '@/components/ui/text-tab'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { notifyError } from '@/store/notifications'
import { useGatewayRequest } from '../gateway/hooks/use-gateway-request'

import { useRefreshHotkey } from '../hooks/use-refresh-hotkey'
import { useRouteEnumParam } from '../hooks/use-route-enum-param'
import { PAGE_INSET_X } from '../layout-constants'
import { PageSearchShell } from '../page-search-shell'
import { includesQuery, prettyName } from '../settings/helpers'

const HUB_MODES = ['agents', 'skills'] as const
type HubMode = (typeof HUB_MODES)[number]

interface LiteLLMAgent {
  id: string
  name: string
  description?: string
}

interface LiteLLMSkill {
  id: string
  name: string
  description?: string
  source?: string
}

function filteredAgents(agents: LiteLLMAgent[], query: string): LiteLLMAgent[] {
  const q = query.trim().toLowerCase()
  return agents
    .filter(agent => !q || includesQuery(agent.name, q) || includesQuery(agent.description || '', q))
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
}

function filteredSkills(skills: LiteLLMSkill[], query: string): LiteLLMSkill[] {
  const q = query.trim().toLowerCase()
  return skills
    .filter(skill => !q || includesQuery(skill.name, q) || includesQuery(skill.description || '', q))
    .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
}

interface HubViewProps extends React.ComponentProps<'section'> {}

export function HubView({ ...props }: HubViewProps) {
  const { t } = useI18n()
  const { requestGateway } = useGatewayRequest()
  const [mode, setMode] = useRouteEnumParam('tab', HUB_MODES, 'agents')

  const [query, setQuery] = useState('')
  const [agents, setAgents] = useState<LiteLLMAgent[] | null>(null)
  const [skills, setSkills] = useState<LiteLLMSkill[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    setError(null)
    try {
      if (mode === 'agents') {
        const data = await requestGateway<{ agents: unknown[] }>('litellm_hub.agents', { limit: 100 })
        setAgents((data?.agents || []).map((a: unknown) => {
          const x = a as Record<string, unknown>
          return { id: String(x.id || x.name || ''), name: String(x.name || ''), description: x.description ? String(x.description) : undefined }
        }))
      } else {
        const data = await requestGateway<{ skills: unknown[] }>('litellm_hub.skills', { limit: 100 })
        setSkills((data?.skills || []).map((s: unknown) => {
          const x = s as Record<string, unknown>
          return { id: String(x.id || x.name || ''), name: String(x.name || ''), description: x.description ? String(x.description) : undefined, source: x.source ? String(x.source) : undefined }
        }))
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      notifyError(err, `Failed to load ${mode}`)
    }
  }

  useEffect(() => { void refresh() }, [mode])
  useRefreshHotkey(refresh)

  const filteredAgentsList = useMemo(() => agents ? filteredAgents(agents, query) : null, [agents, query])
  const filteredSkillsList = useMemo(() => skills ? filteredSkills(skills, query) : null, [skills, query])
  const isLoading = (mode === 'agents' && agents === null) || (mode === 'skills' && skills === null)

  return (
    <section {...props} className={cn('flex flex-col overflow-hidden', props.className)}>
      <PageSearchShell
        searchValue={query}
        onSearchChange={setQuery}
        searchPlaceholder={mode === 'agents' ? 'Search agents...' : 'Search skills...'}
        tabs={
          <>
            <TextTab active={mode === 'agents'} onClick={() => setMode('agents')} className="data-[active]:bg-accent/5">
              <span>Agents</span>
              {filteredAgentsList && <Badge variant="outline" className="ml-2 pointer-events-none">{filteredAgentsList.length}</Badge>}
            </TextTab>
            <TextTab active={mode === 'skills'} onClick={() => setMode('skills')} className="data-[active]:bg-accent/5">
              <span>Skills</span>
              {filteredSkillsList && <Badge variant="outline" className="ml-2 pointer-events-none">{filteredSkillsList.length}</Badge>}
            </TextTab>
          </>
        }
      >
        {error && (
          <div className="px-4 py-3 text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900 m-4 rounded">
            {error}
          </div>
        )}
        {isLoading ? (
          <PageLoader />
        ) : mode === 'agents' ? (
          <AgentsList agents={filteredAgentsList || []} query={query} />
        ) : (
          <SkillsList skills={filteredSkillsList || []} query={query} />
        )}
      </PageSearchShell>
    </section>
  )
}

function AgentsList({ agents, query }: { agents: LiteLLMAgent[]; query: string }) {
  return (
    <div className="overflow-y-auto flex-1">
      {agents.length === 0 ? (
        <div className={cn('flex items-center justify-center h-full text-sm text-muted-foreground', PAGE_INSET_X)}>
          {query ? 'No agents match your search.' : 'No agents available.'}
        </div>
      ) : (
        <div className={cn('space-y-1 p-4', PAGE_INSET_X)}>
          {agents.map(agent => (
            <div key={agent.id} className="p-3 rounded border border-border bg-card hover:bg-accent/5 transition-colors">
              <div className="flex items-start gap-2">
                <Codicon className="mt-1" name="robot" />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-foreground">{agent.name}</div>
                  {agent.description && <div className="text-sm text-muted-foreground mt-1 line-clamp-2">{agent.description}</div>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SkillsList({ skills, query }: { skills: LiteLLMSkill[]; query: string }) {
  return (
    <div className="overflow-y-auto flex-1">
      {skills.length === 0 ? (
        <div className={cn('flex items-center justify-center h-full text-sm text-muted-foreground', PAGE_INSET_X)}>
          {query ? 'No skills match your search.' : 'No skills available.'}
        </div>
      ) : (
        <div className={cn('space-y-1 p-4', PAGE_INSET_X)}>
          {skills.map(skill => (
            <div key={skill.id} className="p-3 rounded border border-border bg-card hover:bg-accent/5 transition-colors">
              <div className="flex items-start gap-2">
                <Codicon className="mt-1" name="lightbulb" />
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-foreground">{skill.name}</div>
                  {skill.description && <div className="text-sm text-muted-foreground mt-1 line-clamp-2">{skill.description}</div>}
                  {skill.source && <div className="text-xs text-muted-foreground/70 mt-1 font-mono truncate">{skill.source}</div>}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
""",
        encoding="utf-8",
    )
    changed = True

# ---------------------------------------------------------------------------
# 2. routes.ts — add HUB_ROUTE constant, 'hub' to AppView/AppRouteId/APP_ROUTES/OVERLAY_VIEWS
# ---------------------------------------------------------------------------
routes_path = app_dir / "routes.ts"
if routes_path.exists():
    t = read(routes_path)
    t2 = t

    # HUB_ROUTE constant
    if "HUB_ROUTE" not in t2:
        t2 = t2.replace(
            "export const AGENTS_ROUTE = '/agents'",
            "export const AGENTS_ROUTE = '/agents'\nexport const HUB_ROUTE = '/hub'",
            1,
        )

    # AppView union — add 'hub'
    m = re.search(r"(export type AppView =\n(?:  \| '[^']+'\n)+)", t2)
    if m and "'hub'" not in m.group(1):
        old_view = m.group(1)
        new_view = old_view.rstrip("\n") + "\n  | 'hub'\n"
        t2 = t2.replace(old_view, new_view, 1)

    # AppRouteId union — add 'hub'
    m2 = re.search(r"(export type AppRouteId =\n(?:  \| '[^']+'\n)+)", t2)
    if m2 and "'hub'" not in m2.group(1):
        old_id = m2.group(1)
        new_id = old_id.rstrip("\n") + "\n  | 'hub'\n"
        t2 = t2.replace(old_id, new_id, 1)

    # APP_ROUTES array — insert hub entry before agents
    if "path: HUB_ROUTE" not in t2 and "id: 'agents'" in t2:
        t2 = t2.replace(
            "  { id: 'agents', path: AGENTS_ROUTE, view: 'agents' }",
            "  { id: 'hub', path: HUB_ROUTE, view: 'hub' },\n  { id: 'agents', path: AGENTS_ROUTE, view: 'agents' }",
            1,
        )

    # OVERLAY_VIEWS — add 'hub'
    m3 = re.search(r"(export const OVERLAY_VIEWS: ReadonlySet<AppView> = new Set\(\[)([^\]]+)(\]\))", t2)
    if m3 and "'hub'" not in m3.group(2):
        t2 = t2[:m3.start(2)] + m3.group(2).rstrip() + ", 'hub'" + t2[m3.end(2):]

    if t2 != t:
        write(routes_path, t2)
        changed = True

# ---------------------------------------------------------------------------
# 3. desktop-controller.tsx — add HubView lazy import and route
# ---------------------------------------------------------------------------
dc_path = app_dir / "desktop-controller.tsx"
if dc_path.exists():
    t = read(dc_path)
    t2 = t

    # Add import to routes
    if "HUB_ROUTE" not in t2 and "AGENTS_ROUTE" in t2:
        t2 = re.sub(
            r"(import \{[^}]*AGENTS_ROUTE[^}]*\} from '\.\/routes')",
            lambda m: m.group(0).replace("AGENTS_ROUTE", "AGENTS_ROUTE, HUB_ROUTE") if "HUB_ROUTE" not in m.group(0) else m.group(0),
            t2, count=1
        )

    # Lazy HubView import — insert after CronView
    if "HubView" not in t2 and "const CronView" in t2:
        t2 = t2.replace(
            "const CronView = lazy(",
            "const CronView = lazy(",
        )
        t2 = t2.replace(
            "const MessagingView = lazy(",
            "const HubView = lazy(async () => ({ default: (await import('./hub')).HubView }))\nconst MessagingView = lazy(",
            1,
        )

    # Route element — insert hub Route after skills Route
    hub_route_jsx = (
        "          <Route\n"
        "            element={\n"
        "              <Suspense fallback={null}>\n"
        "                <HubView />\n"
        "              </Suspense>\n"
        "            }\n"
        "            path=\"hub\"\n"
        "          />"
    )
    if "<HubView />" not in t2 and 'path="skills"' in t2:
        # Insert after the closing tag of the skills Route block
        t2 = re.sub(
            r'(            path="skills"\n          />)',
            r'\1\n' + hub_route_jsx,
            t2, count=1
        )

    # Null route placeholder — insert after cron null route
    if '<Route element={null} path="hub"' not in t2 and '<Route element={null} path="cron"' in t2:
        t2 = t2.replace(
            '          <Route element={null} path="cron" />',
            '          <Route element={null} path="cron" />\n          <Route element={null} path="hub" />',
            1,
        )

    if t2 != t:
        write(dc_path, t2)
        changed = True

# ---------------------------------------------------------------------------
# 4. types.ts — add 'discover' to SidebarNavId
# ---------------------------------------------------------------------------
types_path = app_dir / "types.ts"
if types_path.exists():
    t = read(types_path)
    if "SidebarNavId" in t and "'discover'" not in t:
        # Insert 'discover' into the union (keep alphabetical: after 'command-center')
        t2 = re.sub(
            r"(export type SidebarNavId = )([^\n]+)",
            lambda m: m.group(1) + " | ".join(
                sorted(set(m.group(2).split(" | ") + ["'discover'"]))
            ) if "'discover'" not in m.group(2) else m.group(0),
            t,
        )
        if t2 != t:
            write(types_path, t2)
            changed = True

# ---------------------------------------------------------------------------
# 5. sidebar/index.tsx — add 'discover' nav item before messaging
# ---------------------------------------------------------------------------
sidebar_path = app_dir / "chat" / "sidebar" / "index.tsx"
if sidebar_path.exists():
    t = read(sidebar_path)
    t2 = t

    # Add HUB_ROUTE to import from '../../routes'
    if "HUB_ROUTE" not in t2 and "SKILLS_ROUTE" in t2:
        t2 = re.sub(
            r"(import \{[^}]*SKILLS_ROUTE[^}]*\} from '\.\.\/\.\.\/routes')",
            lambda m: m.group(0).replace("SKILLS_ROUTE", "SKILLS_ROUTE, HUB_ROUTE") if "HUB_ROUTE" not in m.group(0) else m.group(0),
            t2, count=1
        )

    # Add 'discover' nav item between skills and messaging
    discover_item = (
        "  {\n"
        "    id: 'discover',\n"
        "    label: '',\n"
        "    icon: props => <Codicon name=\"compass\" {...props} />,\n"
        "    route: HUB_ROUTE\n"
        "  },\n"
    )
    if "'discover'" not in t2 and "route: MESSAGING_ROUTE" in t2:
        t2 = re.sub(
            r"(  \{ id: 'messaging',[^\n]+\n)",
            discover_item + r"\1",
            t2, count=1
        )

    if t2 != t:
        write(sidebar_path, t2)
        changed = True

# ---------------------------------------------------------------------------
# 6. i18n/types.ts — add 'discover' to nav Record key union
# ---------------------------------------------------------------------------
i18n_types_path = i18n_dir / "types.ts"
if i18n_types_path.exists():
    t = read(i18n_types_path)
    t2 = t
    # The nav Record key union is the part between Record< and the first comma
    # e.g.  nav: Record<'newChat' | 'settings' | 'skills' | 'messaging' | 'artifacts', { ...
    # We must not match the > inside { title: string; detail: string }
    m = re.search(r"(nav: Record<)([^,]+)(,\s*\{)", t2)
    if m and "'discover'" not in m.group(2):
        new_keys = m.group(2).rstrip() + " | 'discover'"
        t2 = t2[:m.start(2)] + new_keys + t2[m.end(2):]
    if t2 != t:
        write(i18n_types_path, t2)
        changed = True

# ---------------------------------------------------------------------------
# 7. i18n/en.ts — add discover keybind and nav entries
# ---------------------------------------------------------------------------
en_path = i18n_dir / "en.ts"
if en_path.exists():
    t = read(en_path)
    t2 = t
    if "'nav.discover'" not in t2 and "'nav.settings'" in t2:
        t2 = t2.replace(
            "      'nav.settings':",
            "      'nav.discover': 'Open Discover',\n      'nav.settings':",
            1,
        )
    if "discover: {" not in t2 and "skills: {" in t2:
        t2 = re.sub(
            r"(      skills: \{[^}]+\},?\n)",
            r"\1      discover: { title: 'Discover', detail: 'Browse LiteLLM agents and skills' },\n",
            t2, count=1
        )
    if t2 != t:
        write(en_path, t2)
        changed = True

# ---------------------------------------------------------------------------
# 8. i18n/zh.ts — add discover entries
# ---------------------------------------------------------------------------
zh_path = i18n_dir / "zh.ts"
if zh_path.exists():
    t = read(zh_path)
    t2 = t
    if "'nav.discover'" not in t2 and "'nav.settings'" in t2:
        t2 = t2.replace(
            "      'nav.settings':",
            "      'nav.discover': '打开探索',\n      'nav.settings':",
            1,
        )
    if "discover: {" not in t2 and "skills: {" in t2:
        t2 = re.sub(
            r"(      skills: \{[^}]+\},?\n)",
            r"\1      discover: { title: '探索', detail: '浏览 LiteLLM 智能体和技能' },\n",
            t2, count=1
        )
    if t2 != t:
        write(zh_path, t2)
        changed = True

# ---------------------------------------------------------------------------
# 9. i18n/de.ts — add discover entries
# ---------------------------------------------------------------------------
de_path = i18n_dir / "de.ts"
if de_path.exists():
    t = read(de_path)
    t2 = t
    if "'nav.discover'" not in t2 and "'nav.settings'" in t2:
        t2 = t2.replace(
            "      'nav.settings':",
            "      'nav.discover': 'Entdecken öffnen',\n      'nav.settings':",
            1,
        )
    if "discover: {" not in t2 and "skills: {" in t2:
        t2 = re.sub(
            r"(      skills: \{[^}]+\},?\n)",
            r"\1      discover: { title: 'Entdecken', detail: 'LiteLLM-Agenten und Fähigkeiten durchsuchen' },\n",
            t2, count=1
        )
    if t2 != t:
        write(de_path, t2)
        changed = True

print("patched" if changed else "already")
PY
}

# Public Electron mirror used as a last-resort fallback when GitHub's release
# host is blocked/throttled (the repeating "retrying" symptom). npmmirror.com is
# the de-facto Electron community mirror (Alibaba). @electron/get SHASUM-checks
# the download, but the SHASUMS come from the same mirror — that guards against a
# corrupt/partial download, NOT a compromised mirror. Reaching for it is an
# explicit trust trade-off we only make AFTER the canonical GitHub download has
# failed, and we never override a user-pinned ELECTRON_MIRROR.
DESKTOP_ELECTRON_FALLBACK_MIRROR="https://npmmirror.com/mirrors/electron/"

# Build apps/desktop into a launchable native app. Mirrors install.ps1's
# Install-Desktop: a root-level npm install so the apps/* workspace resolves
# the desktop's own deps (Electron ~150MB), then `npm run pack`
# (electron-builder --dir) which emits an unpacked app for the current OS. Only invoked
# via the 'desktop' stage / --include-desktop, which the Electron app's own
# first-launch bootstrap never requests (it must not rebuild itself).
install_desktop() {
    local desktop_dir="$INSTALL_DIR/apps/desktop"

    # The desktop stage only runs when a build is explicitly requested
    # (--include-desktop / 'desktop' stage), so a missing toolchain is a hard
    # failure, not a silent skip — a silent skip yields a "complete" install
    # with no app and a confusing "couldn't find a built desktop" at launch.
    # Always re-resolve Node here. Stages run in separate processes, so we can't
    # trust an earlier check; more importantly check_node now enforces the build
    # floor (^20.19 || >=22.12) and prepends the Hermes-managed Node to PATH, so
    # the build never runs on a too-old system Node — the cause of the opaque
    # "Build desktop app … exit code 1" failure (Vite crashes on old Node).
    check_node
    if ! command -v npm >/dev/null 2>&1; then
        log_error "Cannot build desktop app: Node.js / npm unavailable"
        log_info "Install Node.js and retry: cd $desktop_dir && npm run pack"
        return 1
    fi
    if [ ! -f "$desktop_dir/package.json" ]; then
        log_warn "Skipping desktop build (apps/desktop not present in checkout)"
        return 0
    fi

    log_info "Ensuring desktop German locale wiring..."
    if ensure_desktop_german_locale "$desktop_dir"; then
        log_success "Desktop German locale wiring ready"
    else
        log_warn "Desktop German locale patch failed; continuing with upstream desktop sources"
    fi

    log_info "Ensuring desktop Hub / Discover sidebar section..."
    if ensure_desktop_hub_sidebar "$desktop_dir"; then
        log_success "Desktop Hub sidebar section ready"
    else
        log_warn "Desktop Hub sidebar patch failed; continuing without Discover section"
    fi

    # 1. Root workspace install so apps/desktop's deps (Electron, Vite,
    #    node-pty prebuilds) resolve. The browser-tools install runs in the
    #    repo-root package workspace, which does not pull apps/* deps.
    #
    #    Prefer `npm ci`: it deletes node_modules and reinstalls from the
    #    lockfile, so it always produces a complete tree. Bare `npm install`
    #    can report "up to date" against a stale node_modules/.package-lock.json
    #    marker while node_modules is actually empty (Windows workspace-hoisting
    #    flake) — leaving tsc/typescript unresolved and `npm run pack`'s
    #    `tsc -b` failing with no obvious cause. Fall back to `npm install`
    #    only if `npm ci` is unavailable or the lockfile is out of sync.
    log_info "Installing desktop workspace dependencies (includes Electron ~150MB, 1-3min)..."
    ( cd "$INSTALL_DIR" && npm ci ) || ( cd "$INSTALL_DIR" && npm install ) || {
        log_error "Desktop workspace npm install failed"
        # Common cause: a previous 'sudo npm'/'sudo npx' left root-owned files in
        # ~/.npm, so this non-root install can't write the shared cache. npm hides
        # it behind a confusing EEXIST / "File exists" message while the real errno
        # is EACCES (-13). Point the user at the fix instead of a raw npm trace.
        log_info "If the errors above mention EACCES / 'permission denied' / EEXIST while"
        log_info "writing the npm cache, your ~/.npm likely holds root-owned files from an"
        log_info "earlier 'sudo npm' or 'sudo npx'. Reclaim ownership and retry:"
        log_info "  sudo chown -R \"\$(id -un)\" ~/.npm && npm cache verify"
        log_info "Then re-run this installer, or build manually:"
        log_info "  cd \"$INSTALL_DIR\" && npm ci && cd apps/desktop && npm run pack"
        return 1
    }
    log_success "Desktop workspace dependencies installed"

    # npm can occasionally resolve a mismatched @assistant-ui/store/@assistant-ui/tap
    # pair in fresh runtime clones, which breaks Vite with:
    # "./react-shim is not exported ... from @assistant-ui/tap".
    # Workspace overrides (root package.json) already handle this, so skip if in workspace context.
    # For standalone installs without workspace overrides, attempt the manual pin.
    log_info "Pinning desktop UI compatibility deps (@assistant-ui/store 0.2.9, @assistant-ui/tap 0.5.10)..."
    if ( cd "$INSTALL_DIR" && npm install --no-save @assistant-ui/store@0.2.9 @assistant-ui/tap@0.5.10 ) 2>&1 | grep -q "conflicts with direct dependency"; then
        log_warn "Desktop UI deps already pinned via workspace overrides (normal in workspace install)"
    elif ! ( cd "$INSTALL_DIR" && npm install --no-save @assistant-ui/store@0.2.9 @assistant-ui/tap@0.5.10 ); then
        log_warn "Could not apply manual pin; proceeding with workspace overrides or installed versions"
    else
        log_success "Desktop UI compatibility deps pinned"
    fi

    # 2. Build, with up to three escalating attempts so a transient/blocked
    #    Electron download self-heals instead of failing the whole install:
    #      a) plain `npm run pack` (downloads Electron from GitHub),
    #      b) on failure, purge a corrupt cached zip + stale unpacked dir and
    #         retry (matches install.ps1 / `hermes desktop`),
    #      c) on still-failing, fall back to a public Electron mirror — this is
    #         the GitHub-blocked/throttled case (the repeating "retrying" log).
    log_info "Building desktop app (this takes 1-3 minutes)..."
    local pack_ok=false
    if _desktop_pack "$desktop_dir"; then
        pack_ok=true
    else
        # (b) Corrupt cached Electron zip is the most common self-healable cause.
        local purged
        purged="$(clear_electron_build_cache "$desktop_dir")"
        if [ -n "$purged" ]; then
            log_warn "Desktop build failed; cleared cached Electron download and retrying once..."
            if _desktop_pack "$desktop_dir"; then
                pack_ok=true
            fi
        fi
    fi

    # (c) Still failing and the user hasn't pinned their own mirror: the GitHub
    #     release host is likely blocked/throttled. Retry once via a public
    #     Electron mirror (@electron/get still SHASUM-verifies the download).
    if [ "$pack_ok" = false ] && [ -z "${ELECTRON_MIRROR:-}" ]; then
        log_warn "Desktop build still failing — the Electron download from GitHub looks blocked."
        log_warn "Retrying once via a public Electron mirror ($DESKTOP_ELECTRON_FALLBACK_MIRROR)..."
        log_warn "  (set ELECTRON_MIRROR yourself to use a different/trusted mirror)"
        if _desktop_pack "$desktop_dir" "$DESKTOP_ELECTRON_FALLBACK_MIRROR"; then
            pack_ok=true
        fi
    fi

    if [ "$pack_ok" = false ]; then
        log_error "Desktop app build failed"
        # If the log shows repeated "retrying" lines fetching the Electron zip,
        # the binary download is blocked/throttled (firewall, proxy, region) and
        # the mirror fallback above also couldn't reach a host. Try a mirror you
        # trust and rebuild (@electron/get honors ELECTRON_MIRROR):
        log_info "If the log shows Electron download retries, rebuild via a reachable mirror:"
        log_info "  ELECTRON_MIRROR=<mirror-base-url> \\"
        log_info "    bash -c 'cd \"$desktop_dir\" && CSC_IDENTITY_AUTO_DISCOVERY=false npm run pack'"
        log_info "Otherwise build manually: cd $desktop_dir && npm run pack"
        return 1
    fi

    local app=""
    if [ "$OS" = "linux" ]; then
        if [ -x "$desktop_dir/release/linux-unpacked/Hermes" ]; then
            app="$desktop_dir/release/linux-unpacked/Hermes"
        elif [ -x "$desktop_dir/release/linux-unpacked/hermes" ]; then
            app="$desktop_dir/release/linux-unpacked/hermes"
        fi
    else
        local cand
        for cand in \
            "$desktop_dir/release/mac-arm64/Hermes.app" \
            "$desktop_dir/release/mac/Hermes.app"; do
            if [ -d "$cand" ]; then
                app="$cand"
                break
            fi
        done
    fi
    if [ -z "$app" ]; then
        log_error "Desktop build completed but no app was found under $desktop_dir/release/"
        return 1
    fi
    log_success "Desktop app built: $app"

    # Linux: Electron's chrome-sandbox helper needs root:root 4755 or the
    # sandboxed renderer will abort on startup.  Check the file is a regular
    # file (not a symlink) before chown/chmod so we don't follow an
    # attacker-controlled link to an arbitrary path.
    if [ "$OS" = "linux" ]; then
        local sandbox="$desktop_dir/release/linux-unpacked/chrome-sandbox"
        if [ -f "$sandbox" ] && [ ! -L "$sandbox" ]; then
            if [ "$(id -u)" -eq 0 ]; then
                chown root:root "$sandbox" && chmod 4755 "$sandbox" || {
                    log_error "Cannot configure Electron sandbox helper: $sandbox"
                    return 1
                }
            elif command -v sudo >/dev/null 2>&1; then
                sudo chown root:root "$sandbox" && sudo chmod 4755 "$sandbox" || {
                    log_error "Cannot configure Electron sandbox helper (sudo failed): $sandbox"
                    return 1
                }
            else
                log_error "Cannot configure Electron sandbox helper without sudo: $sandbox"
                return 1
            fi
        fi
    fi

    # macOS: make the locally-built (ad-hoc) app relaunchable after an in-place
    # self-update. An ad-hoc bundle has no stable Designated Requirement, so a
    # later in-place rebuild (new cdhash) plus the inherited quarantine flag
    # trips Gatekeeper's tamper check ("Hermes is damaged and can't be opened").
    # Strip quarantine + re-apply a clean deep ad-hoc signature (no
    # hardened-runtime flag, which an ad-hoc build can't satisfy). Skipped when a
    # real signing identity is configured so a signed build isn't clobbered.
    if [ "$OS" = "macos" ] && [ -z "${CSC_LINK:-}" ] && [ -z "${APPLE_SIGNING_IDENTITY:-}" ] && command -v codesign >/dev/null 2>&1; then
        xattr -cr "$app" 2>/dev/null || true
        codesign --force --deep --sign - "$app" >/dev/null 2>&1 || true
    fi

    # `npm install` + `npm run pack` rewrite lockfiles; restore them so the
    # checkout stays clean for the next `hermes update`.
    restore_dirty_lockfiles "$INSTALL_DIR"
}

# Each --stage runs in its own process, so (unlike the monolithic main() where
# clone_repo cd's once and later steps inherit it) a stage that operates on the
# checkout must cd into it explicitly. Without this, install_deps/setup_path run
# from the desktop app's cwd and resolve `.` / the venv against the wrong tree.
require_install_dir() {
    if [ -z "$INSTALL_DIR" ] || [ ! -d "$INSTALL_DIR" ]; then
        log_error "Install directory not found: ${INSTALL_DIR:-<unset>}"
        log_info "The 'repository' stage must run before this one."
        return 1
    fi
    cd "$INSTALL_DIR"
}

# Desktop bootstrap stage protocol. Mirrors the Windows install.ps1 surface
# closely enough for the Electron bootstrap runner to show structured progress.
run_stage_body() {
    local stage="$1"

    case "$stage" in
        prerequisites)
            print_banner
            detect_os
            resolve_install_layout
            install_uv
            check_python
            check_git
            check_node
            check_network_prerequisites
            install_system_packages
            ;;
        repository)
            detect_os
            resolve_install_layout
            check_git
            clone_repo
            ensure_litellm_hub_gateway "$INSTALL_DIR"
            backfill_skill_dependencies_on_repo_sync
            ;;
        venv)
            detect_os
            resolve_install_layout
            require_install_dir
            install_uv
            check_python
            setup_venv
            ;;
        python-deps)
            detect_os
            resolve_install_layout
            require_install_dir
            install_uv
            check_python
            install_deps
            ;;
        node-deps)
            detect_os
            resolve_install_layout
            require_install_dir
            check_node
            install_node_deps
            ;;
        path)
            detect_os
            resolve_install_layout
            require_install_dir
            setup_path
            ;;
        config)
            detect_os
            resolve_install_layout
            require_install_dir
            copy_config_templates
            ;;
        mcp-check)
            detect_os
            resolve_install_layout
            require_install_dir
            check_configured_mcp_servers
            ;;
        mcp-reload)
            detect_os
            resolve_install_layout
            require_install_dir
            reload_configured_mcp_servers
            ;;
        setup)
            detect_os
            resolve_install_layout
            require_install_dir
            run_setup_wizard
            ;;
        gateway)
            detect_os
            resolve_install_layout
            require_install_dir
            maybe_start_gateway
            ;;
        desktop)
            detect_os
            resolve_install_layout
            require_install_dir
            # Each stage runs in its own process, so the Hermes-managed Node
            # provisioned during prerequisites/node-deps (at $HERMES_HOME/node/bin)
            # isn't on PATH here. check_node re-adds it (or installs if missing)
            # so install_desktop can find npm instead of silently skipping.
            check_node
            install_desktop
            ;;
        complete)
            detect_os
            resolve_install_layout
            print_success
            echo "git" > "$HERMES_HOME/.install_method"
            ;;
        *)
            log_error "Unknown stage: $stage"
            return 2
            ;;
    esac
}

run_stage_protocol() {
    local stage="$1"
    if [ -z "$stage" ]; then
        log_error "--stage requires a stage name"
        if [ "$JSON_OUTPUT" = true ]; then
            emit_stage_json "" false false "missing stage name"
        fi
        return 2
    fi

    if [ "$NON_INTERACTIVE" = true ] && stage_needs_user_input "$stage"; then
        log_info "Skipping $stage (non-interactive bootstrap)"
        if [ "$JSON_OUTPUT" = true ]; then
            emit_stage_json "$stage" true true
        fi
        return 0
    fi

    # Run the stage body in a subshell so a stage helper that calls `exit 1`
    # on failure (clone_repo, install_deps, etc. were written for the monolithic
    # flow) only exits the subshell — the parent still reaches the JSON result
    # frame below. Without this, a failed --stage would terminate the process
    # before emitting the frame and the Rust/Electron parser would see "no
    # result frame" instead of a clean {ok:false} contract response.
    set +e
    ( run_stage_body "$stage" )
    local code=$?
    set -e

    if [ "$JSON_OUTPUT" = true ]; then
        if [ "$code" -eq 0 ]; then
            emit_stage_json "$stage" true false
        else
            emit_stage_json "$stage" false false "exit code $code"
        fi
    fi
    return "$code"
}

# ============================================================================
# Main
# ============================================================================

main() {
    print_banner

    detect_os
    resolve_install_layout
    install_uv
    check_python
    check_git
    check_node
    check_network_prerequisites
    install_system_packages

    clone_repo
    ensure_litellm_hub_gateway "$INSTALL_DIR"
    setup_venv
    install_deps
    install_node_deps
    setup_path
    copy_config_templates
    run_setup_wizard
    maybe_start_gateway

    if [ "$INCLUDE_DESKTOP" = true ]; then
        install_desktop
    fi

    print_success

    echo "git" > "$HERMES_HOME/.install_method"
}

if [ "$MANIFEST_MODE" = true ]; then
    emit_manifest
elif [ -n "$STAGE_NAME" ]; then
    run_stage_protocol "$STAGE_NAME"
elif [ -n "$ENSURE_DEPS" ]; then
    ensure_mode
elif [ "$POSTINSTALL_MODE" = true ]; then
    postinstall_mode
else
    main
fi
