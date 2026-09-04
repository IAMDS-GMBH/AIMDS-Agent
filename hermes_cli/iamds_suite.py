"""AIMDS-Suite provider environments: one resolver, one status, one re-auth path.

Why this module exists (AIS-286, support cases SUP-20260902-123535 /
SUP-20260903-074039): the base URL of an AIMDS-Suite environment used to be
read from *different* places depending on the code path — ``config.yaml``
``providers.<slug>.base_url`` in the MCP layer, the ``IAMDS_LITELLM_BASE_URL``
env var in the LLM runtime — so a stale env var silently pointed the prod
provider at staging, the desktop card said "Connected" because a key string
existed, and after the user fixed the domains nothing recovered.

Precedence everywhere (``resolve_suite_endpoint``):

1. ``config.yaml`` ``providers.<slug>.base_url`` (legacy ``iamds-litellm*``
   slugs are honoured) — authoritative, what the settings UI writes
2. the environment's ``*_BASE_URL`` env var (``OPENAI_BASE_URL`` compat for prod)
3. the built-in default host — flagged ``base_url_source="default"`` and never
   treated as "configured" by the status logic

Keys come from ``providers.<slug>.key_env`` or the environment's own env var
only. Staging/dev/localdev deliberately do **not** fall back to the prod key
any more: that phantom credential is what produced "connected" cards with a
foreign key and 401s at runtime.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- environments

@dataclass(frozen=True)
class SuiteEnv:
    provider_id: str
    label: str
    key_env: str
    url_env: str
    default_base_url: str
    legacy_slugs: tuple[str, ...] = ()
    #: extra base-URL env vars accepted for backwards compatibility
    compat_url_envs: tuple[str, ...] = ()


SUITE_ENVIRONMENTS: Dict[str, SuiteEnv] = {
    "aimds-suite-prod": SuiteEnv(
        provider_id="aimds-suite-prod",
        label="AIMDS-Suite",
        key_env="IAMDS_LITELLM_API_KEY",
        url_env="IAMDS_LITELLM_BASE_URL",
        default_base_url="https://suite.iamds.com/litellm/v1",
        legacy_slugs=("iamds-litellm", "aimds-suite"),
        compat_url_envs=("OPENAI_BASE_URL",),
    ),
    "aimds-suite-staging": SuiteEnv(
        provider_id="aimds-suite-staging",
        label="AIMDS-Suite (Staging)",
        key_env="IAMDS_LITELLM_STAGING_API_KEY",
        url_env="IAMDS_LITELLM_STAGING_BASE_URL",
        default_base_url="https://staging.suite.iamds.com/litellm/v1",
        legacy_slugs=("iamds-litellm-staging",),
    ),
    "aimds-suite-dev": SuiteEnv(
        provider_id="aimds-suite-dev",
        label="AIMDS-Suite (Development)",
        key_env="IAMDS_LITELLM_DEV_API_KEY",
        url_env="IAMDS_LITELLM_DEV_BASE_URL",
        default_base_url="https://dev.suite.iamds.com/litellm/v1",
        legacy_slugs=("iamds-litellm-dev",),
    ),
    "aimds-suite-localdev": SuiteEnv(
        provider_id="aimds-suite-localdev",
        label="AIMDS-Suite (Local Dev)",
        key_env="IAMDS_LITELLM_LOCALDEV_API_KEY",
        url_env="IAMDS_LITELLM_LOCALDEV_BASE_URL",
        default_base_url="http://localhost:8000/litellm/v1",
        legacy_slugs=("iamds-litellm-localdev",),
    ),
}

_LEGACY_TO_CANONICAL: Dict[str, str] = {
    legacy: env.provider_id for env in SUITE_ENVIRONMENTS.values() for legacy in env.legacy_slugs
}

STATE_CONNECTED = "connected"
STATE_NEEDS_REAUTH = "needs_reauth"
STATE_NOT_CONFIGURED = "not_configured"
STATE_UNREACHABLE = "unreachable"


def canonical_suite_provider(provider: Optional[str]) -> Optional[str]:
    """Map any AIMDS-Suite slug (incl. legacy ``iamds-litellm*``) to its canonical id."""
    slug = str(provider or "").strip().lower()
    if not slug:
        return None
    if slug in SUITE_ENVIRONMENTS:
        return slug
    return _LEGACY_TO_CANONICAL.get(slug)


def is_suite_provider(provider: Optional[str]) -> bool:
    return canonical_suite_provider(provider) is not None


# --------------------------------------------------------------------------- resolution

@dataclass
class SuiteEndpoint:
    provider_id: str
    label: str
    key_env: str
    url_env: str
    base_url: str = ""
    #: "config" | "env" | "default" | ""
    base_url_source: str = ""
    api_key: str = ""
    #: env var name, "config:key_env:<VAR>" or ""
    key_source: str = ""
    #: value of the environment's URL env var (for mismatch detection)
    env_base_url: str = ""
    #: config URL and env URL both set but different
    env_mismatch: bool = False

    @property
    def configured(self) -> bool:
        """True when a base URL was explicitly configured (config or env)."""
        return bool(self.base_url) and self.base_url_source in {"config", "env"}

    def to_dict(self, *, redact: bool = True) -> Dict[str, Any]:
        data = asdict(self)
        if redact:
            data["api_key"] = _truncate(self.api_key)
        data["configured"] = self.configured
        return data


def _truncate(secret: str, keep: int = 4) -> str:
    secret = str(secret or "")
    if not secret:
        return ""
    if len(secret) <= keep:
        return "…"
    return "…" + secret[-keep:]


def _norm_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _env_value(name: str) -> str:
    """Read an env var preferring ``~/.hermes/.env`` over the process environment."""
    if not name:
        return ""
    try:
        from hermes_cli.config import get_env_value

        val = get_env_value(name)
        if val:
            return str(val).strip()
    except Exception:
        pass
    return os.environ.get(name, "").strip()


def _load_config_safe(config: Optional[dict]) -> dict:
    if isinstance(config, dict):
        return config
    try:
        from hermes_cli.config import load_config

        return load_config() or {}
    except Exception:
        return {}


def _provider_entry(config: dict, env: SuiteEnv) -> dict:
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return {}
    for slug in (env.provider_id, *env.legacy_slugs):
        entry = providers.get(slug)
        if isinstance(entry, dict):
            return entry
    return {}


def resolve_suite_endpoint(
    provider: str,
    *,
    config: Optional[dict] = None,
    allow_default: bool = True,
) -> SuiteEndpoint:
    """Resolve base URL + key for an AIMDS-Suite environment (see module docstring)."""
    canonical = canonical_suite_provider(provider)
    if canonical is None:
        raise ValueError(f"{provider!r} is not an AIMDS-Suite provider")
    env = SUITE_ENVIRONMENTS[canonical]
    cfg = _load_config_safe(config)
    entry = _provider_entry(cfg, env)

    ep = SuiteEndpoint(provider_id=canonical, label=env.label, key_env=env.key_env, url_env=env.url_env)

    config_url = _norm_url(entry.get("base_url") or entry.get("api") or entry.get("url"))
    env_url = _norm_url(_env_value(env.url_env))
    if not env_url:
        for compat in env.compat_url_envs:
            env_url = _norm_url(_env_value(compat))
            if env_url:
                break
    ep.env_base_url = env_url

    if config_url:
        ep.base_url, ep.base_url_source = config_url, "config"
        ep.env_mismatch = bool(env_url) and env_url != config_url
    elif env_url:
        ep.base_url, ep.base_url_source = env_url, "env"
    elif allow_default:
        ep.base_url, ep.base_url_source = env.default_base_url, "default"

    key_env = str(entry.get("key_env") or "").strip() or env.key_env
    ep.key_env = key_env
    api_key = _env_value(key_env)
    if api_key:
        ep.api_key = api_key
        ep.key_source = key_env if key_env == env.key_env else f"config:key_env:{key_env}"
    return ep


def sync_suite_env_from_providers(config: Optional[dict] = None) -> Dict[str, str]:
    """Mirror ``providers.<slug>.base_url`` into the per-environment ``*_BASE_URL`` env vars.

    Runs after the settings UI saves the config. A provider entry sets its
    env var; a removed entry clears it, so a stale env value can no longer
    steer the runtime to another host. Returns ``{env_var: "set"|"removed"}``.
    """
    cfg = _load_config_safe(config)
    changes: Dict[str, str] = {}
    try:
        from hermes_cli.config import remove_env_value, save_env_value
    except Exception:
        return changes
    for env in SUITE_ENVIRONMENTS.values():
        entry = _provider_entry(cfg, env)
        config_url = _norm_url(entry.get("base_url") or entry.get("api") or entry.get("url"))
        current = _norm_url(_env_value(env.url_env))
        try:
            if config_url and config_url != current:
                save_env_value(env.url_env, config_url)
                changes[env.url_env] = "set"
            elif not config_url and current and _provider_entry_absent(cfg, env):
                remove_env_value(env.url_env)
                changes[env.url_env] = "removed"
        except Exception as exc:  # never break a config save over env sync
            logger.warning("suite env sync failed for %s: %s", env.url_env, exc)
    return changes


def _provider_entry_absent(config: dict, env: SuiteEnv) -> bool:
    providers = config.get("providers")
    if not isinstance(providers, dict):
        return True
    return not any(slug in providers for slug in (env.provider_id, *env.legacy_slugs))


# --------------------------------------------------------------------------- probe

def litellm_model_info_url(base_url: str) -> str:
    """Normalize ``host``, ``host/litellm`` or ``host/litellm/v1`` to ``…/litellm/model/info``."""
    normalized = _norm_url(base_url)
    if not normalized:
        return ""
    if normalized.endswith("/litellm/v1"):
        return normalized[: -len("/v1")] + "/model/info"
    if normalized.endswith("/litellm"):
        return normalized + "/model/info"
    if "/litellm/" in normalized:
        return normalized.split("/litellm/", 1)[0].rstrip("/") + "/litellm/model/info"
    return normalized + "/litellm/model/info"


def probe_suite_endpoint(base_url: str, api_key: str, *, timeout: float = 5.0) -> tuple[Optional[int], str]:
    """GET ``/litellm/model/info`` with the key; returns ``(http_status, error)``.

    ``http_status`` is ``None`` on network failure (``error`` explains).
    """
    url = litellm_model_info_url(base_url)
    if not url:
        return None, "no base url"
    headers = {"Accept": "application/json", "User-Agent": "hermes-agent/iamds-suite-status"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), ""
    except urllib.error.HTTPError as exc:
        return int(exc.code), str(exc.reason or "")
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- ntfy (AIS-232)

NTFY_PRIVATE_TOPIC_PREFIX = "private-"
_NTFY_CACHE_TTL_SECONDS = 24 * 3600
_NTFY_TOPIC_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-]")


def suite_root_url(base_url: str) -> str:
    """``https://host/litellm/v1`` → ``https://host`` (service root, no trailing slash)."""
    base = _norm_url(base_url)
    if not base:
        return ""
    for suffix in ("/litellm/v1", "/litellm/mcp", "/litellm", "/v1"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.rstrip("/")


def suite_ntfy_url(base_url: str) -> str:
    """The Suite's ntfy endpoint: ``<service root>/ntfy``."""
    root = suite_root_url(base_url)
    return f"{root}/ntfy" if root else ""


def litellm_key_info_url(base_url: str) -> str:
    root = suite_root_url(base_url)
    return f"{root}/litellm/key/info" if root else ""


def primary_suite_provider(config: Optional[dict] = None) -> Optional[str]:
    """The AIMDS-Suite environment Hermes currently runs on.

    ``model.provider`` when it is a Suite slug; otherwise the first Suite
    environment (prod → staging → dev → localdev) with a configured URL and a
    usable key. ``None`` when no Suite environment is set up.
    """
    cfg = _load_config_safe(config)
    model_cfg = cfg.get("model") if isinstance(cfg.get("model"), dict) else {}
    active = canonical_suite_provider(model_cfg.get("provider"))
    if active:
        ep = resolve_suite_endpoint(active, config=cfg)
        if ep.api_key:
            return active
    for provider_id in SUITE_ENVIRONMENTS:
        ep = resolve_suite_endpoint(provider_id, config=cfg)
        if ep.configured and ep.api_key:
            return provider_id
    return active


def fetch_suite_key_info(base_url: str, api_key: str, *, timeout: float = 5.0) -> Dict[str, Any]:
    """``GET <root>/litellm/key/info`` with the VirtualKey → the ``info`` object (``{}`` on failure)."""
    url = litellm_key_info_url(base_url)
    if not url or not api_key:
        return {}
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}", "User-Agent": "hermes-agent/iamds-suite-ntfy"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except Exception:
        return {}
    info = payload.get("info") if isinstance(payload, dict) else None
    return info if isinstance(info, dict) else (payload if isinstance(payload, dict) else {})


