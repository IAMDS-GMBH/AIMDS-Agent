"""Tests for hermes_cli.m365_auth MSAL token cache and PublicClientApplication helper."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.m365_auth import (
    get_m365_token_cache_path,
    get_msal_app,
    has_valid_msal_cache,
    save_msal_cache,
)


def test_has_valid_msal_cache_returns_true_when_accounts_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch("hermes_cli.m365_auth.get_msal_app") as mock_get_app:
        mock_app = MagicMock()
        mock_app.get_accounts.return_value = [{"username": "user@contoso.com"}]
        mock_get_app.return_value = mock_app

        assert has_valid_msal_cache() is True


def test_has_valid_msal_cache_returns_false_when_no_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    with patch("hermes_cli.m365_auth.get_msal_app") as mock_get_app:
        mock_app = MagicMock()
        mock_app.get_accounts.return_value = []
        mock_get_app.return_value = mock_app

        assert has_valid_msal_cache() is False


def test_get_m365_token_cache_path_respects_hermes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    path = get_m365_token_cache_path()
    assert path == tmp_path / "m365_token_cache.bin"
    assert tmp_path.exists()


def test_get_msal_app_deserializes_existing_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cache_path = tmp_path / "m365_token_cache.bin"
    dummy_cache = {"Account": {"acc1": {"realm": "organizations"}}}
    cache_path.write_text(json.dumps(dummy_cache), encoding="utf-8")

    app = get_msal_app()
    assert app is not None
    assert app.client_id == "41c29967-8ee6-4fac-b484-e87460272bda"


def test_save_msal_cache_is_atomic(tmp_path):
    cache_path = tmp_path / "m365_token_cache.bin"
    mock_app = MagicMock()
    mock_app.token_cache.has_state_changed = True
    mock_app.token_cache.serialize.return_value = '{"Account": {"test": 1}}'

    save_msal_cache(mock_app, cache_path=cache_path)

    assert cache_path.read_text(encoding="utf-8") == '{"Account": {"test": 1}}'
    assert not list(tmp_path.glob("*.tmp.*"))


# --------------------------------------------------------------------------- AIS-286

from hermes_cli.m365_auth import (  # noqa: E402
    M365_ALL_SCOPES,
    M365_LOGIN_SCOPES,
    M365_SELF_CONSENT_SCOPES,
    M365_STANDARD_SCOPES,
    build_admin_consent_url,
    classify_m365_auth_error,
    m365_scopes_for_tier,
    m365_tier_for_endpoint,
    resolve_m365_tenant_id,
)


def test_scope_tiers_nest_and_login_default_is_self():
    assert M365_LOGIN_SCOPES == M365_SELF_CONSENT_SCOPES
    assert set(M365_SELF_CONSENT_SCOPES) < set(M365_STANDARD_SCOPES) < set(M365_ALL_SCOPES)
    assert m365_scopes_for_tier("admin") == M365_ALL_SCOPES
    assert m365_scopes_for_tier("STANDARD") == M365_STANDARD_SCOPES
    assert m365_scopes_for_tier(None) == M365_LOGIN_SCOPES
    assert m365_scopes_for_tier("typo") == M365_LOGIN_SCOPES  # never escalates


def test_tier_for_endpoint():
    assert m365_tier_for_endpoint("/me/messages") == "self"
    assert m365_tier_for_endpoint("/me/chats/1/messages") == "standard"
    assert m365_tier_for_endpoint("/users/shared@example.com/mailFolders") == "standard"
    assert m365_tier_for_endpoint("/users") == "admin"
    assert m365_tier_for_endpoint("/sites/root") == "admin"


@pytest.mark.parametrize(
    ("payload", "code", "category", "consent"),
    [
        ({"error": "invalid_grant", "error_description": "AADSTS90094: Need admin approval"}, "AADSTS90094", "consent", True),
        ("AADSTS65001: The user or administrator has not consented", "AADSTS65001", "consent", True),
        ({"error": "consent_required"}, "consent_required", "consent", True),
        ({"error": "interaction_required"}, "interaction_required", "consent", True),
        ({"error": "authorization_declined"}, "authorization_declined", "declined", False),
        ({"error": "expired_token"}, "expired_token", "expired", False),
        # Unknown AADSTS code + known OAuth error → the OAuth classification wins.
        ({"error": "invalid_grant", "error_description": "AADSTS50173 token revoked"}, "invalid_grant", "expired", False),
        ("AADSTS99999: something new", "AADSTS99999", "unknown", False),
        ("AADSTS7000218: public client flows disabled", "AADSTS7000218", "config", False),
        ("AADSTS50076: MFA required", "AADSTS50076", "mfa", False),
        ("Need admin approval — contact your administrator", "consent_required", "consent", True),
        ("", "", "unknown", False),
    ],
)
def test_classify_m365_auth_error(payload, code, category, consent):
    err = classify_m365_auth_error(payload)
    assert (err.code, err.category, err.admin_consent_required) == (code, category, consent)
    assert err.message
    assert err.to_dict()["error_code"] == code


def test_resolve_tenant_prefers_env_then_cache_realm_then_organizations(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for var in ("M365_TENANT_ID", "OUTLOOK_TENANT_ID", "TEAMS_TENANT_ID"):
        monkeypatch.delenv(var, raising=False)
    assert resolve_m365_tenant_id() == "organizations"
    (tmp_path / "m365_token_cache.bin").write_text(
        '{"Account": {"a": {"realm": "11111111-2222-3333-4444-555555555555"}}}', encoding="utf-8"
    )
    assert resolve_m365_tenant_id() == "11111111-2222-3333-4444-555555555555"
    monkeypatch.setenv("M365_TENANT_ID", "common")
    assert resolve_m365_tenant_id() == "11111111-2222-3333-4444-555555555555"  # common is never used
    monkeypatch.setenv("M365_TENANT_ID", "contoso.onmicrosoft.com")
    assert resolve_m365_tenant_id() == "contoso.onmicrosoft.com"


def test_build_admin_consent_url_fully_qualifies_scopes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for var in ("M365_CLIENT_ID", "OUTLOOK_CLIENT_ID", "TEAMS_CLIENT_ID", "M365_TENANT_ID", "OUTLOOK_TENANT_ID", "TEAMS_TENANT_ID"):
        monkeypatch.delenv(var, raising=False)
    url = build_admin_consent_url(scopes=["User.Read", "https://graph.microsoft.com/Chat.ReadWrite"], state="abc")
    assert url.startswith("https://login.microsoftonline.com/organizations/v2.0/adminconsent?client_id=41c29967-8ee6-4fac-b484-e87460272bda")
    assert "scope=https%3A%2F%2Fgraph.microsoft.com%2FUser.Read%20https%3A%2F%2Fgraph.microsoft.com%2FChat.ReadWrite" in url
    assert "redirect_uri=http%3A%2F%2Flocalhost%3A8400" in url and url.endswith("&state=abc")
    assert "graph.microsoft.com%2F.default" in build_admin_consent_url(use_default_scope=True)
    assert "/t-1/v2.0/" in build_admin_consent_url(tenant_id="t-1", client_id="c-1")
