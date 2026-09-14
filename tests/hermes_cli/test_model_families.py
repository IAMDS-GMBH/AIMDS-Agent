"""Newest-version-per-family reduction for locally detected OAuth providers (AIS-325).

Locally detected providers (Claude Code, Copilot, Codex, Gemini CLI) enumerate
every generation the account can still reach, and the Anthropic picker
additionally merged the stale curated list in front of the live catalog — 13
Claude entries for four families. The picker path
(``cached_provider_model_ids``) now keeps only the newest version per family.
``provider_model_ids`` stays untouched because model validation fuzzy-matches
against it.
"""

import json
import time

import pytest

from hermes_cli import models as M
from hermes_cli.model_families import (
    PICKER_LATEST_ONLY_PROVIDERS,
    _parse_model_id,
    latest_per_family,
    reduce_for_picker,
)

# Real catalogs observed on 2026-09-14.
ANTHROPIC_MERGED = [
    "claude-fable-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-opus-4-5-20251101",
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-20250514",
    "claude-sonnet-4-20250514",
    "claude-haiku-4-5-20251001",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-fable-5-1",
]
COPILOT_LIVE = [
    "claude-opus-4.8-fast",
    "claude-opus-5",
    "claude-sonnet-5",
    "gpt-5.3-codex",
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
    "grok-4.6",
    "mai-code-1.1-flash",
    "gpt-5-mini",
    "claude-haiku-4.5",
]
CODEX_CURATED = ["gpt-5.5", "gpt-5.4-mini", "gpt-5.4", "gpt-5.3-codex", "gpt-5.3-codex-spark"]
GEMINI_CLI_CURATED = [
    "gemini-3.1-pro-preview",
    "gemini-3-pro-preview",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
]


def test_anthropic_reduces_to_newest_per_family_in_curated_order():
    assert latest_per_family(ANTHROPIC_MERGED) == [
        "claude-fable-5-1",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5-20251001",
    ]


def test_copilot_live_catalog_is_already_one_per_family():
    assert latest_per_family(COPILOT_LIVE) == COPILOT_LIVE


def test_codex_drops_older_plain_gpt_but_keeps_variants():
    assert latest_per_family(CODEX_CURATED) == [
        "gpt-5.5",
        "gpt-5.4-mini",
        "gpt-5.3-codex",
        "gpt-5.3-codex-spark",
    ]


def test_gemini_cli_keeps_both_flash_access_gates():
    assert latest_per_family(GEMINI_CLI_CURATED) == [
        "gemini-3.1-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.5-flash",
    ]


@pytest.mark.parametrize(
    "model_id, family, version, date",
    [
        ("claude-opus-4-20250514", ("claude", "opus"), (4,), 20250514),
        ("claude-opus-4-5-20251101", ("claude", "opus"), (4, 5), 20251101),
        ("claude-3-5-sonnet-20241022", ("claude", "sonnet"), (3, 5), 20241022),
        ("gpt-5.3-codex-spark", ("gpt", "codex", "spark"), (5, 3), 0),
        ("gemini-3.1-pro-preview", ("gemini", "pro", "preview"), (3, 1), 0),
        ("mai-code-1.1-flash", ("mai", "code", "flash"), (1, 1), 0),
        ("llama-3.3-70b-versatile", ("llama", "70b", "versatile"), (3, 3), 0),
        ("o3", ("o3",), None, 0),
        ("auto", ("auto",), None, 0),
    ],
)
def test_parse_model_id(model_id, family, version, date):
    assert _parse_model_id(model_id) == (family, version, date)


def test_dated_minor_beats_undated_major_only_when_version_is_higher():
    # (4,) with a date loses to (4, 5): the date never counts as a version.
    assert latest_per_family(["claude-opus-4-20250514", "claude-opus-4-5-20251101"]) == [
        "claude-opus-4-5-20251101"
    ]


def test_old_naming_scheme_loses_to_new_generation():
    assert latest_per_family(["claude-3-5-sonnet-20241022", "claude-sonnet-5"]) == ["claude-sonnet-5"]


def test_minor_version_wins_within_variant_family():
    assert latest_per_family(["gpt-5-mini", "gpt-5.4-mini"]) == ["gpt-5.4-mini"]


def test_undated_alias_beats_dated_snapshot_of_same_version():
    assert latest_per_family(["claude-sonnet-4-5-20250929", "claude-sonnet-4-5"]) == ["claude-sonnet-4-5"]


