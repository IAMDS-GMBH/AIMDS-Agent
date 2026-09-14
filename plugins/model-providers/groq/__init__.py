"""Groq provider profile (OpenAI-compatible API, api.groq.com)."""

from providers import register_provider
from providers.base import ProviderProfile

groq = ProviderProfile(
    name="groq",
    aliases=("groqcloud",),
    display_name="Groq",
    description="Groq (OpenAI-compatible API, GROQ_API_KEY)",
    signup_url="https://console.groq.com/keys",
    env_vars=("GROQ_API_KEY",),
    base_url="https://api.groq.com/openai/v1",
    auth_type="api_key",
    # No curated fallback on purpose: the live ``/models`` listing (and the
    # models.dev merge — ``groq`` is in ``_MODELS_DEV_PREFERRED``) decides
    # what the key can actually reach, never a guess.
)

register_provider(groq)
