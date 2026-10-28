"""AIS-740: the compaction trigger is capped by an absolute prompt budget.

AIMDS-Suite-Auto advertises the largest routable window (200k); 75 % of it
compacted only at ~150k, while the Suite flags every request >= 100k.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent.context_compressor import (
    MIN_PROMPT_BUDGET_TOKENS,
    SUITE_PROMPT_BUDGET_TOKENS,
    ContextCompressor,
    resolve_prompt_budget,
)

SUITE_LARGE_PROMPT_TOKENS = 100_000


def _compressor(model: str, provider: str, context_length: int = 200_000, **kwargs) -> ContextCompressor:
    with patch("agent.context_compressor.get_model_context_length", return_value=context_length):
        return ContextCompressor(
            model=model,
            provider=provider,
            threshold_percent=0.75,
            quiet_mode=True,
            **kwargs,
        )


@pytest.mark.parametrize(
    "model, provider",
    [
        ("AIMDS-Suite-Auto", "aimds-suite-prod"),
        ("AIMDS-Suite-Auto", "aimds-suite-dev"),
        ("claude-sonnet-4.6", "aimds-suite-prod"),  # every model on a Suite key
        ("AIMDS-Suite-Auto", "custom"),  # the router model through any provider entry
        ("auto", "iamds-litellm"),  # legacy slug
    ],
)
def test_suite_class_compacts_well_below_the_large_prompt_mark(model, provider):
    c = _compressor(model, provider)
    assert c.prompt_budget_tokens == SUITE_PROMPT_BUDGET_TOKENS
    assert c.threshold_tokens == SUITE_PROMPT_BUDGET_TOKENS
    assert c.threshold_tokens < SUITE_LARGE_PROMPT_TOKENS
    assert c.should_compress(SUITE_PROMPT_BUDGET_TOKENS) is True
    assert c.should_compress(SUITE_PROMPT_BUDGET_TOKENS - 1) is False
    # The tail kept after compaction scales with the effective trigger.
    assert c.tail_token_budget == int(SUITE_PROMPT_BUDGET_TOKENS * c.summary_target_ratio)


def test_other_providers_keep_the_percentage_of_their_window():
    c = _compressor("claude-sonnet-4.6", "anthropic")
    assert c.prompt_budget_tokens is None
    assert c.threshold_tokens == 150_000


def test_budget_never_raises_a_lower_percentage_trigger():
    c = _compressor("AIMDS-Suite-Auto", "aimds-suite-prod", context_length=96_000)
    assert c.threshold_tokens == 72_000


@pytest.mark.parametrize(
    "setting, provider, expected",
    [
        ("auto", "aimds-suite-prod", SUITE_PROMPT_BUDGET_TOKENS),
        (None, "aimds-suite-prod", SUITE_PROMPT_BUDGET_TOKENS),
        ("auto", "openrouter", None),
        (90_000, "openrouter", 90_000),
        ("90000", "aimds-suite-prod", 90_000),
        (1_000, "openrouter", MIN_PROMPT_BUDGET_TOKENS),
        (0, "aimds-suite-prod", None),
        ("off", "aimds-suite-prod", None),
        ("garbage", "aimds-suite-prod", None),
    ],
)
def test_resolve_prompt_budget(setting, provider, expected):
    assert resolve_prompt_budget(setting, "AIMDS-Suite-Auto" if "suite" in provider else "x", provider) == expected


def test_explicit_budget_caps_every_model():
    c = _compressor("gpt-5.5", "openrouter", max_prompt_tokens=90_000)
    assert c.threshold_tokens == 90_000


def test_budget_off_restores_the_percentage_for_suite():
    c = _compressor("AIMDS-Suite-Auto", "aimds-suite-prod", max_prompt_tokens=0)
    assert c.threshold_tokens == 150_000


def test_model_switch_recomputes_the_class():
    c = _compressor("claude-sonnet-4.6", "anthropic")
    assert c.threshold_tokens == 150_000
    c.update_model("AIMDS-Suite-Auto", 200_000, provider="aimds-suite-prod")
    assert c.threshold_tokens == SUITE_PROMPT_BUDGET_TOKENS
    c.update_model("claude-sonnet-4.6", 200_000, provider="anthropic")
    assert c.threshold_tokens == 150_000