def test_newer_date_wins_between_two_snapshots():
    assert latest_per_family(["claude-sonnet-4-5-20250929", "claude-sonnet-4-5-20251201"]) == [
        "claude-sonnet-4-5-20251201"
    ]


def test_versionless_ids_pass_through_at_their_position():
    assert latest_per_family(["auto", "claude-opus-4-8", "o3", "claude-opus-5"]) == [
        "auto",
        "claude-opus-5",
        "o3",
    ]


def test_dedupes_case_insensitively_first_wins():
    assert latest_per_family(["Claude-Opus-5", "claude-opus-5", "claude-opus-4-8"]) == ["Claude-Opus-5"]


def test_idempotent_and_empty():
    once = latest_per_family(ANTHROPIC_MERGED)
    assert latest_per_family(once) == once
    assert latest_per_family([]) == []
    assert latest_per_family(["", "  "]) == []


@pytest.mark.parametrize("provider", ["iamds-litellm", "aimds-suite-prod", "openai-api", "nous", "", None])
def test_reduce_for_picker_is_identity_outside_local_oauth_providers(provider):
    ids = ["AIMDS-Suite-Auto", "claude-opus-4.8", "claude-sonnet-5", "claude-haiku-4.5"]
    assert reduce_for_picker(provider, ids) == ids


@pytest.mark.parametrize("provider", sorted(PICKER_LATEST_ONLY_PROVIDERS))
def test_reduce_for_picker_applies_to_local_oauth_providers(provider):
    assert reduce_for_picker(provider, ["claude-opus-4-8", "claude-opus-5"]) == ["claude-opus-5"]


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "provider_models_cache.json"
    monkeypatch.setattr(M, "_provider_models_cache_path", lambda: cache_path)
    monkeypatch.setattr(M, "_credential_fingerprint", lambda _provider: "fp-test")
    return cache_path


def test_cached_picker_path_reduces_but_cache_file_keeps_raw_catalog(isolated_cache, monkeypatch):
    monkeypatch.setattr(M, "provider_model_ids", lambda provider, force_refresh=False: list(ANTHROPIC_MERGED))

    picker = M.cached_provider_model_ids("anthropic", force_refresh=True)

    assert picker == ["claude-fable-5-1", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"]
    stored = json.loads(isolated_cache.read_text())
    assert stored["anthropic"]["models"] == ANTHROPIC_MERGED


def test_cached_picker_path_reduces_existing_long_cache_entry_without_rewrite(isolated_cache, monkeypatch):
    isolated_cache.write_text(
        json.dumps({"anthropic": {"fp": "fp-test", "at": time.time(), "models": ANTHROPIC_MERGED}})
    )
    before = isolated_cache.read_text()

    def _boom(*_a, **_k):  # cache hit must not reach the live path
        raise AssertionError("live fetch must not run on a fresh cache hit")

    monkeypatch.setattr(M, "provider_model_ids", _boom)

    assert M.cached_provider_model_ids("claude-code") == [
        "claude-fable-5-1",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5-20251001",
    ]
    assert isolated_cache.read_text() == before


def test_cached_picker_path_reduces_stale_fallback_entry(isolated_cache, monkeypatch):
    isolated_cache.write_text(
        json.dumps({"anthropic": {"fp": "fp-test", "at": 0, "models": ANTHROPIC_MERGED}})
    )
    monkeypatch.setattr(M, "provider_model_ids", lambda provider, force_refresh=False: [])

    assert M.cached_provider_model_ids("anthropic", ttl_seconds=1) == [
        "claude-fable-5-1",
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5-20251001",
    ]


def test_validation_catalog_stays_unreduced(monkeypatch):
    live = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-opus-5", "claude-sonnet-5"]
    monkeypatch.setattr(M, "_fetch_anthropic_models", lambda *a, **k: list(live))

    full = M.provider_model_ids("anthropic")

    assert "claude-opus-4-8" in full and "claude-opus-5" in full
    assert len(full) > len(latest_per_family(full))


def test_fingerprint_folds_gemini_oauth_store(tmp_path, monkeypatch):
    import hermes_constants

    monkeypatch.setattr(hermes_constants, "get_hermes_home", lambda: tmp_path)
    (tmp_path / "auth").mkdir()
    before = M._credential_fingerprint("google-gemini-cli")
    (tmp_path / "auth" / "google_oauth.json").write_text("{}")
    after = M._credential_fingerprint("google-gemini-cli")

    assert before != after
