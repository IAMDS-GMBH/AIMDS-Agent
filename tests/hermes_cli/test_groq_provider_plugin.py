"""Groq is a first-class API-key provider (AIS-325: "available providers" on the accounts page)."""

from hermes_cli.auth import PROVIDER_REGISTRY
from hermes_cli.models import CANONICAL_PROVIDERS, normalize_provider


def test_groq_registered_from_plugin():
    cfg = PROVIDER_REGISTRY["groq"]
    assert cfg.auth_type == "api_key"
    assert "GROQ_API_KEY" in cfg.api_key_env_vars
    assert cfg.inference_base_url == "https://api.groq.com/openai/v1"


def test_groq_is_a_canonical_picker_provider():
    entry = next(p for p in CANONICAL_PROVIDERS if p.slug == "groq")
    assert entry.label == "Groq"
    assert normalize_provider("groq") == "groq"


def test_groq_skeleton_row_carries_key_env(monkeypatch, tmp_path):
    """Without a key the picker shows a setup row pointing at GROQ_API_KEY."""
    from hermes_cli import inventory

    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    rows = [{"slug": "openrouter", "name": "OpenRouter", "is_current": True, "is_user_defined": False,
             "models": ["x"], "total_models": 1, "source": "hermes"}]
    ctx = inventory.ConfigContext(
        current_provider="openrouter", current_model="x", current_base_url="", user_providers={}, custom_providers=[]
    )
    extras = inventory._append_unconfigured_rows(rows, ctx)
    inventory._apply_picker_hints(extras)
    groq = next(r for r in extras if r["slug"] == "groq")
    assert groq["authenticated"] is False
    assert groq["key_env"] == "GROQ_API_KEY"
