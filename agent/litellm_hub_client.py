"""Helpers for LiteLLM public hub endpoints (Skill/Agent/Model Hub)."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

_HUB_PATH_ALIASES = {
    "agents": "agent_hub",
    "skills": "skill_hub",
}


def resolve_litellm_hub_settings() -> Dict[str, Any]:
    """Resolve LiteLLM hub settings from config.yaml with env fallback.

    Resolution order for (base_url, api_key) -- resolved as a MATCHED PAIR
    wherever possible, so the hub never sends one provider's API key to a
    different provider's base_url (e.g. the prod key against the staging
    URL, which the LiteLLM proxy correctly rejects with 401):
    1. skills.litellm_hub.base_url/api_key (explicit hub override; both from
       the same config block, so already consistent).
    2. The user's actual active provider (auth.json active_provider), if it
       is an api_key provider -- base_url and api_key resolved together via
       resolve_api_key_provider_credentials() so they always match.
    3. LITELLM_PROXY_URL / OPENAI_BASE_URL env vars for base_url, paired with
       IAMDS_LITELLM_API_KEY / LITELLM_KEY / config api_key for api_key (last
       resort fallback, kept for backward compatibility with setups that
       don't use the provider registry at all).
    4. First provider entry with a base_url (legacy fallback), with api_key
       resolved from the same provider entry when possible, then any
       AIMDS-Suite-family provider as a final attempt.
    """
    from hermes_cli.config import load_config, load_env

    cfg = load_config() or {}
    skills_cfg = cfg.get("skills", {}) if isinstance(cfg, dict) else {}
    hub_cfg = skills_cfg.get("litellm_hub", {}) if isinstance(skills_cfg, dict) else {}

    # Baseline (legacy) independent resolution: api_key from env/.env/config,
    # base_url from env/.env/first-provider-with-a-base_url. Kept as the
    # fallback for setups that don't have an active provider resolvable via
    # the provider registry (e.g. bare env-var configuration).
    env_vars = load_env()
    api_key = str(
        env_vars.get("IAMDS_LITELLM_API_KEY")
        or env_vars.get("LITELLM_KEY")
        or os.getenv("IAMDS_LITELLM_API_KEY")
        or os.getenv("LITELLM_KEY")
        or hub_cfg.get("api_key")
        or ""
    ).strip()

    base_url = str(
        hub_cfg.get("base_url")
        or os.getenv("LITELLM_PROXY_URL")
        or os.getenv("OPENAI_BASE_URL")
        or _first_provider_base_url(cfg)
        or ""
    ).strip().rstrip("/")

    # Resolve base_url + api_key together from the user's actual active
    # provider (when no explicit skills.litellm_hub.base_url override is
    # set), so a staging/dev active provider is never paired with a
    # different environment's API key (e.g. the prod key against the
    # staging URL, which the LiteLLM proxy correctly rejects with 401).
    # This takes priority over the legacy independent lookups above.
    if not str(hub_cfg.get("base_url") or "").strip():
        try:
            from hermes_cli.auth import get_active_provider, resolve_api_key_provider_credentials

            active_provider_id = get_active_provider()
            if active_provider_id:
                creds = resolve_api_key_provider_credentials(active_provider_id)
                if isinstance(creds, dict) and creds.get("base_url") and creds.get("api_key"):
                    base_url = str(creds["base_url"]).strip().rstrip("/")
                    api_key = str(creds["api_key"]).strip()
        except Exception:
            pass

    # Whenever base_url ends up pointing at a *known* provider entry (e.g. an
    # explicit skills.litellm_hub.base_url override left api_key blank, or a
    # legacy env-var base_url happens to match a registered provider), prefer
    # that provider's own key_env over the blind IAMDS_LITELLM_API_KEY/
    # LITELLM_KEY fallback above. Without this, an explicit staging/dev
    # base_url with no matching api_key silently pairs with the prod key,
    # which the LiteLLM proxy correctly rejects with 401. Only applies when
    # the user hasn't explicitly set skills.litellm_hub.api_key themselves.
    if not str(hub_cfg.get("api_key") or "").strip():
        matched_key = _provider_key_for_base_url(cfg, base_url, env_vars)
        if matched_key:
            api_key = matched_key

    if base_url.endswith("/v1"):
        base_url = base_url[:-3]

    timeout_raw = hub_cfg.get("timeout", 20)
    try:
        timeout = max(1, int(timeout_raw))
    except (TypeError, ValueError):
        timeout = 20

    return {
        "base_url": base_url,
        "api_key": api_key,
        "timeout": timeout,
    }


def _provider_key_for_base_url(
    cfg: Dict[str, Any], base_url: str, env_vars: Dict[str, str]
) -> str:
    """Return the API key belonging to whichever ``providers.*`` entry's
    ``base_url`` matches *base_url*, resolved via that entry's ``key_env``.

    Prevents pairing e.g. the prod ``IAMDS_LITELLM_API_KEY`` with a staging
    or dev base_url just because the prod env var happens to be checked
    first in the legacy fallback chain.
    """
    from hermes_cli.config import get_env_value

    providers = cfg.get("providers", {}) if isinstance(cfg, dict) else {}
    if not isinstance(providers, dict) or not base_url:
        return ""

    def _norm(url: str) -> str:
        url = url.strip().rstrip("/")
        return url[:-3] if url.endswith("/v1") else url

    target = _norm(base_url)
    if not target:
        return ""

    for entry in providers.values():
        if not isinstance(entry, dict):
            continue
        entry_url = _norm(str(entry.get("base_url") or ""))
        if not entry_url or entry_url != target:
            continue
        key_env = entry.get("key_env")
        if not key_env:
            continue
        val = str(
            env_vars.get(key_env) or os.getenv(key_env) or get_env_value(key_env) or ""
        ).strip()
        if val:
            return val
    return ""


def _first_provider_base_url(cfg: Dict[str, Any]) -> str:
    """Return the base_url of the first configured provider entry, if any."""
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
        logger.warning("[LiteLLMHub] base_url is not configured")
        return None, (
            "LiteLLM hub is not configured. "
            "Set skills.litellm_hub.base_url in config.yaml."
        )
    if require_auth and not api_key:
        logger.warning("[LiteLLMHub] api_key is required but not configured")
        return None, (
            "This LiteLLM hub endpoint requires authentication. "
            "Set skills.litellm_hub.api_key (or LITELLM_KEY)."
        )

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    public_endpoint = _HUB_PATH_ALIASES.get(public_path.strip("/"), public_path.strip("/"))
    # Strip /v1 suffix (may be present when settings are passed directly, bypassing
    # resolve_litellm_hub_settings which normally strips it).
    hub_base = base_url.rstrip("/")
    if hub_base.endswith("/v1"):
        hub_base = hub_base[:-3]
    if hub_base.endswith("/litellm"):
        url = f"{hub_base}/public/{public_endpoint}"
    else:
        url = f"{hub_base}/litellm/public/{public_endpoint}"
    logger.info("[LiteLLMHub] Fetching %s (auth=%s)", url, bool(api_key))
    try:
        resp = httpx.get(
            url,
            headers=headers or None,
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        logger.error("[LiteLLMHub] Request failed for %s: %s", url, exc)
        return None, f"Failed to reach LiteLLM hub at {url}: {exc}"

    logger.info("[LiteLLMHub] Response from %s: HTTP %s", url, resp.status_code)
    if resp.status_code == 401:
        return None, f"LiteLLM hub request failed: unauthorized (401) at {url}. Check API key."
    if resp.status_code == 403:
        return None, f"LiteLLM hub request failed: forbidden (403) at {url}. Check API key scope."
    if resp.status_code != 200:
        return None, f"LiteLLM hub request failed: HTTP {resp.status_code} from {url}."

    try:
        data = resp.json()
        logger.info("[LiteLLMHub] Successfully parsed JSON from %s", url)
        return data, None
    except ValueError:
        logger.error("[LiteLLMHub] Non-JSON response from %s: %.200s", url, resp.text)
        return None, f"LiteLLM hub returned non-JSON response from {url}."


def _parse_agents_response(data: Any) -> list:
    """Normalise a LiteLLM agents response into a plain list."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("data") or data.get("agents") or []
    return []


def fetch_litellm_agents(
    settings: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[list], Optional[str]]:
    """Fetch agents from the LiteLLM agent hub.

    Strategy:
      1. Try ``litellm/public/agent_hub`` without authentication (public endpoint).
      2. On any non-2xx response (including 500) or network error, fall back to
         ``litellm/v1/agents`` authenticated with the IAMDS LiteLLM API key
         (``IAMDS_LITELLM_API_KEY`` from ``~/.hermes/.env``).

    Returns (agents_list, error_message).
    """
    settings = settings or resolve_litellm_hub_settings()
    base_url = str(settings.get("base_url", "")).strip().rstrip("/")
    api_key = str(settings.get("api_key", "")).strip()
    timeout = settings.get("timeout", 20)

    if not base_url:
        return None, (
            "LiteLLM base_url is not configured. "
            "Set skills.litellm_hub.base_url in config.yaml."
        )

    # Normalise: strip /v1 suffix; keep /litellm suffix detection for URL building
    hub_base = base_url.rstrip("/")
    if hub_base.endswith("/v1"):
        hub_base = hub_base[:-3]
    litellm_prefix = "" if hub_base.endswith("/litellm") else "/litellm"

    # ── Step 1: try public endpoint (no API key) ──────────────────────────────
    public_url = f"{hub_base}{litellm_prefix}/public/agent_hub"
    logger.info("[LiteLLMHub] Trying public agent hub: %s", public_url)
    try:
        public_resp = httpx.get(public_url, timeout=timeout, follow_redirects=True)
        if public_resp.status_code == 200:
            agents = _parse_agents_response(public_resp.json())
            logger.info("[LiteLLMHub] Public agent hub returned %d agents", len(agents))
            return agents, None
        logger.warning(
            "[LiteLLMHub] Public agent hub returned HTTP %s, falling back to authenticated endpoint",
            public_resp.status_code,
        )
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("[LiteLLMHub] Public agent hub failed (%s), falling back", exc)

    # ── Step 2: fall back to authenticated /v1/agents ─────────────────────────
    authed_url = f"{hub_base}{litellm_prefix}/v1/agents"
    headers: Dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    logger.info("[LiteLLMHub] Fetching agents from authenticated endpoint: %s", authed_url)
    try:
        resp = httpx.get(authed_url, headers=headers or None, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        logger.error("[LiteLLMHub] Agents request failed for %s: %s", authed_url, exc)
        return None, f"Failed to reach LiteLLM agents endpoint at {authed_url}: {exc}"

    if resp.status_code == 401:
        return None, f"Unauthorized (401) at {authed_url}. Check API key."
    if resp.status_code == 403:
        return None, f"Forbidden (403) at {authed_url}. Check API key scope."
    if resp.status_code != 200:
        return None, f"LiteLLM agents request failed: HTTP {resp.status_code} from {authed_url}."

    try:
        data = resp.json()
    except ValueError:
        return None, f"LiteLLM agents endpoint returned non-JSON from {authed_url}."

    return _parse_agents_response(data), None


def get_active_agents() -> list[str]:
    """Return the list of agent names the user has activated via the Hub UI."""
    try:
        from hermes_cli.config import load_config
        cfg = load_config() or {}
        hub_cfg = cfg.get("skills", {}).get("litellm_hub", {})
        active = hub_cfg.get("active_agents", [])
        return [str(a) for a in active if a]
    except Exception:
        return []


def set_active_agents(agent_names: list[str]) -> None:
    """Persist the list of active agent names to config.yaml."""
    from hermes_cli.config import load_config, save_config
    cfg = load_config() or {}
    skills = cfg.setdefault("skills", {})
    hub = skills.setdefault("litellm_hub", {})
    hub["active_agents"] = sorted(set(agent_names))
    save_config(cfg)
