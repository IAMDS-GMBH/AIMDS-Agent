"""AIMDS-Suite gateway provider profile."""

from providers import register_provider
from providers.base import ProviderProfile

iamds_litellm = ProviderProfile(
    name="iamds-litellm",
    aliases=("iamds", "iamds_litellm", "aimds", "aimds_suite"),
    api_mode="codex_responses",
    env_vars=("IAMDS_LITELLM_API_KEY",),
    base_url="",  # Configured via IAMDS_LITELLM_BASE_URL (fallback: OPENAI_BASE_URL)
    display_name="AIMDS-Suite",
    description="AIMDS-Suite gateway (OpenAI-compatible API)",
    auth_type="api_key",
)

register_provider(iamds_litellm)
