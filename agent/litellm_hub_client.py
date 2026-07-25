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

    Resolution order for base_url:
    1. skills.litellm_hub.base_url (explicit hub override)
    2. LITELLM_PROXY_URL env var
    3. OPENAI_BASE_URL env var
    4. First provider entry with a base_url (the model provider is usually LiteLLM)
    """
    from hermes_cli.config import load_config, load_env

    cfg = load_config() or {}
    skills_cfg = cfg.get("skills", {}) if isinstance(cfg, dict) else {}
    hub_cfg = skills_cfg.get("litellm_hub", {}) if isinstance(skills_cfg, dict) else {}

    base_url = str(
        hub_cfg.get("base_url")
        or os.getenv("LITELLM_PROXY_URL")
        or os.getenv("OPENAI_BASE_URL")
        or _first_provider_base_url(cfg)
        or ""
    ).strip().rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    # Prefer IAMDS/LiteLLM runtime secrets from ~/.hermes/.env (freshly read)
    # over a potentially stale process env or persisted config key.
    env_vars = load_env()
    # Prefer IAMDS/LiteLLM runtime secrets from env/.env over a potentially
    # stale key persisted in config.yaml.
    api_key = str(
        env_vars.get("IAMDS_LITELLM_API_KEY")
        or env_vars.get("LITELLM_KEY")
        or os.getenv("IAMDS_LITELLM_API_KEY")
        or os.getenv("LITELLM_KEY")
        or hub_cfg.get("api_key")
        or ""
    ).strip()
    if not api_key:
        try:
            from hermes_cli.auth import resolve_api_key_provider_credentials

            for p_id in ("aimds-suite-prod", "aimds-suite-dev", "aimds-suite-staging", "iamds-litellm", "iamds-litellm-staging", "iamds-litellm-dev"):
                creds = resolve_api_key_provider_credentials(p_id)
                if isinstance(creds, dict) and creds.get("api_key"):
                    api_key = str(creds["api_key"]).strip()
                    break
        except Exception:
            pass
    if not api_key:
        try:
            from hermes_cli.auth import resolve_api_key_provider_credentials

            for provider_id in ("aimds-suite-prod", "aimds-suite-dev", "aimds-suite-staging", "iamds-litellm", "iamds-litellm-staging", "iamds-litellm-dev"):
                creds = resolve_api_key_provider_credentials(provider_id)
                if isinstance(creds, dict) and creds.get("api_key"):
                    api_key = str(creds["api_key"]).strip()
                    break
        except Exception:
            pass
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