def ntfy_private_topic(user_id: str) -> str:
    uid = str(user_id or "").strip()
    if not uid:
        return ""
    return NTFY_PRIVATE_TOPIC_PREFIX + _NTFY_TOPIC_SAFE_RE.sub("_", uid)


@dataclass
class SuiteNtfy:
    provider_id: str
    server_url: str
    token: str
    user_id: str = ""
    topic: str = ""
    #: topics the key is allowed to use, when the Suite publishes them in key metadata
    topics: List[str] = field(default_factory=list)
    #: "key_info" | "cache" | "no_user_id"
    user_source: str = ""

    def to_dict(self, *, redact: bool = True) -> Dict[str, Any]:
        data = asdict(self)
        if redact:
            data["token"] = _truncate(self.token)
        return data


def _ntfy_cache_path() -> Path:
    from hermes_cli.config import get_hermes_home

    return Path(get_hermes_home()) / "state" / "iamds_suite_ntfy.json"


def _key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()[:16]


def _read_ntfy_cache(provider_id: str, api_key: str) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(_ntfy_cache_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    entry = data.get(provider_id) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or entry.get("key_fingerprint") != _key_fingerprint(api_key):
        return None
    if time.time() - float(entry.get("cached_at") or 0) > _NTFY_CACHE_TTL_SECONDS:
        return None
    return entry


def _write_ntfy_cache(provider_id: str, api_key: str, user_id: str, topics: List[str]) -> None:
    path = _ntfy_cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict):
            data = {}
        data[provider_id] = {"key_fingerprint": _key_fingerprint(api_key), "user_id": user_id, "topics": list(topics), "cached_at": time.time()}
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def _topics_from_key_info(info: Dict[str, Any]) -> List[str]:
    meta = info.get("metadata") if isinstance(info.get("metadata"), dict) else {}
    for key in ("ntfy_topics", "topics", "allowed_topics"):
        raw = meta.get(key) if isinstance(meta, dict) else None
        if raw is None:
            raw = info.get(key)
        if isinstance(raw, list):
            return [str(t) for t in raw if str(t).strip()]
        if isinstance(raw, str) and raw.strip():
            return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def resolve_suite_ntfy(
    provider: Optional[str] = None,
    *,
    config: Optional[dict] = None,
    use_cache: bool = True,
    timeout: float = 5.0,
) -> Optional[SuiteNtfy]:
    """Zero-touch ntfy settings for the Suite: server ``<root>/ntfy``, the
    VirtualKey as Bearer token, the private topic ``private-<user_id>``.

    ``user_id`` comes from LiteLLM ``/key/info`` (cached 24 h per key
    fingerprint under ``state/iamds_suite_ntfy.json``). Returns ``None``
    when no Suite provider with a key is configured; ``topic`` is empty when
    the key info lookup fails — server and token still allow publishing to
    explicitly named topics.
    """
    cfg = _load_config_safe(config)
    provider_id = canonical_suite_provider(provider) if provider else primary_suite_provider(cfg)
    if not provider_id:
        return None
    ep = resolve_suite_endpoint(provider_id, config=cfg)
    if not ep.api_key or not ep.base_url:
        return None
    result = SuiteNtfy(provider_id=provider_id, server_url=suite_ntfy_url(ep.base_url), token=ep.api_key)
    cached = _read_ntfy_cache(provider_id, ep.api_key) if use_cache else None
    if cached and cached.get("user_id"):
        result.user_id = str(cached["user_id"])
        result.topics = [str(t) for t in (cached.get("topics") or [])]
        result.user_source = "cache"
    else:
        info = fetch_suite_key_info(ep.base_url, ep.api_key, timeout=timeout)
        user_id = str(info.get("user_id") or info.get("user") or "").strip()
        if not user_id and isinstance(info.get("metadata"), dict):
            user_id = str(info["metadata"].get("user_id") or "").strip()
        result.user_id = user_id
        result.topics = _topics_from_key_info(info) if info else []
        result.user_source = "key_info" if user_id else "no_user_id"
        if user_id:
            _write_ntfy_cache(provider_id, ep.api_key, user_id, result.topics)
    result.topic = ntfy_private_topic(result.user_id)
    return result


