"""Tests for hermes_cli.m365_auth MSAL token cache and PublicClientApplication helper."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_cli.m365_auth import (
    get_m365_token_cache_path,
    get_msal_app,
    save_msal_cache,
)


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
