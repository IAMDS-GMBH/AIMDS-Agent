import os
from unittest.mock import patch
import pytest

from hermes_cli.providers import _LABEL_OVERRIDES
from hermes_cli.auth import PROVIDER_REGISTRY, _resolve_api_key_provider_secret
from utils import is_dev_environment
from agent.model_metadata import _resolve_requests_verify
from tools.mcp_tool import _resolve_mcp_ssl_verify


def test_aimds_suite_localdev_provider_registration():
    """Verify aimds-suite-localdev provider overlay and registry configuration."""
    assert "aimds-suite-localdev" in _LABEL_OVERRIDES
    assert _LABEL_OVERRIDES["aimds-suite-localdev"] == "AIMDS-Suite (Local Dev)"
    
    assert "aimds-suite-localdev" in PROVIDER_REGISTRY
    localdev_info = PROVIDER_REGISTRY["aimds-suite-localdev"]
    assert localdev_info.base_url_env_var == "IAMDS_LITELLM_LOCALDEV_BASE_URL"
    assert localdev_info.inference_base_url == "http://localhost:8000/litellm/v1"


def test_aimds_suite_localdev_secret_resolution(monkeypatch):
    """Verify secret resolution falls back to localdev API key env vars."""
    monkeypatch.setenv("IAMDS_LITELLM_LOCALDEV_API_KEY", "localdev-secret-key")
    pconfig = PROVIDER_REGISTRY["aimds-suite-localdev"]
    secret, env_var = _resolve_api_key_provider_secret("aimds-suite-localdev", pconfig)
    assert secret == "localdev-secret-key"
    assert env_var == "IAMDS_LITELLM_LOCALDEV_API_KEY"


def test_is_dev_environment_localdev():
    """Verify is_dev_environment identifies localdev provider and local loopback hosts."""
    assert is_dev_environment(provider_id="aimds-suite-localdev") is True
    assert is_dev_environment("http://localhost:8000/v1") is True
    assert is_dev_environment("https://127.0.0.1:8000/v1") is True
    assert is_dev_environment("http://local.suite.iamds.com/litellm/v1") is True


def test_ssl_verify_bypass_env(monkeypatch):
    """Verify HERMES_SSL_VERIFY=false bypasses SSL verification for requests and MCP."""
    monkeypatch.setenv("HERMES_SSL_VERIFY", "false")
    assert _resolve_requests_verify() is False
    assert _resolve_mcp_ssl_verify({}) is False

    monkeypatch.setenv("HERMES_SSL_VERIFY", "0")
    assert _resolve_requests_verify() is False
    assert _resolve_mcp_ssl_verify({}) is False

    monkeypatch.setenv("HERMES_SSL_VERIFY", "true")
    assert _resolve_requests_verify() is True
    assert _resolve_mcp_ssl_verify({}) is True