# --------------------------------------------------------------------------- runtime auth-failure flag

_FLAG_LOCK = threading.Lock()


def _flag_path() -> Path:
    try:
        from hermes_cli.config import get_hermes_home

        home = Path(get_hermes_home())
    except Exception:
        home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
    return home / "state" / "iamds_suite_auth.json"


def _read_flags() -> Dict[str, Any]:
    path = _flag_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_flags(data: Dict[str, Any]) -> None:
    path = _flag_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # the flag is advisory — never raise into the agent loop
        logger.debug("could not write %s: %s", path, exc)


def mark_suite_auth_failure(provider: str, http_status: Optional[int], message: str, *, source: str) -> None:
    """Record that the runtime got an auth failure for a Suite environment.

    ``source`` is ``"llm"`` or ``"mcp"``. Read by ``/api/status`` so the
    desktop can switch the provider card to "needs re-auth" without polling
    the LiteLLM endpoint itself.
    """
    canonical = canonical_suite_provider(provider)
    if canonical is None:
        return
    with _FLAG_LOCK:
        flags = _read_flags()
        flags[canonical] = {
            "state": STATE_NEEDS_REAUTH,
            "http_status": http_status,
            "message": str(message or "")[:300],
            "source": source,
            "since": time.time(),
        }
        _write_flags(flags)
    logger.info("suite auth failure recorded provider=%s status=%s source=%s", canonical, http_status, source)


