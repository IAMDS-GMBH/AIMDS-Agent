"""Shared fixtures for the hermes-agent test suite.

Hermetic-test invariants enforced here (see AGENTS.md for rationale):

1. **No credential env vars.** All provider/credential-shaped env vars
   (ending in _API_KEY, _TOKEN, _SECRET, _PASSWORD, _CREDENTIALS, etc.)
   are unset before every test. Local developer keys cannot leak in.
2. **Isolated HERMES_HOME.** HERMES_HOME points to a per-test tempdir so
   code reading ``~/.hermes/*`` via ``get_hermes_home()`` can't see the
   real one. (We do NOT also redirect HOME — that broke subprocesses in
   CI. Code using ``Path.home() / ".hermes"`` instead of the canonical
   ``get_hermes_home()`` is a bug to fix at the callsite.)
3. **Deterministic runtime.** TZ=UTC, LANG=C.UTF-8, PYTHONHASHSEED=0.
4. **No HERMES_SESSION_* inheritance** — the agent's current gateway
   session must not leak into tests.
5. **No network, no browser, no developer logins** (AIS-487). Non-loopback
   DNS/connects behave like an offline machine and fail the test that tried;
   ``webbrowser`` / ``open``-style launches are refused; the developer's gh,
   Codex, Claude Code and Microsoft 365 sign-ins are hidden. See "Network
   guard" below; ``@pytest.mark.network`` + ``HERMES_LIVE_TESTS=1`` for the
   rare live test.

These invariants make the local test run match CI closely. Gaps that
remain (CPU count, xdist worker count) are addressed by the canonical
test runner at ``scripts/run_tests.sh``.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Per-file process isolation ──────────────────────────────────────────────
# Tests run via ``scripts/run_tests_parallel.py``, which spawns a fresh
# ``python -m pytest <file>`` subprocess per test file. Cross-file state
# leakage (module-level dicts, ContextVars, caches) is impossible: each
# file gets a clean Python interpreter. Intra-file ordering is the test
# author's responsibility — if test A in foo.py mutates state that test B
# in foo.py reads, that's a real bug to fix in the file (it would also
# bite anyone running ``pytest tests/foo.py`` directly).
#
# This replaces the historic _reset_module_state autouse fixture (manual
# state clearing) and the brief experiment with subprocess-per-test
# isolation (too slow at ~17k tests).
#
# See ``scripts/run_tests_parallel.py`` for the runner.


# ── Credential env-var filter ──────────────────────────────────────────────
#
# Any env var in the current process matching ONE of these patterns is
# unset for every test. Developers' local keys cannot leak into assertions
# about "auto-detect provider when key present".

_CREDENTIAL_SUFFIXES = (
    "_API_KEY",
    "_TOKEN",
    "_SECRET",
    "_PASSWORD",
    "_CREDENTIALS",
    "_ACCESS_KEY",
    "_SECRET_ACCESS_KEY",
    "_PRIVATE_KEY",
    "_OAUTH_TOKEN",
    "_WEBHOOK_SECRET",
    "_ENCRYPT_KEY",
    "_APP_SECRET",
    "_CLIENT_SECRET",
    "_CORP_SECRET",
    "_AES_KEY",
)

# Explicit names (for ones that don't fit the suffix pattern)
_CREDENTIAL_NAMES = frozenset({
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "ANTHROPIC_TOKEN",
    "FAL_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "NOUS_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "GROQ_API_KEY",
    "XAI_API_KEY",
    "MISTRAL_API_KEY",
    "DEEPSEEK_API_KEY",
    "KIMI_API_KEY",
    "MOONSHOT_API_KEY",
    "GLM_API_KEY",
    "ZAI_API_KEY",
    "MINIMAX_API_KEY",
    "OLLAMA_API_KEY",
    "OPENVIKING_API_KEY",
    "COPILOT_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "BROWSERBASE_API_KEY",
    "FIRECRAWL_API_KEY",
    "PARALLEL_API_KEY",
    "EXA_API_KEY",
    "TAVILY_API_KEY",
    "WANDB_API_KEY",
    "ELEVENLABS_API_KEY",
    "HONCHO_API_KEY",
    "MEM0_API_KEY",
    "SUPERMEMORY_API_KEY",
    "RETAINDB_API_KEY",
    "HINDSIGHT_API_KEY",
    "HINDSIGHT_LLM_API_KEY",
    "DAYTONA_API_KEY",
    "TWILIO_AUTH_TOKEN",
    "TELEGRAM_BOT_TOKEN",
    "DISCORD_BOT_TOKEN",
    "SLACK_BOT_TOKEN",
    "SLACK_APP_TOKEN",
    "MATTERMOST_TOKEN",
    "MATRIX_ACCESS_TOKEN",
    "MATRIX_PASSWORD",
    "MATRIX_RECOVERY_KEY",
    "HASS_TOKEN",
    "EMAIL_PASSWORD",
    "BLUEBUBBLES_PASSWORD",
    "FEISHU_APP_SECRET",
    "FEISHU_ENCRYPT_KEY",
    "FEISHU_VERIFICATION_TOKEN",
    "DINGTALK_CLIENT_SECRET",
    "QQ_CLIENT_SECRET",
    "QQ_STT_API_KEY",
    "WECOM_SECRET",
    "WECOM_CALLBACK_CORP_SECRET",
    "WECOM_CALLBACK_TOKEN",
    "WECOM_CALLBACK_ENCODING_AES_KEY",
    "WEIXIN_TOKEN",
    "MODAL_TOKEN_ID",
    "MODAL_TOKEN_SECRET",
    "TERMINAL_SSH_KEY",
    "SUDO_PASSWORD",
    "GATEWAY_PROXY_KEY",
    "API_SERVER_KEY",
    "TOOL_GATEWAY_USER_TOKEN",
    "TELEGRAM_WEBHOOK_SECRET",
    "WEBHOOK_SECRET",
    "VOICE_TOOLS_OPENAI_KEY",
    "BROWSER_USE_API_KEY",
    "CUSTOM_API_KEY",
    "GATEWAY_PROXY_URL",
    "GEMINI_BASE_URL",
    "OPENAI_BASE_URL",
    "OPENROUTER_BASE_URL",
    "OLLAMA_BASE_URL",
    "GROQ_BASE_URL",
    "XAI_BASE_URL",
    "ANTHROPIC_BASE_URL",
})


def _looks_like_credential(name: str) -> bool:
    """True if env var name matches a credential-shaped pattern."""
    if name in _CREDENTIAL_NAMES:
        return True
    return any(name.endswith(suf) for suf in _CREDENTIAL_SUFFIXES)


# HERMES_* vars that change test behavior by being set. Unset all of these
# unconditionally — individual tests that need them set do so explicitly.
_HERMES_BEHAVIORAL_VARS = frozenset({
    "HERMES_YOLO_MODE",
    "HERMES_INTERACTIVE",
    "HERMES_QUIET",
    "HERMES_TOOL_PROGRESS",
    "HERMES_TOOL_PROGRESS_MODE",
    "HERMES_MAX_ITERATIONS",
    "HERMES_SESSION_PLATFORM",
    "HERMES_SESSION_CHAT_ID",
    "HERMES_SESSION_CHAT_NAME",
    "HERMES_SESSION_THREAD_ID",
    "HERMES_SESSION_SOURCE",
    "HERMES_SESSION_KEY",
    "HERMES_GATEWAY_SESSION",
    "HERMES_CRON_SESSION",
    "_HERMES_GATEWAY",
    "HERMES_PLATFORM",
    "HERMES_MODEL",
    "HERMES_INFERENCE_MODEL",
    "HERMES_INFERENCE_PROVIDER",
    "HERMES_TUI_PROVIDER",
    "HERMES_MANAGED",
    "HERMES_DEV",
    "HERMES_CONTAINER",
    "HERMES_EPHEMERAL_SYSTEM_PROMPT",
    "HERMES_TIMEZONE",
    "HERMES_REDACT_SECRETS",
    "HERMES_BACKGROUND_NOTIFICATIONS",
    "HERMES_EXEC_ASK",
    "HERMES_HOME_MODE",
    "HERMES_AGENT_USE_LEGACY_SESSION_KEYS",
    # Kanban path/board pins must never leak from a developer shell or
    # dispatched worker into tests; otherwise tests can write fake tasks to
    # the real ~/.hermes/kanban.db instead of the per-test HERMES_HOME.
    "HERMES_KANBAN_DB",
    "HERMES_KANBAN_BOARD",
    "HERMES_KANBAN_HOME",
    "HERMES_KANBAN_WORKSPACES_ROOT",
    "HERMES_KANBAN_LOGS_ROOT",
    "HERMES_KANBAN_TASK",
    "HERMES_KANBAN_WORKSPACE",
    "HERMES_KANBAN_RUN_ID",
    "HERMES_KANBAN_CLAIM_LOCK",
    "HERMES_KANBAN_DISPATCH_IN_GATEWAY",
    "HERMES_TENANT",
    # Dashboard OAuth auth gate (PR #30156). When set, the bundled
    # dashboard-auth `nous` plugin auto-registers itself on plugin discovery,
    # which is triggered by any `/api/status` call. That leaks a provider
    # into the dashboard_auth registry across tests in the same worker and
    # makes assertions like `auth_providers == []` flaky. CI never sets
    # these, so production tests must not see them either.
    "HERMES_DASHBOARD_OAUTH_CLIENT_ID",
    "HERMES_DASHBOARD_PORTAL_URL",
    "TERMINAL_CWD",
    "TERMINAL_ENV",
    "TERMINAL_CONTAINER_CPU",
    "TERMINAL_CONTAINER_DISK",
    "TERMINAL_CONTAINER_MEMORY",
    "TERMINAL_CONTAINER_PERSISTENT",
    "TERMINAL_DOCKER_PERSIST_ACROSS_PROCESSES",
    "TERMINAL_DOCKER_ORPHAN_REAPER",
    "TERMINAL_DOCKER_RUN_AS_HOST_USER",
    "BROWSER_CDP_URL",
    "CAMOFOX_URL",
    # Platform allowlists — not credentials, but if set from any source
    # (user shell, earlier leaky test, CI env), they change gateway auth
    # behavior and flake button-authorization tests.
    "TELEGRAM_ALLOWED_USERS",
    "DISCORD_ALLOWED_USERS",
    "WHATSAPP_ALLOWED_USERS",
    "SLACK_ALLOWED_USERS",
    "SIGNAL_ALLOWED_USERS",
    "SIGNAL_GROUP_ALLOWED_USERS",
    "EMAIL_ALLOWED_USERS",
    "SMS_ALLOWED_USERS",
    "MATTERMOST_ALLOWED_USERS",
    "MATRIX_ALLOWED_USERS",
    "DINGTALK_ALLOWED_USERS",
    "FEISHU_ALLOWED_USERS",
    "WECOM_ALLOWED_USERS",
    "GATEWAY_ALLOWED_USERS",
    "GATEWAY_ALLOW_ALL_USERS",
    "TELEGRAM_ALLOW_ALL_USERS",
    "DISCORD_ALLOW_ALL_USERS",
    "WHATSAPP_ALLOW_ALL_USERS",
    "SLACK_ALLOW_ALL_USERS",
    "SIGNAL_ALLOW_ALL_USERS",
    "EMAIL_ALLOW_ALL_USERS",
    "SMS_ALLOW_ALL_USERS",
    # Gateway home channels are set by /sethome in real profiles. Tests that
    # exercise dashboard notification toggles must opt in explicitly or they
    # can accidentally subscribe against a developer's real home channel.
    "TELEGRAM_HOME_CHANNEL",
    "TELEGRAM_HOME_CHANNEL_THREAD_ID",
    "TELEGRAM_HOME_CHANNEL_NAME",
    "TELEGRAM_CRON_THREAD_ID",
    "DISCORD_HOME_CHANNEL",
    "DISCORD_HOME_CHANNEL_THREAD_ID",
    "DISCORD_HOME_CHANNEL_NAME",
    "SLACK_HOME_CHANNEL",
    "SLACK_HOME_CHANNEL_THREAD_ID",
    "SLACK_HOME_CHANNEL_NAME",
    "WHATSAPP_HOME_CHANNEL",
    "WHATSAPP_HOME_CHANNEL_THREAD_ID",
    "WHATSAPP_HOME_CHANNEL_NAME",
    "SIGNAL_HOME_CHANNEL",
    "SIGNAL_HOME_CHANNEL_THREAD_ID",
    "SIGNAL_HOME_CHANNEL_NAME",
    "EMAIL_HOME_CHANNEL",
    "EMAIL_HOME_CHANNEL_THREAD_ID",
    "EMAIL_HOME_CHANNEL_NAME",
    "SMS_HOME_CHANNEL",
    "SMS_HOME_CHANNEL_THREAD_ID",
    "SMS_HOME_CHANNEL_NAME",
    "MATTERMOST_HOME_CHANNEL",
    "MATTERMOST_HOME_CHANNEL_THREAD_ID",
    "MATTERMOST_HOME_CHANNEL_NAME",
    "MATRIX_HOME_CHANNEL",
    "MATRIX_HOME_CHANNEL_THREAD_ID",
    "MATRIX_HOME_CHANNEL_NAME",
    "DINGTALK_HOME_CHANNEL",
    "DINGTALK_HOME_CHANNEL_THREAD_ID",
    "DINGTALK_HOME_CHANNEL_NAME",
    "FEISHU_HOME_CHANNEL",
    "FEISHU_HOME_CHANNEL_THREAD_ID",
    "FEISHU_HOME_CHANNEL_NAME",
    "WECOM_HOME_CHANNEL",
    "WECOM_HOME_CHANNEL_THREAD_ID",
    "WECOM_HOME_CHANNEL_NAME",
    # API server bind/auth settings are common in local gateway profiles and
    # change adapter defaults plus load_gateway_config() enablement. Tests that
    # need them set opt in explicitly with monkeypatch.
    "API_SERVER_ENABLED",
    "API_SERVER_HOST",
    "API_SERVER_PORT",
    "API_SERVER_KEY",
    "API_SERVER_CORS_ORIGINS",
    "API_SERVER_MODEL_NAME",
    # Platform gating — set by load_gateway_config() as a side effect when
    # a config.yaml is present, so individual test bodies that call the
    # loader leak these values into later tests in the same process.
    # Force-clear on every test setup so the leak can't happen.
    "SLACK_REQUIRE_MENTION",
    "SLACK_STRICT_MENTION",
    "SLACK_FREE_RESPONSE_CHANNELS",
    "SLACK_ALLOW_BOTS",
    "SLACK_REACTIONS",
    "DISCORD_REQUIRE_MENTION",
    "DISCORD_FREE_RESPONSE_CHANNELS",
    "TELEGRAM_REQUIRE_MENTION",
    "WHATSAPP_REQUIRE_MENTION",
    "DINGTALK_REQUIRE_MENTION",
    "MATRIX_REQUIRE_MENTION",
})


# ``gh`` default host for tests: nobody is logged in there (see 4c below).
_NO_GH_LOGIN_HOST = "hermes-tests.invalid"


@pytest.fixture(autouse=True)
def _hermetic_environment(tmp_path, monkeypatch):
    """Blank out all credential/behavioral env vars so local and CI match.

    Also redirects HOME and HERMES_HOME to per-test tempdirs so code that
    reads ``~/.hermes/*`` can't touch the real one, and pins TZ/LANG so
    datetime/locale-sensitive tests are deterministic.
    """
    # 1. Blank every credential-shaped env var that's currently set.
    for name in list(os.environ.keys()):
        if _looks_like_credential(name):
            monkeypatch.delenv(name, raising=False)

    # 2. Blank behavioral HERMES_* vars that could change test semantics.
    for name in _HERMES_BEHAVIORAL_VARS:
        monkeypatch.delenv(name, raising=False)

    # 3. Redirect HERMES_HOME to a per-test tempdir. Code that reads
    #    ``~/.hermes/*`` via ``get_hermes_home()`` now gets the tempdir.
    #
    #    NOTE: We do NOT also redirect HOME. Doing so broke CI because
    #    some tests (and their transitive deps) spawn subprocesses that
    #    inherit HOME and expect it to be stable. If a test genuinely
    #    needs HOME isolated, it should set it explicitly in its own
    #    fixture. Any code in the codebase reading ``~/.hermes/*`` via
    #    ``Path.home() / ".hermes"`` instead of ``get_hermes_home()``
    #    is a bug to fix at the callsite.
    fake_hermes_home = tmp_path / "hermes_test"
    fake_hermes_home.mkdir()
    (fake_hermes_home / "sessions").mkdir()
    (fake_hermes_home / "cron").mkdir()
    (fake_hermes_home / "memories").mkdir()
    (fake_hermes_home / "skills").mkdir()
    monkeypatch.setenv("HERMES_HOME", str(fake_hermes_home))

    # 3b. Redirect HERMES_DOCUMENTS_DIR to a per-test tempdir. Code that
    #     reads ``~/Documents`` (workspace/vault folder default, the
    #     HermesMemory symlink maintenance in _ensure_documents_memory_link)
    #     now gets the tempdir instead of the real user's Documents folder.
    #     Without this, running the test suite silently pollutes the real
    #     ~/Documents with backup symlinks/directories -- confirmed to have
    #     happened to a real user's machine.
    fake_documents_dir = tmp_path / "documents_test"
    fake_documents_dir.mkdir()
    monkeypatch.setenv("HERMES_DOCUMENTS_DIR", str(fake_documents_dir))

    # 4. Deterministic locale / timezone / hashseed. CI runs in UTC with
    #    C.UTF-8 locale; local dev often doesn't. Pin everything.
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("LANG", "C.UTF-8")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    monkeypatch.setenv("PYTHONHASHSEED", "0")

    # 4b. Disable AWS IMDS lookups. Without this, any test that ends up
    #     calling has_aws_credentials() / resolve_aws_auth_env_var()
    #     (e.g. provider auto-detect, status command, cron run_job) burns
    #     ~2s waiting for the metadata service at 169.254.169.254 to time
    #     out. Tests don't run on EC2 — IMDS is always unreachable here.
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_METADATA_SERVICE_TIMEOUT", "1")
    monkeypatch.setenv("AWS_METADATA_SERVICE_NUM_ATTEMPTS", "1")
    # Tirith auto-installs from GitHub when enabled and missing. Unit tests
    # should never perform that implicit network/bootstrap path; Tirith-specific
    # tests opt back in by patching the security config directly.
    monkeypatch.setenv("TIRITH_ENABLED", "false")
    # 4c. Hide the developer's GitHub CLI login. ``gh auth token`` would
    #     otherwise hand the real token to the Copilot credential resolver,
    #     which then exchanges it at api.github.com (AIS-487). An empty config
    #     dir is not enough on macOS (gh still answers from the keyring for
    #     github.com); a default host nobody is logged into is.
    fake_gh_config = tmp_path / "gh_config_test"
    fake_gh_config.mkdir()
    monkeypatch.setenv("GH_CONFIG_DIR", str(fake_gh_config))
    monkeypatch.setenv("GH_HOST", _NO_GH_LOGIN_HOST)
    # 4d. The pre-AIS-418 M365 token-cache migration copies
    #     ``~/.hermes/m365_token_cache.bin`` into an empty HERMES_HOME. HOME is
    #     not redirected (see 3.), so without this every test would import the
    #     developer's real Microsoft 365 sign-in — and MSAL would then call
    #     login.microsoftonline.com from the system-prompt builder.
    try:
        import hermes_cli.m365_auth as _m365_auth
        monkeypatch.setattr(
            _m365_auth,
            "_legacy_m365_token_cache_path",
            lambda: tmp_path / "no_legacy_home" / "m365_token_cache.bin",
        )
    except Exception:
        pass
    # 4e. Same for Claude Code's sign-in: ``~/.claude/.credentials.json``
    #     (read for Anthropic auto-detection — and *written* on token
    #     refresh) and the macOS Keychain entry (refused by the subprocess
    #     guard in ``_live_system_guard``). Tests that fake a home by
    #     patching ``Path.home`` still reach their own file: only the
    #     developer's real home is swapped for an empty directory.
    _real_home = Path.home()
    try:
        import agent.anthropic_adapter as _anthropic_adapter

        def _hermetic_claude_credentials_path():
            home = Path.home()
            if home == _real_home:
                home = tmp_path / "no_claude_home"
            return home / ".claude" / ".credentials.json"

        monkeypatch.setattr(
            _anthropic_adapter, "_claude_code_credentials_path",
            _hermetic_claude_credentials_path,
        )
    except Exception:
        pass
    # 4f. Codex CLI credentials (``~/.codex/auth.json``) — same reason.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex_home_test"))

    # 5. Reset plugin singleton so tests don't leak plugins from
    #    ~/.hermes/plugins/ (which, per step 3, is now empty — but the
    #    singleton might still be cached from a previous test).
    try:
        import hermes_cli.plugins as _plugins_mod
        monkeypatch.setattr(_plugins_mod, "_plugin_manager", None)
    except Exception:
        pass
    # Explicitly clear provider-specific base URL overrides that don't match
    # the generic credential-shaped env-var filter above.
    monkeypatch.delenv("GMI_API_KEY", raising=False)
    monkeypatch.delenv("GMI_BASE_URL", raising=False)


# ── Remote catalog / metadata fetchers: offline by default (AIS-487) ─────────
#
# Building an agent, a compressor, a model picker or an auxiliary client asks
# the network for model metadata: the models.dev registry, the OpenRouter
# catalog, ``<base_url>/models`` and ``/api/show`` probes, the Codex /models
# list, the Copilot token exchange. Production treats every one of them as
# optional (offline → cache / hardcoded defaults), so tests get exactly that
# offline answer here instead of a blocked connection per test.
#
# Tests of a fetcher itself (with their own transport mocks) opt out with
# ``@pytest.mark.real_fetchers``; a test that wants specific catalog data
# patches the fetcher itself, which overrides these stubs.


def _offline_copilot_exchange(*_args, **_kwargs):
    raise ConnectionError("offline (tests/conftest.py): Copilot token exchange stubbed")


def _offline_probe_api_models(api_key, base_url, timeout=5.0, api_mode=None):
    """``probe_api_models`` when ``<base_url>/models`` is unreachable."""
    models = sys.modules["hermes_cli.models"]
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized or models._is_github_models_base_url(normalized):
        # No probe / Copilot catalog path (its fetcher is stubbed below).
        return _offline_probe_api_models.real(api_key, base_url, timeout=timeout, api_mode=api_mode)
    alternate = normalized[:-3].rstrip("/") if normalized.endswith("/v1") else normalized + "/v1"
    return {
        "models": None,
        "probed_url": normalized + "/models",
        "resolved_base_url": normalized,
        "suggested_base_url": alternate if alternate != normalized else None,
        "used_fallback": False,
    }


def _offline_openrouter_models(*_args, force_refresh=False, **_kwargs):
    """``fetch_openrouter_models`` offline: cached list, else the in-repo snapshot."""
    models = sys.modules["hermes_cli.models"]
    if models._openrouter_catalog_cache is not None and not force_refresh:
        return list(models._openrouter_catalog_cache)
    return list(models.OPENROUTER_MODELS)


_OFFLINE_FETCHERS = (
    # (module, attribute, offline stand-in) — the defining modules
    ("agent.models_dev", "fetch_models_dev", lambda *a, **k: {}),
    ("agent.model_metadata", "fetch_model_metadata", lambda *a, **k: {}),
    ("agent.model_metadata", "fetch_endpoint_model_metadata", lambda *a, **k: {}),
    ("agent.model_metadata", "_query_ollama_api_show", lambda *a, **k: None),
    ("agent.model_metadata", "_fetch_codex_oauth_context_lengths", lambda *a, **k: {}),
    ("agent.model_metadata", "_query_anthropic_context_length", lambda *a, **k: None),
    ("hermes_cli.copilot_auth", "exchange_copilot_token", _offline_copilot_exchange),
    ("hermes_cli.models", "fetch_github_model_catalog", lambda *a, **k: None),
    ("hermes_cli.models", "_fetch_anthropic_models", lambda *a, **k: None),
    ("hermes_cli.models", "fetch_models_with_pricing", lambda *a, **k: {}),
    ("hermes_cli.models", "fetch_nous_recommended_models", lambda *a, **k: {}),
    ("hermes_cli.models", "probe_api_models", _offline_probe_api_models),
    ("hermes_cli.models", "fetch_openrouter_models", _offline_openrouter_models),
    ("hermes_cli.model_catalog", "_fetch_manifest", lambda *a, **k: None),
    ("hermes_cli.codex_models", "_fetch_models_from_api", lambda *a, **k: []),
    # OSV malware lookup before an npx/uvx MCP server starts: offline → allow.
    ("tools.osv_check", "_query_osv", lambda *a, **k: []),
)
# Module-level ``from … import`` copies of the fetchers above. Patched when the
# module is already loaded; a later import copies the (stubbed) original.
_OFFLINE_FETCHER_COPIES = (
    ("agent.usage_pricing", "fetch_endpoint_model_metadata"),
    ("agent.usage_pricing", "fetch_model_metadata"),
    ("agent.agent_init", "fetch_model_metadata"),
)


@pytest.fixture(autouse=True)
def _offline_remote_fetchers(request, monkeypatch):
    if request.node.get_closest_marker("real_fetchers"):
        return
    import importlib

    stand_ins = {}
    for module_name, attr, stand_in in _OFFLINE_FETCHERS:
        stand_ins[attr] = stand_in
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        if hasattr(module, attr):
            if stand_in is _offline_probe_api_models:
                stand_in.real = getattr(module, attr)
            monkeypatch.setattr(module, attr, stand_in)
    # Provider profiles fetch ``<base_url>/models`` themselves; offline → None
    # ("fall back to the static model list", see ProviderProfile.fetch_models).
    try:
        from providers.base import ProviderProfile

        monkeypatch.setattr(ProviderProfile, "fetch_models", lambda self, *a, **k: None)
    except Exception:
        pass
    for module_name, attr in _OFFLINE_FETCHER_COPIES:
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, attr):
            monkeypatch.setattr(module, attr, stand_ins[attr])


# Backward-compat alias — old tests reference this fixture name. Keep it
# as a no-op wrapper so imports don't break.
@pytest.fixture(autouse=True)
def _assume_online(request, monkeypatch):
    """``hermes update`` probes GitHub before doing anything (AIS-463). Tests
    must not depend on the real network: assume online unless a test opts
    out with the ``real_connectivity`` marker."""
    if request.node.get_closest_marker("real_connectivity"):
        return
    try:
        import hermes_cli.connectivity as connectivity
    except Exception:
        return
    monkeypatch.setattr(connectivity, "is_offline", lambda *a, **k: False)


@pytest.fixture(autouse=True)
def _isolate_hermes_home(_hermetic_environment):
    """Alias preserved for any test that yields this name explicitly."""
    return None


# ── Module-level state reset — replaced by per-file process isolation ──────
#
# Each test FILE runs in a freshly-spawned ``python -m pytest <file>``
# subprocess via ``scripts/run_tests_parallel.py``, so module-level dicts /
# sets / ContextVars from tests in one file cannot leak into tests in
# another file. No manual per-module clearing needed.
#
# Within a single file, ordering is the author's responsibility. If your
# tests in the same file share mutable state, either reset it explicitly
# in a fixture or split them across files.
#
# The skill ``test-suite-cascade-diagnosis`` documents the cascade patterns
# this replaces; the running example was ``test_command_guards`` failing
# 12/15 CI runs because ``tools.approval._session_approved`` carried
# approvals from one test's session into another's.


@pytest.fixture()
def messaging_platforms_enabled(monkeypatch):
    """Lift the AIS-444 retirement gate for tests of the (kept) messaging code.

    AIMDS-Agent no longer loads, lists or configures chat platforms, but the
    adapters and their dashboard endpoints stay in the tree; their own tests
    keep exercising that code with the gate lifted.
    """
    monkeypatch.setattr("gateway.config.is_retired_platform", lambda name: False)


@pytest.fixture()
def tmp_dir(tmp_path):
    """Provide a temporary directory that is cleaned up automatically."""
    return tmp_path


@pytest.fixture()
def mock_config():
    """Return a minimal hermes config dict suitable for unit tests."""
    return {
        "model": "test/mock-model",
        "toolsets": ["terminal", "file"],
        "max_turns": 10,
        "terminal": {
            "backend": "local",
            "cwd": "/tmp",
            "timeout": 30,
        },
        "compression": {"enabled": False},
        "memory": {"memory_enabled": False, "user_profile_enabled": False},
        "command_allowlist": [],
    }


# ── Per-test timeout — handled by the isolation plugin ─────────────────────
#
# The subprocess-per-test plugin enforces the configured ``isolate_timeout``
# ini key by terminating the child if it overruns. The old SIGALRM-based
# fixture (POSIX-only, didn't work on Windows) is gone.


@pytest.fixture(autouse=True)
def _ensure_current_event_loop(request):
    """Provide a default event loop for sync tests that call get_event_loop().

    Python 3.11+ no longer guarantees a current loop for plain synchronous tests.
    A number of gateway tests still use asyncio.get_event_loop().run_until_complete(...).
    Ensure they always have a usable loop without interfering with pytest-asyncio's
    own loop management for @pytest.mark.asyncio tests.

    On Python 3.12+, ``asyncio.get_event_loop_policy().get_event_loop()`` with no
    *running* loop emits DeprecationWarning; skip that path and install a fresh
    loop via ``new_event_loop()`` instead.
    """
    if request.node.get_closest_marker("asyncio") is not None:
        yield
        return

    loop = None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        pass

    if loop is None and sys.version_info < (3, 12):
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            loop = None

    created = loop is None or loop.is_closed()
    if created:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    try:
        yield
    finally:
        if created and loop is not None:
            try:
                loop.close()
            finally:
                asyncio.set_event_loop(None)


# ── Live-system guard ──────────────────────────────────────────────────────
#
# Several test files exercise the gateway-restart / kill code paths
# (``cmd_update``, ``kill_gateway_processes``, ``stop_profile_gateway``).
# When a single test forgets to mock either ``os.kill`` or the global
# ``find_gateway_pids`` helper, the real call leaks out of the hermetic
# environment and finds the developer's live ``hermes-gateway`` process
# via ``psutil`` — sending it SIGTERM mid-test. The shutdown forensics in
# PR #23285 caught this happening 5+ times in 3 days, every time
# correlated with a ``tests/hermes_cli/`` pytest run starting up.
#
# This fixture makes the leak impossible by intercepting the two
# primitives that actually do damage:
#
#  • ``os.kill`` rejects any PID outside the test process subtree with
#    a hard ``RuntimeError`` so the offending test gets a stack trace
#    instead of silently murdering the real gateway.
#  • ``subprocess.run`` / ``subprocess.Popen`` / ``call`` / ``check_call`` /
#    ``check_output`` reject any ``systemctl ... <verb> hermes-gateway``
#    invocation that would mutate the live unit. Read-only systemctl
#    calls (``status``, ``show``, ``list-units``) still pass through.
#
# We intentionally do NOT stub ``find_gateway_pids`` / ``_scan_gateway_pids``
# here — tests of those functions themselves need the real implementation.
# Even if a test gets the live gateway PID back from a real scan, the
# ``os.kill`` guard above catches the actual signal call, and the
# ``systemctl`` guard catches the systemd path. Discovery without
# delivery is harmless.

_LIVE_SYSTEM_GUARD_BYPASS_MARK = "live_system_guard_bypass"


def pytest_configure(config):  # noqa: D401 — pytest hook
    """Register markers used by hermetic conftest."""
    config.addinivalue_line(
        "markers",
        f"{_LIVE_SYSTEM_GUARD_BYPASS_MARK}: bypass the live-system guard "
        "(only for tests that genuinely need real os.kill / subprocess "
        "behaviour — e.g. PTY tests that signal their own child).",
    )
    _install_network_guard()  # see "Network guard (AIS-487)" below
    _install_browser_guard()  # never opens the developer's browser
    # No fire-and-forget session-title threads: they call the auxiliary model
    # (the Suite / a test's fake base URL) and outlive their test, so a late
    # one trips the network guard in whatever test runs next (flaky errors in
    # tui_gateway and ACP tests). Process-wide, because a per-test monkeypatch
    # is undone before such a thread looks. Title tests unset it themselves.
    os.environ["HERMES_DISABLE_AUTO_TITLE"] = "1"
    # Session-wide baseline of _hermetic_environment 4c/4f for threads that
    # outlive their test (its monkeypatch is undone by then) and for
    # subprocesses: no developer GitHub CLI / Codex CLI login either way.
    import tempfile

    _session_cred_dir = tempfile.mkdtemp(prefix="hermes-test-creds-")
    _net_state["session_cred_dir"] = _session_cred_dir
    os.environ["GH_CONFIG_DIR"] = os.path.join(_session_cred_dir, "gh")
    os.environ["GH_HOST"] = _NO_GH_LOGIN_HOST
    os.environ["CODEX_HOME"] = os.path.join(_session_cred_dir, "codex")
    _capture_live_llm_env()  # before _hermetic_environment blanks *_API_KEY


@pytest.fixture(autouse=True)
def _live_system_guard(request, monkeypatch):
    """Block real os.kill / systemctl / gateway-pid scans during tests.

    See block comment above for the why. Tests that genuinely need
    real signal delivery (e.g. PTY tests that SIGINT their own child)
    can opt out with ``@pytest.mark.live_system_guard_bypass``.

    Coverage (every primitive that can deliver a signal to or otherwise
    terminate a foreign process):
      • os.kill, os.killpg (POSIX)
      • subprocess.run / Popen / call / check_call / check_output
      • subprocess.getoutput / getstatusoutput
      • os.system / os.popen
      • pty.spawn
      • asyncio.create_subprocess_exec / create_subprocess_shell
    Subprocess inspection looks at the WHOLE command string (not just
    tokens[0]), so ``bash -c "systemctl restart hermes-gateway"``,
    ``sudo systemctl ...``, ``env systemctl ...``, ``setsid systemctl ...``
    are all caught. ``pkill``/``killall``/``taskkill`` invocations
    targeting hermes/python patterns are also blocked.
    """
    if request.node.get_closest_marker(_LIVE_SYSTEM_GUARD_BYPASS_MARK):
        yield
        return

    import os as _os
    import shlex as _shlex
    import subprocess as _subprocess

    test_pid = _os.getpid()
    # Capture the test process's existing children at fixture start —
    # any *new* children spawned by the test are also allowlisted via
    # the live psutil walk below. Static set keeps the fast path cheap.
    try:
        import psutil as _psutil
        _initial_children = {
            c.pid for c in _psutil.Process(test_pid).children(recursive=True)
        }
    except Exception:
        _psutil = None
        _initial_children = set()

    def _is_own_subtree(pid: int) -> bool:
        # PID 0 means "our own process group"; -1 means "every process we
        # can signal". Both are dangerous when paired with SIGTERM/SIGKILL,
        # but pid 0 is technically scoped to our group so allow it; pid -1
        # is treated as foreign (refuse).
        if pid == 0:
            return True
        if pid < 0:
            return False
        if pid == test_pid or pid in _initial_children:
            return True
        if _psutil is None:
            return False
        try:
            walker = _psutil.Process(pid)
        except Exception:
            # Stale PID — kill would be a no-op anyway, allow it.
            return True
        try:
            for parent in walker.parents():
                if parent.pid == test_pid:
                    return True
        except Exception:
            return False
        return False

    real_kill = _os.kill

    def _guarded_kill(pid, sig, *args, **kwargs):
        if _is_own_subtree(int(pid)):
            return real_kill(pid, sig, *args, **kwargs)
        raise RuntimeError(
            f"tests/conftest.py live-system guard: blocked os.kill("
            f"{pid}, {sig}) — PID is outside the test process subtree. "
            "If this fired in CI it means the test reached a real "
            "kill_gateway_processes / stop_profile_gateway / cmd_update "
            "code path without mocking find_gateway_pids and os.kill. "
            "Mock both, or mark the test with "
            "@pytest.mark.live_system_guard_bypass if real signal "
            "delivery is genuinely required."
        )

    monkeypatch.setattr(_os, "kill", _guarded_kill)

    # ``os.killpg`` is the same risk class — sends a signal to every
    # process in a group. The gateway is a session leader (its own
    # PGID == its PID), so killpg(gateway_pid, SIGTERM) is a one-shot
    # kill of the live process. Allow it only when the target PGID is
    # the test process's own group.
    if hasattr(_os, "killpg"):
        real_killpg = _os.killpg
        own_pgid = _os.getpgrp()

        def _guarded_killpg(pgid, sig, *args, **kwargs):
            if int(pgid) == own_pgid or _is_own_subtree(int(pgid)):
                return real_killpg(pgid, sig, *args, **kwargs)
            raise RuntimeError(
                f"tests/conftest.py live-system guard: blocked "
                f"os.killpg({pgid}, {sig}) — PGID is outside the test "
                "process group. See _live_system_guard for the why."
            )

        monkeypatch.setattr(_os, "killpg", _guarded_killpg)

    # ── Subprocess command-string inspection (whole-line) ──────────
    _HERMES_TOKENS = (
        "hermes-gateway",
        "hermes.service",
        "hermes_cli.main gateway",
        "hermes_cli/main.py gateway",
        "gateway/run.py",
        "hermes gateway",
    )
    _MUTATING_VERBS = (
        "restart", "start", "stop", "kill", "reload",
        "reset-failed", "enable", "disable", "mask", "unmask",
        "daemon-reload", "try-restart", "reload-or-restart",
    )
    _PROCESS_KILLERS = ("pkill", "killall", "taskkill", "skill", "fuser")
    _URL_OPENERS = (
        "open", "xdg-open", "start", "explorer", "explorer.exe", "gio open",
        "gnome-open", "kde-open", "kde-open5", "wslview", "sensible-browser",
        "x-www-browser", "www-browser",
    )

    def _cmd_to_string(cmd) -> str:
        if cmd is None:
            return ""
        if isinstance(cmd, (bytes, bytearray)):
            try:
                return bytes(cmd).decode(errors="replace")
            except Exception:
                return ""
        if isinstance(cmd, str):
            return cmd
        if isinstance(cmd, (list, tuple)):
            try:
                return " ".join(str(t) for t in cmd)
            except Exception:
                return ""
        return str(cmd)

    def _matches_hermes_gateway(cmd_str: str) -> bool:
        low = cmd_str.lower()
        return any(tok in low for tok in _HERMES_TOKENS)

    def _is_blocked_systemctl(cmd) -> bool:
        cmd_str = _cmd_to_string(cmd)
        if "systemctl" not in cmd_str:
            return False
        if not _matches_hermes_gateway(cmd_str):
            return False
        try:
            tokens = _shlex.split(cmd_str)
        except ValueError:
            tokens = cmd_str.split()
        return any(verb in tokens for verb in _MUTATING_VERBS)

    def _is_process_killer(cmd) -> bool:
        cmd_str = _cmd_to_string(cmd)
        try:
            tokens = _shlex.split(cmd_str)
        except ValueError:
            tokens = cmd_str.split()
        if not tokens:
            return False
        for tok in tokens:
            head = tok.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            if head in _PROCESS_KILLERS:
                low = cmd_str.lower()
                # pkill -f pattern: catch hermes-themed patterns + a
                # plain "python" -f which would catch the live gateway
                # whose cmdline contains "python -m hermes_cli.main".
                if (
                    "hermes" in low
                    or "gateway" in low
                    or ("python" in low and "-f" in tokens)
                ):
                    return True
        return False

    def _is_gateway_spawn(cmd) -> bool:
        # A real ``hermes gateway run|start|restart`` started from a test
        # outlives it: detached (setsid, parent = launchd/init), it keeps a
        # cron ticker and the kanban dispatcher running against the test's
        # HERMES_HOME and restarts itself on every checkout change — the
        # developer machine collects one per test run and slows down.
        cmd_str = _cmd_to_string(cmd)
        low = cmd_str.lower()
        if "hermes" not in low or "gateway" not in low:
            return False
        try:
            tokens = _shlex.split(cmd_str)
        except ValueError:
            tokens = cmd_str.split()
        if "--help" in tokens or "-h" in tokens:
            return False  # argparse probes exit right away
        for i, tok in enumerate(tokens[:-1]):
            if tok != "gateway":
                continue
            rest = [t for t in tokens[i + 1:] if not t.startswith("-")]
            if rest and rest[0] in ("run", "start", "restart"):
                return True
        return False

    def _url_opener_target(cmd):
        """``open``/``xdg-open``/``start``/``explorer`` … launch → its args."""
        cmd_str = _cmd_to_string(cmd)
        try:
            tokens = _shlex.split(cmd_str, posix=(_os.name != "nt"))
        except ValueError:
            tokens = cmd_str.split()
        while tokens and tokens[0].rsplit("/", 1)[-1] in ("env", "nohup", "setsid"):
            tokens = tokens[1:]
        if not tokens:
            return None
        head = tokens[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
        if head in ("cmd", "cmd.exe") and len(tokens) > 2 and tokens[1].lower() == "/c":
            tokens = tokens[2:]
            head = tokens[0].lower()
        if head == "gio" and len(tokens) > 2 and tokens[1] == "open":
            tokens = tokens[1:]
            head = "gio open"
        if head in _URL_OPENERS and len(tokens) > 1:
            return " ".join(str(t) for t in tokens[1:])
        return None

    def _is_keychain_secret_read(cmd) -> bool:
        cmd_str = _cmd_to_string(cmd)
        if "security" not in cmd_str:
            return False
        tokens = cmd_str.split()
        return bool(tokens) and tokens[0].rsplit("/", 1)[-1] == "security" and any(
            t in ("find-generic-password", "find-internet-password") for t in tokens
        )

    def _check_subprocess_cmd(name, cmd):
        if _is_keychain_secret_read(cmd):
            # The developer's macOS Keychain (Claude Code sign-in …) must not
            # feed unit tests. Behave like a machine without the entry; tests
            # of the keychain reader mock subprocess themselves (AIS-487).
            raise FileNotFoundError(
                "tests/conftest.py live-system guard: keychain secret reads are "
                "disabled in tests"
            )
        target = _url_opener_target(cmd)
        if target is not None:
            _record_attempt("launch", f"{name}: {target}")
            raise RuntimeError(
                f"tests/conftest.py live-system guard: blocked "
                f"subprocess.{name}({cmd!r}) — would open a browser/app on "
                "the developer's machine. Mock the launcher in the test."
            )
        if _is_gateway_spawn(cmd):
            raise RuntimeError(
                f"tests/conftest.py live-system guard: blocked "
                f"subprocess.{name}({cmd!r}) — would start a real, detached "
                "Hermes gateway that outlives the test. Mock "
                "_spawn_detached_gateway / launch_detached_profile_gateway_restart "
                "/ _spawn_hermes_action, or mark with "
                "@pytest.mark.live_system_guard_bypass."
            )
        if _is_blocked_systemctl(cmd):
            raise RuntimeError(
                f"tests/conftest.py live-system guard: blocked "
                f"subprocess.{name}({cmd!r}) — would mutate the "
                "live hermes-gateway systemd unit. Mock "
                "subprocess.run / _run_systemctl in the test, or "
                "mark with @pytest.mark.live_system_guard_bypass."
            )
        if _is_process_killer(cmd):
            raise RuntimeError(
                f"tests/conftest.py live-system guard: blocked "
                f"subprocess.{name}({cmd!r}) — process-killer command "
                "targeting hermes/python could hit the live gateway. "
                "Mark with @pytest.mark.live_system_guard_bypass if "
                "intentional."
            )

    def _wrap_subprocess(name, real):
        def _guarded(cmd, *args, **kwargs):
            _check_subprocess_cmd(name, cmd)
            return real(cmd, *args, **kwargs)
        _guarded.__name__ = f"_guarded_{name}"
        # Make the wrapper subscriptable like the wrapped callable when
        # the wrapped object is. ``subprocess.Popen[bytes]`` is used as
        # a type annotation in third-party packages (mcp, etc.); replacing
        # ``Popen`` with a plain function breaks ``Popen[bytes]`` at
        # import time. Defer ``__class_getitem__`` to the original.
        if hasattr(real, "__class_getitem__"):
            _guarded.__class_getitem__ = real.__class_getitem__
        return _guarded

    def _wrap_popen():
        """Subclass Popen so isinstance checks AND Popen[bytes] still work."""
        real = _subprocess.Popen

        class _GuardedPopen(real):  # type: ignore[misc, valid-type]
            def __init__(self, cmd, *args, **kwargs):
                _check_subprocess_cmd("Popen", cmd)
                super().__init__(cmd, *args, **kwargs)

        _GuardedPopen.__name__ = "Popen"
        _GuardedPopen.__qualname__ = "Popen"
        return _GuardedPopen

    real_run = _subprocess.run
    real_popen = _subprocess.Popen
    real_call = _subprocess.call
    real_check_call = _subprocess.check_call
    real_check_output = _subprocess.check_output
    real_getoutput = _subprocess.getoutput
    real_getstatusoutput = _subprocess.getstatusoutput

    monkeypatch.setattr(_subprocess, "run", _wrap_subprocess("run", real_run))
    monkeypatch.setattr(_subprocess, "Popen", _wrap_popen())
    monkeypatch.setattr(_subprocess, "call", _wrap_subprocess("call", real_call))
    monkeypatch.setattr(
        _subprocess, "check_call", _wrap_subprocess("check_call", real_check_call)
    )
    monkeypatch.setattr(
        _subprocess,
        "check_output",
        _wrap_subprocess("check_output", real_check_output),
    )
    monkeypatch.setattr(
        _subprocess, "getoutput", _wrap_subprocess("getoutput", real_getoutput)
    )
    monkeypatch.setattr(
        _subprocess,
        "getstatusoutput",
        _wrap_subprocess("getstatusoutput", real_getstatusoutput),
    )

    # os.system / os.popen — same risk class, completely unwrapped before.
    real_os_system = _os.system
    real_os_popen = _os.popen

    def _guarded_os_system(command):
        _check_subprocess_cmd("os.system", command)
        return real_os_system(command)

    def _guarded_os_popen(cmd, *args, **kwargs):
        _check_subprocess_cmd("os.popen", cmd)
        return real_os_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(_os, "system", _guarded_os_system)
    monkeypatch.setattr(_os, "popen", _guarded_os_popen)

    # pty.spawn — POSIX-only.
    try:
        import pty as _pty
        if hasattr(_pty, "spawn"):
            real_pty_spawn = _pty.spawn

            def _guarded_pty_spawn(argv, *args, **kwargs):
                _check_subprocess_cmd("pty.spawn", argv)
                return real_pty_spawn(argv, *args, **kwargs)

            monkeypatch.setattr(_pty, "spawn", _guarded_pty_spawn)
    except Exception:
        pass

    # asyncio.create_subprocess_* — bypasses subprocess module entirely.
    try:
        import asyncio as _asyncio
        real_async_exec = _asyncio.create_subprocess_exec
        real_async_shell = _asyncio.create_subprocess_shell

        async def _guarded_async_exec(program, *args, **kwargs):
            _check_subprocess_cmd(
                "asyncio.create_subprocess_exec", [program, *args]
            )
            return await real_async_exec(program, *args, **kwargs)

        async def _guarded_async_shell(cmd, *args, **kwargs):
            _check_subprocess_cmd("asyncio.create_subprocess_shell", cmd)
            return await real_async_shell(cmd, *args, **kwargs)

        monkeypatch.setattr(_asyncio, "create_subprocess_exec", _guarded_async_exec)
        monkeypatch.setattr(
            _asyncio, "create_subprocess_shell", _guarded_async_shell
        )
    except Exception:
        pass

    yield


# ── Network guard (AIS-487) ─────────────────────────────────────────────────
#
# Unit tests must never reach the real network. Before this guard, tests
# silently resolved api.x.ai, openrouter.ai, models.dev, api.github.com …
# and only passed because production code swallows network failures. That
# made the suite slow, flaky offline, and leaked request metadata.
#
# The guard is installed once per pytest process (``pytest_configure``, so
# import-time calls during collection are caught too) and makes every
# non-loopback DNS lookup / connect behave like an offline machine:
#
#  • ``socket.getaddrinfo`` / ``gethostbyname`` / ``gethostbyname_ex``
#    raise ``socket.gaierror`` (EAI_NONAME) for non-local hosts.
#  • ``socket.socket.connect`` / ``connect_ex`` refuse non-loopback
#    addresses with ``OSError(ENETUNREACH)`` (``connect_ex`` returns it).
#    ``socket.create_connection``, asyncio, httpx, requests, aiohttp and
#    ssl all funnel through these primitives.
#
# Allowed: localhost / *.localhost, 127.0.0.0/8, ::1, 0.0.0.0 / ::, the
# machine's own hostname, unix-domain sockets — so tests that start their
# own servers on loopback keep working.
#
# Every blocked attempt is recorded with the test nodeid. In the default
# ``raise`` mode a test that attempted a blocked connection fails at
# teardown even when the code under test swallowed the error — otherwise
# new offenders would hide behind production's offline tolerance.
#
# Knobs:
#  • ``@pytest.mark.network`` — the test needs the real network. Skipped
#    unless ``HERMES_LIVE_TESTS=1``; then the guard is off for that test.
#  • ``HERMES_TEST_NETWORK_GUARD=report`` — audit mode: let the call
#    through, only record it (summary at session end). ``off`` disables
#    the guard entirely. Default: ``raise``.
#  • ``HERMES_TEST_NETWORK_LOG=<path>`` — append one JSON line per
#    blocked attempt, with the innermost project frames (the seam to mock).
#    Useful with the per-file parallel runner, where every file runs in its
#    own pytest process.
#  • ``fake_public_dns`` fixture — resolve hostnames to a public address for
#    code that only inspects a resolution (SSRF pre-flight checks).
#  • ``live_llm`` fixture — the one sanctioned remote host (IAMDS vLLM), see
#    the end of this file.
#
# The remote catalog/metadata fetchers production calls on agent start are
# stubbed offline by ``_offline_remote_fetchers`` (above). Subprocesses
# spawned by tests are not covered (fresh interpreter); mock at the
# subprocess boundary instead.

import errno as _errno
import ipaddress as _ipaddress
import json as _json
import socket as _socket
import threading as _threading

_NETWORK_MARK = "network"
_NETWORK_GUARD_ENV = "HERMES_TEST_NETWORK_GUARD"
_NETWORK_LOG_ENV = "HERMES_TEST_NETWORK_LOG"
_LIVE_TESTS_ENV = "HERMES_LIVE_TESTS"

_net_state = {
    "mode": "raise",
    "nodeid": "<collection>",
    "bypass": False,
    # Hosts (and their resolved IPs) a fixture opened for the running test —
    # only ever the live vLLM host, see ``live_llm`` below.
    "allow": set(),
    "attempts": [],  # list of (nodeid, kind, host)
}
_net_lock = _threading.Lock()
_net_real: dict = {}


def _network_guard_mode() -> str:
    mode = os.environ.get(_NETWORK_GUARD_ENV, "raise").strip().lower()
    return mode if mode in {"raise", "report", "off"} else "raise"


def _live_tests_enabled() -> bool:
    return os.environ.get(_LIVE_TESTS_ENV, "").strip().lower() in {"1", "true", "yes"}


def _local_hostnames() -> set:
    names = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
    try:
        host = _socket.gethostname()
        if host:
            names.add(host.lower())
            names.add(host.split(".", 1)[0].lower())
    except Exception:
        pass
    return names


_LOCAL_HOSTNAMES = _local_hostnames()


def _is_local_host(host) -> bool:
    if host is None:
        return True  # getaddrinfo(None, port) → wildcard/loopback for bind()
    if isinstance(host, bytes):
        host = host.decode("latin-1")
    host = str(host).strip().strip("[]").lower().rstrip(".")
    if not host:
        return True
    if "%" in host:  # scoped IPv6 literal, e.g. fe80::1%lo0
        host = host.split("%", 1)[0]
    try:
        ip = _ipaddress.ip_address(host)
    except ValueError:
        return host in _LOCAL_HOSTNAMES or host.endswith(".localhost")
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_unspecified


def _caller_frames(limit: int = 6) -> list:
    """Innermost project frames (outside tests/conftest.py) for the log —
    points at the production seam a test should mock."""
    import traceback as _traceback

    root = str(PROJECT_ROOT)
    stack = [f for f in reversed(_traceback.extract_stack()) if not f.filename.endswith("conftest.py")]
    frames = [
        f"{os.path.relpath(f.filename, root)}:{f.lineno} {f.name}"
        for f in stack
        if f.filename.startswith(root) and "/.venv/" not in f.filename
    ][:limit]
    if not frames:  # e.g. a library's worker thread — show where it came from
        frames = [f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in stack[:limit]]
    return frames


def _network_blocked(kind: str, host) -> bool:
    """Record a non-local attempt; return True when the call must fail."""
    if _net_state["bypass"] or _is_local_host(host):
        return False
    host_s = host.decode("latin-1") if isinstance(host, bytes) else str(host)
    if host_s.strip("[]").lower().rstrip(".") in _net_state["allow"]:
        return False
    _record_attempt(kind, host_s)
    return _net_state["mode"] == "raise"


def _record_attempt(kind: str, target: str) -> None:
    """Remember a blocked network / browser / app-launch attempt for the
    running test (teardown failure, terminal summary, optional JSONL log)."""
    nodeid = _net_state["nodeid"]
    with _net_lock:
        _net_state["attempts"].append((nodeid, kind, target))
    log_path = os.environ.get(_NETWORK_LOG_ENV)
    if log_path:
        record = {"test": nodeid, "kind": kind, "host": target, "where": _caller_frames()}
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(_json.dumps(record) + "\n")
        except OSError:
            pass


def _offline_gaierror(host):
    return _socket.gaierror(
        _socket.EAI_NONAME,
        f"[hermes test network guard] DNS lookup of {host!r} blocked "
        "(mock it, or mark the test @pytest.mark.network)",
    )


def _offline_oserror(host):
    return OSError(
        _errno.ENETUNREACH,
        f"[hermes test network guard] connection to {host!r} blocked "
        "(mock it, or mark the test @pytest.mark.network)",
    )


def _connect_target(address):
    """Host part of a socket address, or None for unix sockets / unknown."""
    if isinstance(address, tuple) and address:
        return address[0]
    return None  # AF_UNIX path (str/bytes) or exotic families


def _install_network_guard() -> None:
    mode = _network_guard_mode()
    _net_state["mode"] = mode
    if mode == "off" or _net_real:
        return
    _net_real.update(
        getaddrinfo=_socket.getaddrinfo,
        gethostbyname=_socket.gethostbyname,
        gethostbyname_ex=_socket.gethostbyname_ex,
        connect=_socket.socket.connect,
        connect_ex=_socket.socket.connect_ex,
    )

    def getaddrinfo(host, *args, **kwargs):
        if _network_blocked("getaddrinfo", host):
            raise _offline_gaierror(host)
        return _net_real["getaddrinfo"](host, *args, **kwargs)

    def gethostbyname(host):
        if _network_blocked("gethostbyname", host):
            raise _offline_gaierror(host)
        return _net_real["gethostbyname"](host)

    def gethostbyname_ex(host):
        if _network_blocked("gethostbyname_ex", host):
            raise _offline_gaierror(host)
        return _net_real["gethostbyname_ex"](host)

    def connect(self, address):
        host = _connect_target(address)
        if host is not None and _network_blocked("connect", host):
            raise _offline_oserror(host)
        return _net_real["connect"](self, address)

    def connect_ex(self, address):
        host = _connect_target(address)
        if host is not None and _network_blocked("connect_ex", host):
            return _errno.ENETUNREACH
        return _net_real["connect_ex"](self, address)

    _socket.getaddrinfo = getaddrinfo
    _socket.gethostbyname = gethostbyname
    _socket.gethostbyname_ex = gethostbyname_ex
    _socket.socket.connect = connect
    _socket.socket.connect_ex = connect_ex


# ── Browser / external-app guard (AIS-487) ──────────────────────────────────
#
# A test must never open the developer's browser or launch an app: a full run
# once walked through the xAI OAuth loopback login and opened
# accounts.x.ai/…/consent in the real browser. Installed with the network
# guard and independent of its mode: ``webbrowser.open`` / ``open_new`` /
# ``open_new_tab`` and every controller from ``webbrowser.get()`` return False
# ("could not open a browser" — production prints the URL instead), and
# ``os.startfile`` raises. Subprocess launches of ``open`` / ``xdg-open`` /
# ``start`` / ``explorer`` … are refused by ``_live_system_guard`` above. Each
# attempt fails the test at teardown. Tests that assert a browser call keep
# working: their own ``patch("webbrowser.open")`` / monkeypatch replaces the
# guard's function for the duration of the test.

_LAUNCH_KINDS = frozenset({"browser", "launch"})
_browser_real: dict = {}


class _BlockedBrowser:
    """Stand-in for every ``webbrowser`` controller during tests."""

    name = basename = "hermes-test-blocked-browser"

    def open(self, url, new=0, autoraise=True):
        _record_attempt("browser", str(url))
        return False

    def open_new(self, url):
        return self.open(url, 1)

    def open_new_tab(self, url):
        return self.open(url, 2)


def _install_browser_guard() -> None:
    import webbrowser

    if _browser_real:
        return
    _browser_real.update(
        open=webbrowser.open,
        open_new=webbrowser.open_new,
        open_new_tab=webbrowser.open_new_tab,
        get=webbrowser.get,
    )
    blocked = _BlockedBrowser()
    webbrowser.open = blocked.open
    webbrowser.open_new = blocked.open_new
    webbrowser.open_new_tab = blocked.open_new_tab
    webbrowser.get = lambda using=None: blocked
    if hasattr(os, "startfile"):  # Windows
        _browser_real["startfile"] = os.startfile

        def _blocked_startfile(path, *args, **kwargs):
            _record_attempt("launch", f"os.startfile {path}")
            raise OSError("[hermes test guard] os.startfile blocked during tests")

        os.startfile = _blocked_startfile


def _uninstall_browser_guard() -> None:
    import webbrowser

    if not _browser_real:
        return
    webbrowser.open = _browser_real["open"]
    webbrowser.open_new = _browser_real["open_new"]
    webbrowser.open_new_tab = _browser_real["open_new_tab"]
    webbrowser.get = _browser_real["get"]
    if "startfile" in _browser_real:
        os.startfile = _browser_real["startfile"]
    _browser_real.clear()


def _uninstall_network_guard() -> None:
    if not _net_real:
        return
    _socket.getaddrinfo = _net_real["getaddrinfo"]
    _socket.gethostbyname = _net_real["gethostbyname"]
    _socket.gethostbyname_ex = _net_real["gethostbyname_ex"]
    _socket.socket.connect = _net_real["connect"]
    _socket.socket.connect_ex = _net_real["connect_ex"]
    _net_real.clear()


def pytest_unconfigure(config):  # noqa: D401 — pytest hook
    _uninstall_network_guard()
    _uninstall_browser_guard()
    cred_dir = _net_state.pop("session_cred_dir", None)
    if cred_dir:
        import shutil

        shutil.rmtree(cred_dir, ignore_errors=True)


def pytest_collection_modifyitems(config, items):  # noqa: D401 — pytest hook
    """Skip ``@pytest.mark.network`` tests unless HERMES_LIVE_TESTS=1."""
    if _live_tests_enabled():
        return
    skip = pytest.mark.skip(
        reason=f"needs the real network; set {_LIVE_TESTS_ENV}=1 to run"
    )
    for item in items:
        if item.get_closest_marker(_NETWORK_MARK):
            item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):  # noqa: D401 — pytest hook
    """Attribute network attempts to the running test; lift the guard for
    ``network``-marked tests when live tests are enabled."""
    _net_state["nodeid"] = item.nodeid
    _net_state["bypass"] = bool(
        item.get_closest_marker(_NETWORK_MARK) and _live_tests_enabled()
    )
    try:
        yield
    finally:
        _net_state["nodeid"] = "<between tests>"
        _net_state["bypass"] = False


@pytest.fixture(autouse=True)
def _network_guard(request):
    """Fail a test that attempted a blocked connection (``raise`` mode),
    even when the code under test swallowed the offline error."""
    yield
    nodeid = request.node.nodeid
    with _net_lock:
        mine = [(kind, host) for nid, kind, host in _net_state["attempts"] if nid == nodeid]
    launches = sorted({f"{kind} {target}" for kind, target in mine if kind in _LAUNCH_KINDS})
    if launches:
        pytest.fail(
            "test tried to open a browser / launch an app (blocked by the "
            "AIS-487 guard in tests/conftest.py) — mock webbrowser / the "
            "launcher in the test:\n  " + "\n  ".join(launches),
            pytrace=False,
        )
    if _net_state["mode"] != "raise" or _net_state["bypass"]:
        return
    hits = sorted({f"{kind} {host}" for kind, host in mine if kind not in _LAUNCH_KINDS})
    if hits:
        pytest.fail(
            "test attempted real network access (blocked by the AIS-487 "
            "network guard in tests/conftest.py) — mock the call or mark the "
            "test @pytest.mark.network:\n  " + "\n  ".join(hits),
            pytrace=False,
        )


_FAKE_PUBLIC_IP = "93.184.215.14"  # a public address; connecting stays blocked


@pytest.fixture
def fake_public_dns(monkeypatch):
    """Resolve every non-loopback hostname to one public IP, offline.

    For code that only *inspects* a resolution — SSRF pre-flight checks such
    as ``tools.url_safety.is_safe_url`` — in front of a transport the test
    mocks anyway. Loopback names and IP literals resolve as usual; a real
    connection to the fake address is still refused by the network guard.
    """
    guarded = _socket.getaddrinfo

    def _fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        name = host.decode("latin-1") if isinstance(host, bytes) else host
        try:
            _ipaddress.ip_address(str(name).strip("[]"))
            is_literal = True
        except ValueError:
            is_literal = False
        if name is None or is_literal or _is_local_host(name):
            return guarded(host, port, family, type, proto, flags)
        sock_type = type or _socket.SOCK_STREAM
        return [(_socket.AF_INET, sock_type, 6, "", (_FAKE_PUBLIC_IP, port or 0))]

    monkeypatch.setattr(_socket, "getaddrinfo", _fake_getaddrinfo)
    return _FAKE_PUBLIC_IP


def pytest_terminal_summary(terminalreporter, exitstatus, config):  # noqa: D401
    live = _live_llm_state.get("target")
    if live is not None:
        terminalreporter.section("live_llm")
        terminalreporter.write_line(live.describe())
    attempts = _net_state["attempts"]
    if not attempts:
        return
    by_test: dict = {}
    for nodeid, kind, host in attempts:
        by_test.setdefault(nodeid, set()).add(f"{kind} {host}")
    terminalreporter.section(
        f"network/browser guard ({_net_state['mode']}): {len(attempts)} blocked attempt(s)"
    )
    for nodeid in sorted(by_test):
        terminalreporter.write_line(f"{nodeid}: {', '.join(sorted(by_test[nodeid]))}")


# ── live_llm: real vLLM when reachable, loopback fake otherwise (AIS-487) ────
#
# The few tests that genuinely benefit from a real model (a smoke test of a
# chat completion through the agent's OpenAI client path) request the
# ``live_llm`` fixture. It yields a ``LiveLLM`` target — base URL, key, model —
# and the test runs the same code either way:
#
#  • live: ``VLLM_IAMDS_API_KEY`` is set and ``<base>/v1/models`` answers
#    within a few seconds. Base URL from ``VLLM_IAMDS_BASE_URL`` (CI: a repo
#    variable), default ``https://vllm.iamds.com``; key from the
#    ``VLLM_IAMDS_API_KEY`` secret. The network guard opens exactly that host
#    (and its resolved IPs) for the duration of each such test — x.ai,
#    OpenRouter, models.dev, GitHub … stay blocked.
#  • mock: no key, or the endpoint is unreachable/unhealthy. The target points
#    at an OpenAI-compatible fake on 127.0.0.1 that answers ``/v1/models``
#    and ``/v1/chat/completions`` (plain and SSE) with ``pong``.
#
# Reachability is probed once per pytest process and cached; the chosen mode
# is logged and printed in the terminal summary. Both env vars are captured in
# ``pytest_configure`` because ``_hermetic_environment`` blanks every
# ``*_API_KEY`` before each test.

import dataclasses as _dataclasses

_LIVE_LLM_DEFAULT_BASE_URL = "https://vllm.iamds.com"
_LIVE_LLM_PROBE_TIMEOUT = 2.5  # unreachable hosts must fall back fast
_LIVE_LLM_MOCK_MODEL = "hermes-test-mock"
_LIVE_LLM_MOCK_REPLY = "pong"
_live_llm_state: dict = {"env": {}, "target": None, "server": None}


def _openai_v1_base(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


@_dataclasses.dataclass(frozen=True)
class LiveLLM:
    live: bool
    base_url: str  # OpenAI-compatible, ends in /v1
    api_key: str
    model: str
    reason: str

    def describe(self) -> str:
        if self.live:
            return f"live_llm: LIVE — {self.model} at {self.base_url}"
        return f"live_llm: {self.reason}; loopback fake at {self.base_url}"


def _capture_live_llm_env() -> None:
    _live_llm_state["env"] = {
        "base_url": os.environ.get("VLLM_IAMDS_BASE_URL", "").strip(),
        "api_key": os.environ.get("VLLM_IAMDS_API_KEY", "").strip(),
    }


class _allow_network_host:
    """Open the guard for one host (plus the IPs it resolves to)."""

    def __init__(self, host: str):
        self.names = {host.lower().rstrip(".")}

    def __enter__(self):
        resolve = _net_real.get("getaddrinfo", _socket.getaddrinfo)
        host = next(iter(self.names))
        try:
            for info in resolve(host, None):
                self.names.add(str(info[4][0]).lower())
        except OSError:
            pass  # unresolvable → the probe fails and mock mode is chosen
        self.added = self.names - _net_state["allow"]
        _net_state["allow"].update(self.added)
        return self

    def __exit__(self, *exc):
        _net_state["allow"].difference_update(self.added)
        return False


def _probe_live_llm() -> LiveLLM:
    import urllib.parse
    import urllib.request

    env = _live_llm_state["env"]
    base_url = _openai_v1_base(env.get("base_url") or _LIVE_LLM_DEFAULT_BASE_URL)
    api_key = env.get("api_key") or ""
    if not api_key:
        return LiveLLM(False, "", "", _LIVE_LLM_MOCK_MODEL, "VLLM_IAMDS_API_KEY not set → mock mode")
    host = urllib.parse.urlparse(base_url).hostname or ""
    request = urllib.request.Request(
        base_url + "/models", headers={"Authorization": f"Bearer {api_key}"}
    )
    try:
        with _allow_network_host(host):
            with urllib.request.urlopen(request, timeout=_LIVE_LLM_PROBE_TIMEOUT) as resp:
                payload = _json.loads(resp.read().decode("utf-8") or "{}")
    except Exception as exc:  # unreachable, 401, TLS, bad JSON …
        return LiveLLM(False, "", "", _LIVE_LLM_MOCK_MODEL, f"vLLM unreachable → mock mode ({host}: {type(exc).__name__})")
    models = [
        str(m.get("id")) for m in (payload.get("data") or [])
        if isinstance(m, dict) and m.get("id")
    ] if isinstance(payload, dict) else []
    if not models:
        return LiveLLM(False, "", "", _LIVE_LLM_MOCK_MODEL, f"vLLM lists no models → mock mode ({host})")
    return LiveLLM(True, base_url, api_key, models[0], "probe ok")


def _start_fake_openai_server() -> tuple:
    """OpenAI-compatible fake on 127.0.0.1: /v1/models + /v1/chat/completions."""
    import http.server
    import time as _time

    def _completion_chunk(delta: dict, finish=None) -> dict:
        return {
            "id": "chatcmpl-hermes-test", "object": "chat.completion.chunk",
            "created": int(_time.time()), "model": _LIVE_LLM_MOCK_MODEL,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    usage = {"prompt_tokens": 8, "completion_tokens": 1, "total_tokens": 9}

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # keep test output clean
            pass

        def _send_json(self, status: int, body: dict) -> None:
            data = _json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/v1/models"):
                self._send_json(200, {"object": "list", "data": [
                    {"id": _LIVE_LLM_MOCK_MODEL, "object": "model", "owned_by": "hermes-tests"},
                ]})
            else:
                self._send_json(404, {"error": {"message": "not found"}})

        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = _json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                body = {}
            if not self.path.rstrip("/").endswith("/v1/chat/completions"):
                self._send_json(404, {"error": {"message": "not found"}})
                return
            if not body.get("stream"):
                self._send_json(200, {
                    "id": "chatcmpl-hermes-test", "object": "chat.completion",
                    "created": int(_time.time()), "model": _LIVE_LLM_MOCK_MODEL,
                    "choices": [{"index": 0, "finish_reason": "stop", "message": {
                        "role": "assistant", "content": _LIVE_LLM_MOCK_REPLY}}],
                    "usage": usage,
                })
                return
            chunks = [
                _completion_chunk({"role": "assistant", "content": ""}),
                _completion_chunk({"content": _LIVE_LLM_MOCK_REPLY}),
                {**_completion_chunk({}, finish="stop"), "usage": usage},
            ]
            payload = "".join(f"data: {_json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            data = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = _threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


@pytest.fixture(scope="session")
def _live_llm_target():
    import logging

    target = _probe_live_llm()
    server = None
    if not target.live:
        server, base_url = _start_fake_openai_server()
        target = _dataclasses.replace(target, base_url=base_url, api_key="sk-hermes-test-mock")
    _live_llm_state["target"] = target
    logging.getLogger("tests.live_llm").info(target.describe())
    try:
        yield target
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()


@pytest.fixture
def live_llm(_live_llm_target):
    """A real vLLM chat target when reachable, else a loopback fake.

    Usage::

        def test_smoke(live_llm):
            client = OpenAI(base_url=live_llm.base_url, api_key=live_llm.api_key)
            ...  # assertions must hold for both modes (the fake answers "pong")
    """
    if not _live_llm_target.live:
        yield _live_llm_target
        return
    import urllib.parse

    host = urllib.parse.urlparse(_live_llm_target.base_url).hostname or ""
    with _allow_network_host(host):
        yield _live_llm_target
