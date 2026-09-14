"""Reduce a provider's model catalog to the newest version per model family.

Locally detected OAuth providers (Claude Code, GitHub Copilot, OpenAI Codex,
Gemini CLI) enumerate every generation their account can still reach —
``claude-opus-4-5-20251101`` next to ``claude-opus-5`` and so on. Every
model picker (desktop, web dashboard, TUI, ``hermes model``) should only
offer the models that matter today, without a hand-maintained allow-list
that goes stale on each release.

Three invariants drive the parsing:

1. A token made only of digits and dots (``5``, ``5.3``, ``4``) is a
   *version* token. The first run of consecutive version tokens forms the
   version tuple: ``claude-opus-4-8`` → ``(4, 8)``, ``gpt-5.3-codex`` →
   ``(5, 3)``, ``gemini-3.1-pro-preview`` → ``(3, 1)``.
2. An eight-digit token (``20251101``) is a *date snapshot*. It never
   contributes to the version — it only breaks ties between two ids of the
   same version, where an undated alias wins over a dated snapshot.
3. Every other token stays part of the *family key*, in order. ``preview``,
   ``fast``, ``mini``, ``codex``, ``spark``, ``flash``, ``lite`` and code
   names such as ``luna`` are therefore distinct families and never collapse
   into each other. Tokens like ``70b``, ``4o`` or ``o3`` are not pure
   version tokens and are treated as words, so unfamiliar naming schemes
   pass through untouched.

Ids without any version token (``auto``, ``o3``, ``claude-latest``) are kept
verbatim at their original relative position.

The reduction lives on the picker path only (``cached_provider_model_ids``).
``provider_model_ids`` stays the full discovery catalog because
``validate_requested_model`` uses it for fuzzy auto-correction; a reduced
catalog there would silently rewrite an explicitly configured older model.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

# Providers whose picker rows are reduced to the newest version per family.
# Aliases (``claude-code``, ``gemini-cli`` …) are resolved by
# ``hermes_cli.models.normalize_provider`` before this set is consulted.
PICKER_LATEST_ONLY_PROVIDERS: frozenset[str] = frozenset(
    {"anthropic", "copilot", "copilot-acp", "openai-codex", "google-gemini-cli"}
)

_VERSION_TOKEN = re.compile(r"^\d+(?:\.\d+)*$")
_DATE_TOKEN = re.compile(r"^\d{8}$")
_SPLIT = re.compile(r"[-_/]+")


def _parse_model_id(model_id: str) -> tuple[tuple[str, ...], Optional[tuple[int, ...]], int]:
    """Return ``(family, version, date)`` for a model id.

    ``family`` is the ordered tuple of non-version tokens, ``version`` the
    integer tuple from the first run of version tokens (``None`` when the id
    carries no version), ``date`` the last eight-digit snapshot token (``0``
    when absent).
    """
    tokens = [t for t in _SPLIT.split(model_id.strip().lower()) if t]
    family: list[str] = []
    version: Optional[list[int]] = None
    date = 0
    in_version_run = False
    for tok in tokens:
        if _DATE_TOKEN.match(tok):
            date = int(tok)
            in_version_run = False
            continue
        if _VERSION_TOKEN.match(tok):
            parts = [int(p) for p in tok.split(".")]
            if version is None:
                version = parts
                in_version_run = True
                continue
            if in_version_run:
                version.extend(parts)
                continue
            # A second, separate numeric run (e.g. a size suffix). Keep it in
            # the family key so unrelated variants are never collapsed.
        in_version_run = False
        family.append(tok)
    return tuple(family), (tuple(version) if version is not None else None), date


def latest_per_family(model_ids: Iterable[str]) -> list[str]:
    """Keep only the newest version of every model family.

    Families are emitted in the order they first appear in ``model_ids`` (so a
    curated-first catalog keeps its curated ordering), with the winning id
    substituted. Ids without a version are passed through at their original
    relative position. Case-insensitive duplicates are dropped, first wins.
    """
    unique: list[str] = []
    seen_lower: set[str] = set()
    for mid in model_ids:
        if not isinstance(mid, str):
            continue
        cleaned = mid.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        unique.append(cleaned)

    # slot -> either ("family", family_key) or ("passthrough", model_id)
    slots: list[tuple[str, object]] = []
    best: dict[tuple[str, ...], tuple[tuple, str]] = {}
    for index, mid in enumerate(unique):
        family, version, date = _parse_model_id(mid)
        if version is None:
            slots.append(("passthrough", mid))
            continue
        rank = (version, 1 if date == 0 else 0, date, -index)
        current = best.get(family)
        if current is None:
            slots.append(("family", family))
            best[family] = (rank, mid)
        elif rank > current[0]:
            best[family] = (rank, mid)

    result: list[str] = []
    for kind, payload in slots:
        if kind == "passthrough":
            result.append(payload)  # type: ignore[arg-type]
        else:
            result.append(best[payload][1])  # type: ignore[index]
    return result


def reduce_for_picker(provider: Optional[str], model_ids: Iterable[str]) -> list[str]:
    """Apply :func:`latest_per_family` for providers in ``PICKER_LATEST_ONLY_PROVIDERS``.

    Every other provider (AIMDS-Suite, OpenRouter, Nous, custom endpoints …)
    gets its list back unchanged.
    """
    ids = list(model_ids)
    if (provider or "").strip().lower() not in PICKER_LATEST_ONLY_PROVIDERS:
        return ids
    return latest_per_family(ids)
