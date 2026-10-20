"""Automatic model fallback for AIMDS Suite primaries (AIS-503).

A morning-brief cron aborted on ``HTTP 400 Invalid model name … vllm-custom``
because no ``fallback_providers`` were configured (SUP-20261005-071541). On an
AIMDS Suite provider Hermes now appends an automatic chain to the configured
one:

1. ``AIMDS-Suite-Auto`` on the same environment (same key, same host) — the
   Suite router is meant to be always available;
2. the cheapest tool-capable models the key lists (``/model/info`` prices,
   else the fast-model preference);
3. the user's configured ``fallback_providers``;
4. when those name no provider outside the Suite: the cheap default model of
   the first explicitly configured other provider.

Entries carry ``auto_fallback`` and are resolved at activation
(:func:`resolve_entry`) — no network at agent init, every model checked
against the key's own list. Never another Suite environment: Suite calls use
the active environment's key only.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

AUTO_KEY = "auto_fallback"
SUITE_AUTO = "suite-auto"
SUITE_CHEAP = "suite-cheap"
OTHER_PROVIDER = "other-provider"
#: How many cheap Suite models the chain tries after the router.
SUITE_CHEAP_SLOTS = 2
#: Other providers in the order the Accounts page promotes them; the rest of
#: the registry follows in its own order.
_OTHER_PROVIDER_ORDER = ("anthropic", "gemini", "openrouter", "groq")


def _is_suite(provider: Optional[str]) -> bool:
    try:
        from hermes_cli.iamds_suite import is_suite_provider

        return is_suite_provider(provider)
    except Exception:
        return False


def with_auto_fallbacks(provider: Optional[str], model: Optional[str], configured: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """*configured* plus the automatic entries for a Suite primary.

    Previous automatic entries are dropped first, so a model switch rebuilds
    them for the new primary. Non-Suite primaries keep the configured chain.
    """
    chain = [dict(e) for e in configured or [] if isinstance(e, dict) and not e.get(AUTO_KEY)]
    if not _is_suite(provider):
        return chain
    from hermes_cli.iamds_suite import SUITE_AUTO_MODEL, is_suite_auto_model

    auto: List[Dict[str, Any]] = []
    if not is_suite_auto_model(model):
        auto.append({"provider": provider, "model": SUITE_AUTO_MODEL, AUTO_KEY: SUITE_AUTO})
    auto += [{"provider": provider, "model": "", AUTO_KEY: SUITE_CHEAP, "slot": i} for i in range(SUITE_CHEAP_SLOTS)]
    tail: List[Dict[str, Any]] = []
    if not any(not _is_suite(e.get("provider")) for e in chain):
        tail.append({"provider": "", "model": "", AUTO_KEY: OTHER_PROVIDER})
    return auto + chain + tail


def _primary(agent: Any) -> Dict[str, Any]:
    runtime = getattr(agent, "_primary_runtime", None) or {}
    return {
        "provider": runtime.get("provider") or getattr(agent, "provider", ""),
        "model": runtime.get("model") or getattr(agent, "model", ""),
        "base_url": runtime.get("base_url") or getattr(agent, "base_url", ""),
        "api_key": runtime.get("api_key") or getattr(agent, "api_key", ""),
    }


def _key_models(provider: str) -> List[str]:
    try:
        from hermes_cli.models import cached_provider_model_ids

        return [str(m).strip() for m in (cached_provider_model_ids(provider) or []) if str(m).strip()]
    except Exception:
        return []


def suite_cheap_candidates(
    provider: str,
    *,
    exclude: List[str],
    base_url: str = "",
    api_key: str = "",
    available: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Dict[str, Any]]] = None,
) -> List[str]:
    """Cheap models of the key, cheapest first, without *exclude* and the router."""
    from hermes_cli.iamds_suite import is_suite_auto_model, preferred_fast_models, rank_models_by_cost

    available = list(available if available is not None else _key_models(provider))
    if metadata is None:
        try:
            from agent.model_metadata import fetch_endpoint_model_metadata

            metadata = fetch_endpoint_model_metadata(base_url, api_key=api_key) if base_url and isinstance(api_key, str) else {}
        except Exception:
            metadata = {}
    skip = {m.strip().lower() for m in exclude if m}
    by_lower = {m.lower(): m for m in available}
    ordered = rank_models_by_cost(available, metadata or {}) + preferred_fast_models(list(by_lower.values()))
    out: List[str] = []
    for model in ordered:
        if model.lower() in skip or is_suite_auto_model(model) or model in out:
            continue
        out.append(model)
    return out


def _other_provider() -> Optional[Dict[str, Any]]:
    """Cheap default model of the first explicitly configured non-Suite provider."""
    try:
        from agent.auxiliary_client import _OPENROUTER_MODEL, _get_aux_model_for_provider, _is_provider_unhealthy
        from hermes_cli.auth import PROVIDER_REGISTRY, is_provider_explicitly_configured
    except Exception:
        return None
    order = list(_OTHER_PROVIDER_ORDER) + [p for p in PROVIDER_REGISTRY if p not in _OTHER_PROVIDER_ORDER]
    for provider_id in order:
        if _is_suite(provider_id) or provider_id in ("openai-api", "lmstudio"):
            continue  # openai-api is how legacy installs reach the Suite; lmstudio is a local no-auth server
        if _is_provider_unhealthy(provider_id):
            continue
        try:
            if not is_provider_explicitly_configured(provider_id):
                continue
        except Exception:
            continue
        model = _OPENROUTER_MODEL if provider_id == "openrouter" else _get_aux_model_for_provider(provider_id)
        if model:
            return {"provider": provider_id, "model": model}
    return None


#: Failures that come from the request itself — another model gets the same
#: request and fails the same way, so automatic entries are not tried.
_REQUEST_BOUND_REASONS = frozenset({"format_error", "context_overflow", "payload_too_large", "image_too_large"})

#: Failures of the endpoint's configuration — a rejected key, a URL that
#: redirects. Every model behind the same key and host fails the same way, so
#: only entries with their own endpoint or credential are tried (AIS-599).
CONFIG_BOUND_REASONS = frozenset({"auth", "auth_permanent", "endpoint_redirect"})


def _reason_value(reason: Any) -> str:
    return str(getattr(reason, "value", reason) or "")


def _endpoint_id(provider: Any, base_url: Any) -> tuple:
    """(canonical provider, normalized base URL) — Suite legacy slugs fold onto their environment."""
    provider = str(provider or "").strip().lower()
    try:
        from hermes_cli.iamds_suite import canonical_suite_provider

        provider = canonical_suite_provider(provider) or provider
    except Exception:
        pass
    return provider, str(base_url or "").strip().rstrip("/").lower()


def shares_failing_endpoint(agent: Any, entry: Dict[str, Any], reason: Any) -> bool:
    """Whether *entry* would reuse the key/URL that just failed for a configuration reason.

    An entry with its own ``api_key``/``key_env`` is a different credential and
    stays eligible; otherwise the same provider or the same base URL means the
    same endpoint (AIS-599: a revoked Suite key failed through every model).
    """
    if _reason_value(reason) not in CONFIG_BOUND_REASONS:
        return False
    if entry.get(AUTO_KEY) in (SUITE_AUTO, SUITE_CHEAP):
        return True
    if any(str(entry.get(k) or "").strip() for k in ("api_key", "key_env", "api_key_env")):
        return False
    cur_provider, cur_url = _endpoint_id(getattr(agent, "provider", ""), getattr(agent, "base_url", ""))
    fb_provider, fb_url = _endpoint_id(entry.get("provider"), entry.get("base_url"))
    if fb_url and cur_url:
        return fb_url == cur_url
    return bool(fb_provider) and fb_provider == cur_provider


def resolve_entry(agent: Any, entry: Dict[str, Any], *, reason: Any = None) -> Optional[Dict[str, Any]]:
    """A concrete chain entry for an automatic one, or None to skip it."""
    kind = entry.get(AUTO_KEY)
    if kind and _reason_value(reason) in _REQUEST_BOUND_REASONS:
        return None
    if kind and shares_failing_endpoint(agent, entry, reason):
        logger.info("[AIS-599] %s skipped: %s on the same endpoint", kind, _reason_value(reason))
        return None
    primary = _primary(agent)
    provider = str(entry.get("provider") or primary["provider"] or "")
    tried = [primary["model"], getattr(agent, "model", "")]
    if kind in (SUITE_AUTO, SUITE_CHEAP):
        same_env = {"provider": provider, "base_url": primary["base_url"]}
        if isinstance(primary["api_key"], str) and primary["api_key"]:
            same_env["api_key"] = primary["api_key"]
        if kind == SUITE_AUTO:
            listed = _key_models(provider)
            if listed and entry["model"].lower() not in {m.lower() for m in listed}:
                logger.info("[AIS-503] %s is not offered to this key on %s — skipping", entry["model"], provider)
                return None
            return {**same_env, "model": entry["model"], AUTO_KEY: kind}
        candidates = suite_cheap_candidates(
            provider, exclude=tried, base_url=str(primary["base_url"] or ""), api_key=primary["api_key"],
        )
        slot = int(entry.get("slot") or 0)
        if slot >= len(candidates):
            return None
        return {**same_env, "model": candidates[slot], AUTO_KEY: kind}
    if kind == OTHER_PROVIDER:
        other = _other_provider()
        return {**other, AUTO_KEY: kind} if other else None
    return entry


def report(agent: Any, old_model: str, entry: Dict[str, Any], reason: str = "") -> None:
    """Auto incident for an automatic fallback (deduplicated per 24 h by kind)."""
    if not entry.get(AUTO_KEY):
        return
    primary = _primary(agent)
    try:
        from hermes_cli.iamds_suite import suite_reauth_pending

        if suite_reauth_pending(primary["provider"]):
            # AIS-599: the lost login already has its one case (auth-401);
            # every cron run would otherwise add a fallback report.
            return
    except Exception:
        pass
    try:
        from hermes_cli.auto_incidents import _slug, report_in_background

        report_in_background(
            f"model-fallback-{_slug(primary['provider'])}-{_slug(old_model)}",
            f"Model fallback: {old_model} ({primary['provider']}) → {entry['model']} ({entry['provider']})",
            f"The primary model failed{f' ({reason})' if reason else ''}; Hermes switched automatically "
            f"({entry[AUTO_KEY]}). The user saw a status notice.",
            category="connection_error",
            context_type="model_fallback",
            severity="medium",
            session_id=str(getattr(agent, "session_id", "") or ""),
        )
    except Exception as exc:
        logger.debug("[AIS-503] fallback incident not reported: %s", exc)