def clear_suite_auth_failure(provider: Optional[str] = None) -> None:
    """Clear the failure flag for one environment (or all when ``provider`` is None)."""
    with _FLAG_LOCK:
        flags = _read_flags()
        if provider is None:
            flags = {}
        else:
            canonical = canonical_suite_provider(provider)
            if canonical is None or canonical not in flags:
                return
            flags.pop(canonical, None)
        _write_flags(flags)


def suite_auth_failures() -> Dict[str, Dict[str, Any]]:
    """Current runtime auth-failure flags keyed by canonical provider id."""
    with _FLAG_LOCK:
        return {k: v for k, v in _read_flags().items() if isinstance(v, dict)}


# --------------------------------------------------------------------------- status

def _usable_secret(value: str) -> bool:
    try:
        from hermes_cli.auth import has_usable_secret

        return bool(has_usable_secret(value))
    except Exception:
        return bool(value and len(value.strip()) >= 4)


def _mcp_status_for(base_url: str) -> Dict[str, Any]:
    """Compare the configured AIMDSSuiteMCP URL with the resolved base URL."""
    info: Dict[str, Any] = {"name": "AIMDSSuiteMCP", "url": "", "url_matches": None, "connected": None}
    try:
        from tools.mcp_tool import _build_iamds_mcp_url, _load_mcp_config, get_mcp_status

        expected = _build_iamds_mcp_url(base_url) if base_url else ""
        configured = _load_mcp_config() or {}
        entry = configured.get("AIMDSSuiteMCP") or {}
        url = _norm_url(entry.get("url")) + ("/" if entry.get("url") else "")
        info["url"] = url
        info["url_matches"] = (url == expected) if (url and expected) else None
        for server in get_mcp_status() or []:
            if str(server.get("name")) == "AIMDSSuiteMCP":
                info["connected"] = bool(server.get("connected"))
                break
    except Exception as exc:
        info["error"] = str(exc)
    return info


def suite_environment_status(
    provider: str,
    *,
    probe: bool = False,
    config: Optional[dict] = None,
    probe_fn: Optional[Callable[[str, str], tuple[Optional[int], str]]] = None,
    include_mcp: bool = True,
) -> Dict[str, Any]:
    """Tri-state status for one Suite environment.

    ``state`` ∈ connected | needs_reauth | not_configured | unreachable;
    ``reason`` ∈ ok | key_missing | url_missing | env_mismatch | http_401 |
    http_403 | runtime_401 | network | probe_skipped.
    """
    ep = resolve_suite_endpoint(provider, config=config, allow_default=True)
    key_ok = _usable_secret(ep.api_key)
    flags = suite_auth_failures()
    runtime_failure = flags.get(ep.provider_id)

    status: Dict[str, Any] = {
        "id": ep.provider_id,
        "label": ep.label,
        "key_env": ep.key_env,
        "base_url": ep.base_url if ep.configured else "",
        "base_url_source": ep.base_url_source,
        "default_base_url": SUITE_ENVIRONMENTS[ep.provider_id].default_base_url,
        "env_mismatch": ep.env_mismatch,
        "env_base_url": ep.env_base_url,
        "key_present": key_ok,
        "key_source": ep.key_source,
        "key_preview": _truncate(ep.api_key) if key_ok else "",
        "http_status": None,
        "probe_error": "",
        "runtime_auth_failure": runtime_failure,
        "state": STATE_NOT_CONFIGURED,
        "reason": "url_missing",
    }

    if not ep.configured and not key_ok:
        status["state"], status["reason"] = STATE_NOT_CONFIGURED, "url_missing"
    elif not ep.configured:
        status["state"], status["reason"] = STATE_NEEDS_REAUTH, "url_missing"
    elif not key_ok:
        status["state"], status["reason"] = STATE_NEEDS_REAUTH, "key_missing"
    elif ep.env_mismatch:
        status["state"], status["reason"] = STATE_NEEDS_REAUTH, "env_mismatch"
    elif runtime_failure:
        code = runtime_failure.get("http_status")
        status["state"] = STATE_NEEDS_REAUTH
        status["reason"] = "runtime_401" if code in (401, 403, None) else f"runtime_{code}"
    elif probe:
        fn = probe_fn or (lambda url, key: probe_suite_endpoint(url, key))
        code, err = fn(ep.base_url, ep.api_key)
        status["http_status"], status["probe_error"] = code, err
        if code is None:
            status["state"], status["reason"] = STATE_UNREACHABLE, "network"
        elif code in (401, 403):
            status["state"], status["reason"] = STATE_NEEDS_REAUTH, f"http_{code}"
        elif code < 500 or code == 429:
            status["state"], status["reason"] = STATE_CONNECTED, "ok"
        else:
            status["state"], status["reason"] = STATE_UNREACHABLE, f"http_{code}"
    else:
        status["state"], status["reason"] = STATE_CONNECTED, "probe_skipped"

    if include_mcp and ep.provider_id == "aimds-suite-prod":
        status["mcp"] = _mcp_status_for(ep.base_url if ep.configured else "")
    return status


def all_suite_statuses(*, probe: bool = False, config: Optional[dict] = None) -> Dict[str, Any]:
    cfg = _load_config_safe(config)
    return {
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environments": [
            suite_environment_status(env.provider_id, probe=probe, config=cfg, include_mcp=(env.provider_id == "aimds-suite-prod"))
            for env in SUITE_ENVIRONMENTS.values()
        ],
    }


# --------------------------------------------------------------------------- re-auth

def apply_reauth(provider: str, *, reload_mcp: bool = True, refresh_sessions: bool = True) -> Dict[str, Any]:
    """Make a freshly written key/URL effective without restarting Hermes.

    1. reload ``~/.hermes/.env`` into the process
    2. clear the runtime auth-failure flag
    3. reset the credential pool statuses (drops the exhausted/dead marks)
    4. rewrite + reconnect the IAMDS MCP server for the new base URL / key
    5. ask live sessions to rebuild their OpenAI client
    """
    canonical = canonical_suite_provider(provider)
    if canonical is None:
        raise ValueError(f"{provider!r} is not an AIMDS-Suite provider")
    result: Dict[str, Any] = {"provider": canonical, "steps": {}}

    try:
        from hermes_cli.config import invalidate_env_cache

        invalidate_env_cache()
    except Exception:
        pass
    try:
        from hermes_cli.env_loader import load_hermes_dotenv

        load_hermes_dotenv()
        result["steps"]["env_reloaded"] = True
    except Exception as exc:
        result["steps"]["env_reloaded"] = f"failed: {exc}"

    clear_suite_auth_failure(canonical)
    result["steps"]["flag_cleared"] = True

    ep = resolve_suite_endpoint(canonical)
    result["endpoint"] = ep.to_dict()

    try:
        from agent.credential_pool import load_pool

        pool = load_pool(canonical)
        result["steps"]["pool_reset"] = int(pool.reset_statuses()) if pool is not None else 0
    except Exception as exc:
        result["steps"]["pool_reset"] = f"failed: {exc}"

    if reload_mcp and ep.configured and ep.api_key:
        try:
            from tools.mcp_tool import discover_mcp_tools, reload_provider_mcp_servers

            reload_provider_mcp_servers(provider=canonical, new_base_url=ep.base_url, new_api_key=ep.api_key)
            discover_mcp_tools()
            result["steps"]["mcp_reloaded"] = True
        except Exception as exc:
            result["steps"]["mcp_reloaded"] = f"failed: {exc}"
    else:
        result["steps"]["mcp_reloaded"] = False

    if refresh_sessions:
        try:
            from tui_gateway.server import refresh_iamds_credentials_for_sessions

            result["steps"]["sessions_refreshed"] = int(refresh_iamds_credentials_for_sessions(canonical))
        except Exception as exc:
            result["steps"]["sessions_refreshed"] = f"skipped: {exc}"
    return result


__all__ = [
    "SUITE_ENVIRONMENTS",
    "SuiteEndpoint",
    "SuiteEnv",
    "STATE_CONNECTED",
    "STATE_NEEDS_REAUTH",
    "STATE_NOT_CONFIGURED",
    "STATE_UNREACHABLE",
    "all_suite_statuses",
    "apply_reauth",
    "canonical_suite_provider",
    "clear_suite_auth_failure",
    "is_suite_provider",
    "litellm_model_info_url",
    "mark_suite_auth_failure",
    "probe_suite_endpoint",
    "resolve_suite_endpoint",
    "suite_auth_failures",
    "suite_environment_status",
    "sync_suite_env_from_providers",
]
