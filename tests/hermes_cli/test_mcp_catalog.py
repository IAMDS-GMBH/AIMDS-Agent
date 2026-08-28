"""Tests for hermes_cli.mcp_catalog and hermes_cli.mcp_picker.

Manifest parsing, install/uninstall config writes, and picker plumbing
are exercised here. Anything that would actually clone a repo or
launch an MCP is mocked.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _default_mock_probe(monkeypatch):
    """By default tests run the probe-fails path so install_entry() doesn\'t
    try to talk to a real MCP server.

    Individual tests that exercise probe-success behaviour patch
    ``hermes_cli.mcp_catalog._probe_tools`` themselves.
    """
    # Patch the catalog\'s probe wrapper, not the underlying
    # mcp_config._probe_single_server (so tests stay decoupled from that
    # module\'s plumbing).
    import hermes_cli.mcp_catalog as mc

    monkeypatch.setattr(mc, "_probe_tools", lambda name: None)


@pytest.fixture
def catalog_dir(tmp_path, monkeypatch):
    """Provide an isolated optional-mcps/ directory."""
    cat = tmp_path / "optional-mcps"
    cat.mkdir()
    monkeypatch.setenv("HERMES_OPTIONAL_MCPS", str(cat))
    return cat


@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    """Redirect all config I/O to a temp HERMES_HOME."""
    hh = tmp_path / "hermes-home"
    hh.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hh))
    monkeypatch.setattr(
        "hermes_cli.config.get_hermes_home", lambda: hh
    )
    monkeypatch.setattr(
        "hermes_cli.config.get_config_path", lambda: hh / "config.yaml"
    )
    monkeypatch.setattr(
        "hermes_cli.config.get_env_path", lambda: hh / ".env"
    )
    # mcp_catalog grabs get_hermes_home() lazily through hermes_constants
    monkeypatch.setattr(
        "hermes_constants.get_hermes_home", lambda: hh
    )
    return hh


def _write_manifest(catalog_dir: Path, name: str, body: dict) -> Path:
    entry_dir = catalog_dir / name
    entry_dir.mkdir(exist_ok=True)
    path = entry_dir / "manifest.yaml"
    with open(path, "w") as f:
        yaml.safe_dump(body, f)
    return path


def _basic_manifest(name: str = "demo", **overrides) -> dict:
    body = {
        "manifest_version": 1,
        "name": name,
        "description": "Demo MCP",
        "source": "https://example.com",
        "transport": {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "demo-mcp"],
        },
        "auth": {"type": "none"},
    }
    body.update(overrides)
    return body


def _entry(name: str):
    """Wrapper that asserts entry exists (satisfies type-checker + nicer failure msg)."""
    from hermes_cli.mcp_catalog import get_entry

    e = get_entry(name)
    assert e is not None, f"catalog entry {name!r} missing"
    return e



# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


class TestManifestParsing:
    def test_minimal_valid(self, catalog_dir):
        _write_manifest(catalog_dir, "demo", _basic_manifest())
        from hermes_cli.mcp_catalog import list_catalog

        entries = list_catalog()
        assert len(entries) == 1
        e = entries[0]
        assert e.name == "demo"
        assert e.transport.type == "stdio"
        assert e.transport.command == "npx"
        assert e.transport.args == ["-y", "demo-mcp"]
        assert e.auth.type == "none"
        assert e.install is None

    def test_api_key_auth(self, catalog_dir):
        body = _basic_manifest(
            auth={
                "type": "api_key",
                "env": [
                    {"name": "DEMO_KEY", "prompt": "API key", "secret": True},
                    {"name": "DEMO_URL", "prompt": "Base URL", "secret": False, "required": False},
                ],
            }
        )
        _write_manifest(catalog_dir, "demo", body)
        from hermes_cli.mcp_catalog import list_catalog

        e = list_catalog()[0]
        assert e.auth.type == "api_key"
        assert len(e.auth.env) == 2
        assert e.auth.env[0].name == "DEMO_KEY"
        assert e.auth.env[0].secret is True
        assert e.auth.env[1].required is False
        assert e.auth.env[1].secret is False

    def test_auth_notes_optional(self, catalog_dir):
        """auth.notes is an optional free-text clarification for the setup
        dialog (e.g. "choose Cloud OR Server auth, not both"); absent when
        the manifest doesn't declare one, and stripped when present."""
        _write_manifest(catalog_dir, "demo", _basic_manifest(auth={"type": "none"}))
        from hermes_cli.mcp_catalog import list_catalog

        assert list_catalog()[0].auth.notes is None

        _write_manifest(
            catalog_dir,
            "demo2",
            _basic_manifest(
                name="demo2",
                auth={"type": "api_key", "env": [], "notes": "  Pick one auth mode.  \n"},
            ),
        )
        entries = {e.name: e for e in list_catalog()}
        assert entries["demo2"].auth.notes == "Pick one auth mode."

    def test_http_transport_headers_parsed(self, catalog_dir):
        """transport.headers carries static feature-flag/toolset-selection
        headers (e.g. GithubMCP's X-MCP-Toolsets) that must survive
        manifest parsing untouched -- they aren't auth-related and aren't
        collected from user input."""
        body = _basic_manifest(
            transport={
                "type": "http",
                "url": "https://api.example.com/mcp/",
                "headers": {"X-MCP-Toolsets": "context,repos,actions"},
            },
            auth={"type": "none"},
        )
        _write_manifest(catalog_dir, "demo", body)
        from hermes_cli.mcp_catalog import list_catalog

        e = list_catalog()[0]
        assert e.transport.headers == {"X-MCP-Toolsets": "context,repos,actions"}

    def test_http_transport_headers_default_empty(self, catalog_dir):
        _write_manifest(
            catalog_dir,
            "demo",
            _basic_manifest(
                transport={"type": "http", "url": "https://api.example.com/mcp/"},
                auth={"type": "none"},
            ),
        )
        from hermes_cli.mcp_catalog import list_catalog

        assert list_catalog()[0].transport.headers == {}

    def test_http_transport_headers_must_be_mapping(self, catalog_dir):
        body = _basic_manifest(
            transport={
                "type": "http",
                "url": "https://api.example.com/mcp/",
                "headers": ["not", "a", "mapping"],
            },
            auth={"type": "none"},
        )
        _write_manifest(catalog_dir, "demo", body)
        from hermes_cli.mcp_catalog import list_catalog

        # Invalid manifests are skipped (logged, not raised) by list_catalog.
        assert list_catalog() == []

    def test_install_block(self, catalog_dir):
        body = _basic_manifest(
            install={
                "type": "git",
                "url": "https://example.com/demo.git",
                "ref": "v1.0.0",
                "bootstrap": ["pip install -r requirements.txt"],
            },
            transport={
                "type": "stdio",
                "command": "${INSTALL_DIR}/.venv/bin/python",
                "args": ["${INSTALL_DIR}/server.py"],
            },
        )
        _write_manifest(catalog_dir, "demo", body)
        from hermes_cli.mcp_catalog import list_catalog

        e = list_catalog()[0]
        assert e.install is not None
        assert e.install.url == "https://example.com/demo.git"
        assert e.install.ref == "v1.0.0"
        assert e.install.bootstrap == ["pip install -r requirements.txt"]

    def test_invalid_manifest_skipped(self, catalog_dir):
        # Broken: wrong manifest_version
        _write_manifest(catalog_dir, "bad", {
            "manifest_version": 99,
            "name": "bad",
            "description": "x",
            "transport": {"type": "stdio", "command": "x"},
        })
        # Good
        _write_manifest(catalog_dir, "demo", _basic_manifest())
        from hermes_cli.mcp_catalog import list_catalog

        entries = list_catalog()
        assert [e.name for e in entries] == ["demo"]

    def test_missing_transport_command_rejected(self, catalog_dir):
        body = _basic_manifest()
        body["transport"] = {"type": "stdio"}  # no command
        _write_manifest(catalog_dir, "demo", body)
        from hermes_cli.mcp_catalog import list_catalog

        assert list_catalog() == []

    def test_get_entry_strips_official_prefix(self, catalog_dir):
        _write_manifest(catalog_dir, "demo", _basic_manifest())
        from hermes_cli.mcp_catalog import get_entry

        assert get_entry("demo") is not None
        assert get_entry("official/demo") is not None
        assert get_entry("missing") is None


# ---------------------------------------------------------------------------
# Install flow
# ---------------------------------------------------------------------------


class TestInstall:
    def test_install_simple_stdio_writes_config(self, catalog_dir):
        _write_manifest(catalog_dir, "demo", _basic_manifest())
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(_entry("demo"), enable=True)

        cfg = load_config()
        servers = cfg["mcp_servers"]
        assert "demo" in servers
        assert servers["demo"]["command"] == "npx"
        assert servers["demo"]["args"] == ["-y", "demo-mcp"]
        assert servers["demo"]["enabled"] is True

    def test_install_with_install_dir_substitution(self, catalog_dir, tmp_path):
        body = _basic_manifest(
            install={
                "type": "git",
                "url": "https://example.com/demo.git",
                "ref": "main",
                "bootstrap": [],
            },
            transport={
                "type": "stdio",
                "command": "${INSTALL_DIR}/run.sh",
                "args": ["${INSTALL_DIR}/cfg.json"],
            },
        )
        _write_manifest(catalog_dir, "demo", body)

        # Mock the git clone — return a known directory
        fake_clone = tmp_path / "fake-clone"
        fake_clone.mkdir()

        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        with patch.object(mcp_catalog, "_do_git_install", return_value=fake_clone):
            install_entry(_entry("demo"), enable=True)

        servers = load_config()["mcp_servers"]
        assert servers["demo"]["command"] == f"{fake_clone}/run.sh"
        assert servers["demo"]["args"] == [f"{fake_clone}/cfg.json"]

    def test_install_with_api_key_prompts_and_saves(self, catalog_dir, monkeypatch):
        body = _basic_manifest(
            auth={
                "type": "api_key",
                "env": [{"name": "DEMO_KEY", "prompt": "key", "secret": True}],
            }
        )
        _write_manifest(catalog_dir, "demo", body)

        from hermes_cli import mcp_catalog

        monkeypatch.setattr(mcp_catalog, "_prompt_input", lambda *a, **kw: "secret-val")

        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import get_env_value, load_config

        install_entry(_entry("demo"), enable=True)

        assert get_env_value("DEMO_KEY") == "secret-val"
        assert "demo" in load_config()["mcp_servers"]

    def test_reprompt_preserves_existing_env_vars_when_blank(self, catalog_dir, monkeypatch):
        body = _basic_manifest(
            auth={
                "type": "api_key",
                "env": [
                    {"name": "KEY1", "prompt": "key1", "secret": True},
                    {"name": "KEY2", "prompt": "key2", "secret": False, "required": False},
                ],
            }
        )
        _write_manifest(catalog_dir, "demo", body)

        from hermes_cli import mcp_catalog
        from hermes_cli.config import save_env_value, get_env_value
        from hermes_cli.mcp_catalog import install_entry

        save_env_value("KEY1", "existing-secret-1")
        save_env_value("KEY2", "existing-val-2")

        # Mock _prompt_input to return default when default is provided (user hits Enter)
        def fake_prompt(question, default=None, **kwargs):
            return default or ""

        monkeypatch.setattr(mcp_catalog, "_prompt_input", fake_prompt)

        install_entry(_entry("demo"), enable=True, reprompt=True)

        assert get_env_value("KEY1") == "existing-secret-1"
        assert get_env_value("KEY2") == "existing-val-2"

    def test_install_http_oauth_writes_auth_marker(self, catalog_dir):
        body = _basic_manifest(
            transport={"type": "http", "url": "https://mcp.example.com/sse"},
            auth={"type": "oauth"},
        )
        _write_manifest(catalog_dir, "demo", body)

        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(_entry("demo"), enable=True)

        server = load_config()["mcp_servers"]["demo"]
        assert server["url"] == "https://mcp.example.com/sse"
        assert server["auth"] == "oauth"

    def test_install_http_oauth_with_token_sends_bearer_header_not_native_oauth(
        self, catalog_dir, monkeypatch
    ):
        """Regression (GithubMCP stdio->http migration): an http+oauth entry
        with a token already saved for its env_var (pasted PAT, or one
        obtained via a provider's device-code login at install time) must
        send that token as an Authorization header to the remote server --
        generic MCP hosts aren't pre-registered with every provider for
        native browser-based OAuth against a hosted remote endpoint, so
        falling back to `auth: oauth` there would silently never connect."""
        body = _basic_manifest(
            transport={
                "type": "http",
                "url": "https://api.githubcopilot.com/mcp/",
                "headers": {"X-MCP-Toolsets": "context,repos,actions"},
            },
            auth={
                "type": "oauth",
                "provider": "github",
                "env_var": "DEMO_GH_TOKEN",
                "env": [{"name": "DEMO_GH_TOKEN", "prompt": "PAT", "required": False}],
            },
        )
        _write_manifest(catalog_dir, "demo", body)

        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config, get_config_path

        token_value = "ghp_faketoken"
        monkeypatch.setattr(mcp_catalog, "_prompt_input", lambda *a, **kw: token_value)

        install_entry(_entry("demo"), enable=True)

        # The on-disk config.yaml must store an unresolved placeholder
        # reference, never the literal secret -- same convention as the
        # stdio env-block substitution (see _expand_env_vars docs).
        raw_yaml = get_config_path().read_text()
        placeholder = "$" + "{DEMO_GH_TOKEN}"
        assert placeholder in raw_yaml
        assert token_value not in raw_yaml

        # load_config() expands placeholders against the stored env value,
        # so the resolved header must carry the real token at runtime.
        server = load_config()["mcp_servers"]["demo"]
        assert server["url"] == "https://api.githubcopilot.com/mcp/"
        assert "auth" not in server
        assert server["headers"]["X-MCP-Toolsets"] == "context,repos,actions"
        assert server["headers"]["Authorization"] == "Bearer " + token_value

    def test_install_http_oauth_without_token_falls_back_to_native_oauth(
        self, catalog_dir, monkeypatch
    ):
        """Same entry as above, but the user left the PAT blank and no
        device-code login populated it either -- must still fall back to
        `auth: oauth` (native MCP OAuth), not send an empty/broken header."""
        body = _basic_manifest(
            transport={
                "type": "http",
                "url": "https://api.githubcopilot.com/mcp/",
                "headers": {"X-MCP-Toolsets": "context,repos,actions"},
            },
            auth={
                "type": "oauth",
                "provider": "github",
                "env_var": "DEMO_GH_TOKEN",
                "env": [{"name": "DEMO_GH_TOKEN", "prompt": "PAT", "required": False}],
            },
        )
        _write_manifest(catalog_dir, "demo", body)

        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        monkeypatch.setattr(mcp_catalog, "_prompt_input", lambda *a, **kw: "")

        install_entry(_entry("demo"), enable=True)

        server = load_config()["mcp_servers"]["demo"]
        assert server["auth"] == "oauth"
        assert "Authorization" not in server.get("headers", {})
        assert server["headers"]["X-MCP-Toolsets"] == "context,repos,actions"

    def _stdio_provider_manifest(self, *, provider, env_var, extra_env=None):
        return _basic_manifest(
            auth={
                "type": "oauth",
                "provider": provider,
                "env_var": env_var,
                "env": extra_env or [],
            }
        )

    def test_install_github_device_code_login_saves_token(
        self, catalog_dir, monkeypatch
    ):
        """Regression: the github provider branch (now dispatched through
        _DEVICE_CODE_PROVIDERS instead of a hardcoded if-statement) must
        behave identically -- device-code login runs, and the resulting
        token is saved under GITHUB_PERSONAL_ACCESS_TOKEN."""
        body = self._stdio_provider_manifest(
            provider="github",
            env_var="GITHUB_PERSONAL_ACCESS_TOKEN",
            extra_env=[{"name": "GITHUB_PERSONAL_ACCESS_TOKEN", "prompt": "PAT", "required": False}],
        )
        _write_manifest(catalog_dir, "demo", body)

        import sys as _sys
        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import get_env_value

        monkeypatch.setattr(mcp_catalog, "_prompt_input", lambda *a, **kw: "")
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(
            "hermes_cli.copilot_auth.copilot_device_code_login",
            lambda: "gho_devicecodetoken",
        )

        install_entry(_entry("demo"), enable=True)

        assert get_env_value("GITHUB_PERSONAL_ACCESS_TOKEN") == "gho_devicecodetoken"

    def test_install_microsoft_device_code_login_saves_token(
        self, catalog_dir, monkeypatch
    ):
        body = self._stdio_provider_manifest(
            provider="microsoft",
            env_var="M365_ACCESS_TOKEN",
            extra_env=[
                {"name": "M365_CLIENT_ID", "prompt": "client id", "required": False},
                {"name": "M365_TENANT_ID", "prompt": "tenant id", "required": False, "default": "common"},
            ],
        )
        _write_manifest(catalog_dir, "demo", body)

        import sys as _sys
        import msal
        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import get_env_value

        # Leave M365_CLIENT_ID blank, accept the M365_TENANT_ID default --
        # neither is the access token itself, so device-code login must
        # still fire (has_token must key off M365_ACCESS_TOKEN, not these).
        def fake_prompt(question, default=None, **kwargs):
            return default or ""

        monkeypatch.setattr(mcp_catalog, "_prompt_input", fake_prompt)
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)

        class FakeApp:
            def __init__(self, *a, **kw):
                pass

            def initiate_device_flow(self, scopes=None):
                return {
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://microsoft.com/devicelogin",
                }

            def acquire_token_by_device_flow(self, flow):
                return {"access_token": "fake-msal-token"}

        monkeypatch.setattr(msal, "PublicClientApplication", FakeApp)

        install_entry(_entry("demo"), enable=True)

        assert get_env_value("M365_ACCESS_TOKEN") == "fake-msal-token"

    def test_install_microsoft_device_code_login_incomplete_no_crash(
        self, catalog_dir, monkeypatch
    ):
        """MSAL flow returning no access_token (user cancelled/timed out)
        must warn gracefully, not crash, and must not save a token."""
        body = self._stdio_provider_manifest(
            provider="microsoft",
            env_var="M365_ACCESS_TOKEN",
        )
        _write_manifest(catalog_dir, "demo", body)

        import sys as _sys
        import msal
        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import get_env_value

        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)

        class FakeApp:
            def __init__(self, *a, **kw):
                pass

            def initiate_device_flow(self, scopes=None):
                return {
                    "user_code": "ABCD-1234",
                    "verification_uri": "https://microsoft.com/devicelogin",
                }

            def acquire_token_by_device_flow(self, flow):
                return {"error": "authorization_declined"}

        monkeypatch.setattr(msal, "PublicClientApplication", FakeApp)

        install_entry(_entry("demo"), enable=True)

        assert get_env_value("M365_ACCESS_TOKEN") is None

    def test_install_microsoft_device_code_login_raises_no_crash(
        self, catalog_dir, monkeypatch
    ):
        """If the MSAL flow raises (e.g. network error), install must
        continue gracefully instead of propagating the exception."""
        body = self._stdio_provider_manifest(
            provider="microsoft",
            env_var="M365_ACCESS_TOKEN",
        )
        _write_manifest(catalog_dir, "demo", body)

        import sys as _sys
        import msal
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import get_env_value, load_config

        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)

        class FakeApp:
            def __init__(self, *a, **kw):
                raise RuntimeError("network unreachable")

        monkeypatch.setattr(msal, "PublicClientApplication", FakeApp)

        install_entry(_entry("demo"), enable=True)

        assert get_env_value("M365_ACCESS_TOKEN") is None
        assert "demo" in load_config()["mcp_servers"]

    def test_install_microsoft_non_tty_shows_ui_message(
        self, catalog_dir, monkeypatch, capsys
    ):
        """Non-interactive installs (web dashboard, CI) must not attempt the
        MSAL device-code flow at all -- same guard as GitHub's."""
        body = self._stdio_provider_manifest(
            provider="microsoft",
            env_var="M365_ACCESS_TOKEN",
        )
        _write_manifest(catalog_dir, "demo", body)

        import sys as _sys
        from hermes_cli.mcp_catalog import install_entry

        monkeypatch.setattr(_sys.stdin, "isatty", lambda: False)

        install_entry(_entry("demo"), enable=True)

        out = capsys.readouterr().out
        assert "Microsoft OAuth required. Complete via OAuth/Settings in UI." in out

        from hermes_cli.config import get_env_value

        assert get_env_value("M365_ACCESS_TOKEN") is None

    def test_install_unknown_provider_falls_back_to_generic_message(
        self, catalog_dir, monkeypatch, capsys
    ):
        """Providers not in the device-code dispatch table keep today's
        exact fallback behaviour -- the generic `hermes auth <provider>`
        message."""
        body = self._stdio_provider_manifest(
            provider="totally-made-up-provider",
            env_var="MADEUP_TOKEN",
        )
        _write_manifest(catalog_dir, "demo", body)

        import sys as _sys
        from hermes_cli.mcp_catalog import install_entry

        monkeypatch.setattr(_sys.stdin, "isatty", lambda: True)

        install_entry(_entry("demo"), enable=True)

        out = capsys.readouterr().out
        assert "This MCP uses totally-made-up-provider OAuth. Run `hermes auth totally-made-up-provider`" in out

    def test_install_oauth_prints_auth_notes(self, catalog_dir, capsys):
        """auth.notes is parsed but was never surfaced by the CLI install
        flow (only the GUI settings dialog rendered it) -- e.g. clarifying
        that MSOffice365MCP's default Client ID needs no tenant app
        registration was invisible to CLI/onboarding-wizard users."""
        body = _basic_manifest(
            transport={"type": "http", "url": "https://mcp.example.com/sse"},
            auth={"type": "oauth", "notes": "Default Client ID abc-123 needs no tenant registration."},
        )
        _write_manifest(catalog_dir, "demo", body)

        from hermes_cli.mcp_catalog import install_entry

        install_entry(_entry("demo"), enable=True)

        out = capsys.readouterr().out
        assert "Default Client ID abc-123 needs no tenant registration." in out

    def test_install_api_key_prints_auth_notes(self, catalog_dir, monkeypatch, capsys):
        body = _basic_manifest(
            auth={
                "type": "api_key",
                "notes": "Pick one auth mode.",
                "env": [{"name": "DEMO_KEY", "prompt": "key", "secret": True}],
            },
        )
        _write_manifest(catalog_dir, "demo", body)

        from hermes_cli import mcp_catalog

        monkeypatch.setattr(mcp_catalog, "_prompt_input", lambda *a, **kw: "secret-val")

        from hermes_cli.mcp_catalog import install_entry

        install_entry(_entry("demo"), enable=True)

        out = capsys.readouterr().out
        assert "Pick one auth mode." in out

    def test_install_required_env_missing_raises(self, catalog_dir, monkeypatch):
        body = _basic_manifest(
            auth={
                "type": "api_key",
                "env": [{"name": "MUST", "prompt": "x", "required": True, "secret": False}],
            }
        )
        _write_manifest(catalog_dir, "demo", body)

        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import install_entry, CatalogError

        # User hits enter — empty input, no default
        monkeypatch.setattr(mcp_catalog, "_prompt_input", lambda *a, **kw: "")

        with pytest.raises(CatalogError):
            install_entry(_entry("demo"), enable=True)


# ---------------------------------------------------------------------------
# Multi-instance install (instance_name / list_instances)
# ---------------------------------------------------------------------------


class TestMultiInstance:
    def test_install_with_instance_name_writes_custom_config_key(self, catalog_dir):
        _write_manifest(catalog_dir, "AtlassianMCP", _basic_manifest(name="AtlassianMCP"))
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(_entry("AtlassianMCP"), enable=True, instance_name="EVNAtlassianMCP")

        cfg = load_config()
        servers = cfg["mcp_servers"]
        assert "EVNAtlassianMCP" in servers
        assert "AtlassianMCP" not in servers
        assert servers["EVNAtlassianMCP"]["enabled"] is True

    def test_install_without_instance_name_uses_entry_name(self, catalog_dir):
        _write_manifest(catalog_dir, "demo", _basic_manifest())
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(_entry("demo"), enable=True)

        assert "demo" in load_config()["mcp_servers"]

    def test_install_two_instances_side_by_side(self, catalog_dir):
        _write_manifest(catalog_dir, "AtlassianMCP", _basic_manifest(name="AtlassianMCP"))
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(_entry("AtlassianMCP"), enable=True)
        install_entry(_entry("AtlassianMCP"), enable=True, instance_name="EVNAtlassianMCP")

        servers = load_config()["mcp_servers"]
        assert "AtlassianMCP" in servers
        assert "EVNAtlassianMCP" in servers

    def test_list_instances_exact_match(self, catalog_dir):
        _write_manifest(catalog_dir, "AtlassianMCP", _basic_manifest(name="AtlassianMCP"))
        from hermes_cli.mcp_catalog import install_entry, list_instances

        install_entry(_entry("AtlassianMCP"), enable=True)

        assert list_instances("AtlassianMCP") == ["AtlassianMCP"]

    def test_list_instances_suffix_match(self, catalog_dir):
        _write_manifest(catalog_dir, "AtlassianMCP", _basic_manifest(name="AtlassianMCP"))
        from hermes_cli.mcp_catalog import install_entry, list_instances

        install_entry(_entry("AtlassianMCP"), enable=True)
        install_entry(_entry("AtlassianMCP"), enable=True, instance_name="EVNAtlassianMCP")

        instances = list_instances("AtlassianMCP")
        assert set(instances) == {"AtlassianMCP", "EVNAtlassianMCP"}

    def test_list_instances_empty_when_none_installed(self, catalog_dir):
        _write_manifest(catalog_dir, "AtlassianMCP", _basic_manifest(name="AtlassianMCP"))
        from hermes_cli.mcp_catalog import list_instances

        assert list_instances("AtlassianMCP") == []

    def test_list_instances_does_not_match_unrelated_server(self, catalog_dir):
        _write_manifest(catalog_dir, "AtlassianMCP", _basic_manifest(name="AtlassianMCP"))
        _write_manifest(catalog_dir, "TempoMCP", _basic_manifest(name="TempoMCP"))
        from hermes_cli.mcp_catalog import install_entry, list_instances

        install_entry(_entry("TempoMCP"), enable=True)

        assert list_instances("AtlassianMCP") == []

    def test_reinstall_preserves_tool_selection_per_instance(self, catalog_dir):
        _write_manifest(catalog_dir, "AtlassianMCP", _basic_manifest(name="AtlassianMCP"))
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        # _default_mock_probe fixture makes _probe_tools return None (probe
        # fails), so install_entry falls back to the manifest default / no
        # filter for each instance independently.
        install_entry(_entry("AtlassianMCP"), enable=True, instance_name="EVNAtlassianMCP")
        install_entry(_entry("AtlassianMCP"), enable=True)

        servers = load_config()["mcp_servers"]
        # Each instance gets its own independent config block, not a shared one.
        assert "EVNAtlassianMCP" in servers
        assert "AtlassianMCP" in servers

    def _atlassian_manifest_with_env(self):
        return _basic_manifest(
            name="AtlassianMCP",
            auth={
                "type": "api_key",
                "env": [
                    {"name": "JIRA_URL", "prompt": "Jira URL", "secret": False},
                    {"name": "JIRA_PERSONAL_TOKEN", "prompt": "Token", "secret": True},
                ],
            },
        )

    def test_secondary_instance_literal_env_does_not_touch_shared_dotenv(self, catalog_dir):
        """A secondary instance's credentials must be embedded literally in
        its own mcp_servers.<name>.env block, never written to the shared
        ~/.hermes/.env -- otherwise a second Jira tenant's token would
        silently overwrite the default instance's token (same env-var
        names, shared file)."""
        _write_manifest(catalog_dir, "AtlassianMCP", self._atlassian_manifest_with_env())
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config, get_env_path

        install_entry(
            _entry("AtlassianMCP"),
            enable=True,
            instance_name="EVNAtlassianMCP",
            skip_auth_prompt=True,
            literal_env={
                "JIRA_URL": "https://jira.apps.evn.at",
                "JIRA_PERSONAL_TOKEN": "evn-secret-token",
            },
        )

        servers = load_config()["mcp_servers"]
        assert servers["EVNAtlassianMCP"]["env"] == {
            "JIRA_URL": "https://jira.apps.evn.at",
            "JIRA_PERSONAL_TOKEN": "evn-secret-token",
        }
        # Shared .env must remain untouched by the secondary instance's
        # install. Read the file directly rather than get_env_value(), which
        # also falls back to the real process os.environ and would mask a
        # regression if some other in-process test already exported these
        # names into the environment.
        env_path = get_env_path()
        dotenv_content = env_path.read_text() if env_path.exists() else ""
        assert "JIRA_URL" not in dotenv_content
        assert "JIRA_PERSONAL_TOKEN" not in dotenv_content

    def test_default_instance_still_uses_dotenv_interpolation(self, catalog_dir):
        """The default (non-secondary) instance keeps today's behavior: the
        on-disk config.yaml stores ${VAR} templates (resolved from the
        shared .env at connect time), not literal embedding.
        load_config() itself expands ${VAR} refs for runtime convenience,
        so the raw on-disk file (read_raw_config()) is what must be
        asserted against here, not load_config()'s expanded view."""
        _write_manifest(catalog_dir, "AtlassianMCP", self._atlassian_manifest_with_env())
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import read_raw_config, save_env_value

        save_env_value("JIRA_URL", "https://mycompany.atlassian.net")
        save_env_value("JIRA_PERSONAL_TOKEN", "default-token")

        install_entry(_entry("AtlassianMCP"), enable=True, skip_auth_prompt=True)

        servers = read_raw_config()["mcp_servers"]
        assert servers["AtlassianMCP"]["env"] == {
            "JIRA_URL": "${JIRA_URL}",
            "JIRA_PERSONAL_TOKEN": "${JIRA_PERSONAL_TOKEN}",
        }

    def test_two_secondary_instances_keep_independent_credentials(self, catalog_dir):
        """Two secondary instances installed side-by-side must each keep
        their own literal credentials, not clobber each other."""
        _write_manifest(catalog_dir, "AtlassianMCP", self._atlassian_manifest_with_env())
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(
            _entry("AtlassianMCP"), enable=True, instance_name="EVNAtlassianMCP",
            skip_auth_prompt=True,
            literal_env={"JIRA_URL": "https://jira.evn.at", "JIRA_PERSONAL_TOKEN": "evn-token"},
        )
        install_entry(
            _entry("AtlassianMCP"), enable=True, instance_name="AcmeAtlassianMCP",
            skip_auth_prompt=True,
            literal_env={"JIRA_URL": "https://acme.atlassian.net", "JIRA_PERSONAL_TOKEN": "acme-token"},
        )

        servers = load_config()["mcp_servers"]
        assert servers["EVNAtlassianMCP"]["env"]["JIRA_PERSONAL_TOKEN"] == "evn-token"
        assert servers["AcmeAtlassianMCP"]["env"]["JIRA_PERSONAL_TOKEN"] == "acme-token"


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_uninstall_removes_server_block(self, catalog_dir):
        _write_manifest(catalog_dir, "demo", _basic_manifest())
        from hermes_cli.mcp_catalog import install_entry, uninstall_entry
        from hermes_cli.config import load_config

        install_entry(_entry("demo"), enable=True)
        assert "demo" in load_config().get("mcp_servers", {})

        assert uninstall_entry("demo") is True
        assert "demo" not in load_config().get("mcp_servers", {})

    def test_uninstall_missing_returns_false(self):
        from hermes_cli.mcp_catalog import uninstall_entry

        assert uninstall_entry("nonexistent") is False


# ---------------------------------------------------------------------------
# Picker (non-TTY paths only — interactive curses is integration-tested)
# ---------------------------------------------------------------------------


class TestPicker:
    def test_show_catalog_empty(self, catalog_dir, capsys):
        from hermes_cli.mcp_picker import show_catalog

        show_catalog()
        out = capsys.readouterr().out
        assert "No MCPs in the catalog or configured" in out

    def test_show_catalog_lists_entry(self, catalog_dir, capsys):
        _write_manifest(catalog_dir, "demo", _basic_manifest())
        from hermes_cli.mcp_picker import show_catalog

        show_catalog()
        out = capsys.readouterr().out
        assert "demo" in out
        assert "available" in out

    def test_install_by_name_unknown(self, catalog_dir, capsys):
        from hermes_cli.mcp_picker import install_by_name

        rc = install_by_name("nope")
        assert rc == 1
        assert "not in the catalog" in capsys.readouterr().out

    def test_install_by_name_success(self, catalog_dir):
        _write_manifest(catalog_dir, "demo", _basic_manifest())
        from hermes_cli.mcp_picker import install_by_name
        from hermes_cli.config import load_config

        rc = install_by_name("demo")
        assert rc == 0
        assert "demo" in load_config().get("mcp_servers", {})

    def test_run_picker_non_tty_falls_back(self, catalog_dir, capsys, monkeypatch):
        _write_manifest(catalog_dir, "demo", _basic_manifest())
        # Force isatty false
        import sys as _sys
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: False)
        from hermes_cli.mcp_picker import run_picker

        run_picker()
        out = capsys.readouterr().out
        assert "MCP Catalog + configured servers" in out


# ---------------------------------------------------------------------------
# Shipped catalog (sanity: every manifest in the repo's optional-mcps/ parses)
# ---------------------------------------------------------------------------


class TestToolSelection:
    def _make_probed(self, *names):
        """Return a list of (tool_name, description) tuples for mocking."""
        return [(n, f"description of {n}") for n in names]

    def test_probe_fail_no_default_writes_no_filter(self, catalog_dir):
        body = _basic_manifest()
        _write_manifest(catalog_dir, "demo", body)
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(_entry("demo"), enable=True)
        server = load_config()["mcp_servers"]["demo"]
        # No tools.include => all tools active when reachable
        assert "tools" not in server, server

    def test_probe_fail_with_default_applies_directly(self, catalog_dir):
        body = _basic_manifest(
            tools={"default_enabled": ["a", "b", "c"]},
        )
        _write_manifest(catalog_dir, "demo", body)
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(_entry("demo"), enable=True)
        server = load_config()["mcp_servers"]["demo"]
        assert server["tools"]["include"] == ["a", "b", "c"]

    def test_probe_success_non_tty_with_default_filters_to_default(
        self, catalog_dir, monkeypatch
    ):
        body = _basic_manifest(
            tools={"default_enabled": ["alpha", "gamma"]},
        )
        _write_manifest(catalog_dir, "demo", body)
        import hermes_cli.mcp_catalog as mc

        probed = self._make_probed("alpha", "beta", "gamma", "delta")
        monkeypatch.setattr(mc, "_probe_tools", lambda name: probed)
        import sys as _sys
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: False)

        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(_entry("demo"), enable=True)
        server = load_config()["mcp_servers"]["demo"]
        # Only the manifest defaults that actually exist on the server
        assert server["tools"]["include"] == ["alpha", "gamma"]

    def test_probe_success_non_tty_no_default_clears_filter(
        self, catalog_dir, monkeypatch
    ):
        _write_manifest(catalog_dir, "demo", _basic_manifest())
        import hermes_cli.mcp_catalog as mc

        probed = self._make_probed("x", "y")
        monkeypatch.setattr(mc, "_probe_tools", lambda name: probed)
        import sys as _sys
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: False)

        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(_entry("demo"), enable=True)
        server = load_config()["mcp_servers"]["demo"]
        assert "tools" not in server

    def test_default_enabled_filters_out_unknown_tool_names(
        self, catalog_dir, monkeypatch
    ):
        """If manifest names a tool the server doesn\'t actually expose, it
        silently drops out — never written into tools.include."""
        body = _basic_manifest(
            tools={"default_enabled": ["real", "ghost"]},
        )
        _write_manifest(catalog_dir, "demo", body)
        import hermes_cli.mcp_catalog as mc

        probed = self._make_probed("real", "other")
        monkeypatch.setattr(mc, "_probe_tools", lambda name: probed)
        import sys as _sys
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: False)

        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(_entry("demo"), enable=True)
        server = load_config()["mcp_servers"]["demo"]
        assert server["tools"]["include"] == ["real", "ghost"]

    def test_reinstall_preserves_prior_user_selection(
        self, catalog_dir, monkeypatch
    ):
        """Second install of the same entry uses the user\'s prior
        tools.include as the pre-check, NOT the manifest default."""
        body = _basic_manifest(
            tools={"default_enabled": ["alpha"]},
        )
        _write_manifest(catalog_dir, "demo", body)

        import hermes_cli.mcp_catalog as mc
        probed = self._make_probed("alpha", "beta", "gamma")
        monkeypatch.setattr(mc, "_probe_tools", lambda name: probed)
        import sys as _sys
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: False)

        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config, save_config

        # First install
        install_entry(_entry("demo"), enable=True)
        # Simulate user opening configure and choosing beta+gamma
        cfg = load_config()
        cfg["mcp_servers"]["demo"]["tools"]["include"] = ["beta", "gamma"]
        save_config(cfg)

        # Reinstall (non-TTY honors prior_selection over manifest default)
        install_entry(_entry("demo"), enable=True)
        server = load_config()["mcp_servers"]["demo"]
        assert server["tools"]["include"] == ["beta", "gamma"], server

    def test_manifest_invalid_default_enabled_rejected(self, catalog_dir):
        body = _basic_manifest()
        body["tools"] = {"default_enabled": "not a list"}
        _write_manifest(catalog_dir, "demo", body)
        from hermes_cli.mcp_catalog import list_catalog

        # Invalid manifests are silently skipped at list_catalog level
        assert list_catalog() == []




# ---------------------------------------------------------------------------
# Forward-compat / diagnostics
# ---------------------------------------------------------------------------


class TestCatalogDiagnostics:
    def test_future_manifest_version_skipped_with_diagnostic(self, catalog_dir):
        """A manifest with a newer manifest_version is skipped, but the skip
        is reported via catalog_diagnostics so the UI can tell the user."""
        body = _basic_manifest()
        body["manifest_version"] = 999  # Future version
        _write_manifest(catalog_dir, "futuristic", body)
        # Plus one valid entry
        _write_manifest(catalog_dir, "demo", _basic_manifest())

        from hermes_cli.mcp_catalog import list_catalog, catalog_diagnostics

        entries = list_catalog()
        assert [e.name for e in entries] == ["demo"]

        diags = catalog_diagnostics()
        # At least one future_manifest diagnostic for the futuristic entry
        future = [d for d in diags if d[1] == "future_manifest"]
        assert len(future) == 1
        assert future[0][0] == "futuristic"

    def test_invalid_manifest_diagnostic(self, catalog_dir):
        body = _basic_manifest()
        body["transport"] = {"type": "unsupported"}
        _write_manifest(catalog_dir, "broken", body)

        from hermes_cli.mcp_catalog import list_catalog, catalog_diagnostics

        entries = list_catalog()
        assert entries == []
        diags = catalog_diagnostics()
        invalid = [d for d in diags if d[1] == "invalid"]
        assert len(invalid) == 1

    def test_picker_surfaces_future_manifest_warning(self, catalog_dir, capsys, monkeypatch):
        """The text-dump path should print a warning line for future-manifest
        entries so users running headless or after `hermes setup` know to update."""
        body = _basic_manifest()
        body["manifest_version"] = 999
        _write_manifest(catalog_dir, "futuristic", body)
        _write_manifest(catalog_dir, "demo", _basic_manifest())

        import sys as _sys
        monkeypatch.setattr(_sys.stdin, "isatty", lambda: False)
        from hermes_cli.mcp_picker import show_catalog

        show_catalog()
        out = capsys.readouterr().out
        assert "futuristic" in out
        assert "requires a newer Hermes" in out


# ---------------------------------------------------------------------------
# Picker — custom (non-catalog) MCP rows
# ---------------------------------------------------------------------------


class TestCustomMcpRows:
    def test_custom_mcp_shown_alongside_catalog(self, catalog_dir, capsys):
        """Servers in mcp_servers that aren't in the catalog show up in the
        picker text dump with a 'custom' status."""
        _write_manifest(catalog_dir, "demo", _basic_manifest())

        from hermes_cli.config import load_config, save_config
        cfg = load_config()
        cfg.setdefault("mcp_servers", {})["my-custom"] = {
            "command": "npx",
            "args": ["-y", "my-custom-mcp"],
            "enabled": True,
        }
        save_config(cfg)

        from hermes_cli.mcp_picker import show_catalog
        show_catalog()
        out = capsys.readouterr().out
        assert "demo" in out
        assert "my-custom" in out
        assert "custom" in out  # The status badge

    def test_custom_mcp_only_no_catalog(self, catalog_dir, capsys):
        """If the catalog is empty but the user has custom MCPs, they\'re
        still visible — the picker is the unified surface."""
        from hermes_cli.config import load_config, save_config
        cfg = load_config()
        cfg.setdefault("mcp_servers", {})["my-custom"] = {
            "url": "https://mcp.example.com",
            "enabled": False,
        }
        save_config(cfg)

        from hermes_cli.mcp_picker import show_catalog
        show_catalog()
        out = capsys.readouterr().out
        assert "my-custom" in out


# ---------------------------------------------------------------------------
# Git install — SHA ref detection
# ---------------------------------------------------------------------------


class TestGitInstallShaRef:
    def test_sha_ref_skips_branch_attempt(self, catalog_dir, monkeypatch, tmp_path):
        """When install.ref is a SHA-shaped hex string, _do_git_install
        skips the `git clone --branch <ref>` attempt (which would always fail
        noisily for SHAs) and goes straight to clone + checkout."""
        body = _basic_manifest(
            install={
                "type": "git",
                "url": "https://example.com/x.git",
                "ref": "abc1234567890abcdef1234567890abcdef12345",  # 40-char SHA
                "bootstrap": [],
            },
            transport={
                "type": "stdio",
                "command": "${INSTALL_DIR}/run.sh",
                "args": [],
            },
        )
        _write_manifest(catalog_dir, "demo", body)

        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import _do_git_install

        calls = []

        class _FakeProc:
            def __init__(self, returncode):
                self.returncode = returncode

        def fake_run(argv, *args, **kwargs):
            calls.append(list(argv))
            # Make every command succeed
            return _FakeProc(returncode=0)

        monkeypatch.setattr(mcp_catalog.subprocess, "run", fake_run)
        monkeypatch.setattr(mcp_catalog.shutil, "which", lambda x: "/usr/bin/git")

        from hermes_cli.mcp_catalog import get_entry
        entry = get_entry("demo")
        assert entry is not None
        _do_git_install(entry)

        # Should have called clone (no --branch) then checkout — NOT clone --branch
        branch_attempts = [c for c in calls if "--branch" in c]
        assert branch_attempts == [], (
            "SHA refs must NOT trigger a --branch clone attempt — that would "
            "always fail noisily before falling back. Calls were: " + repr(calls)
        )
        # Confirm we DID do plain clone + checkout
        clone_calls = [c for c in calls if "clone" in c and "--branch" not in c]
        checkout_calls = [c for c in calls if "checkout" in c]
        assert len(clone_calls) == 1, calls
        assert len(checkout_calls) == 1, calls

    def test_branch_ref_uses_branch_clone(self, catalog_dir, monkeypatch):
        """When install.ref is a branch/tag (not SHA-shaped), the fast
        `git clone --depth 1 --branch <ref>` path is used."""
        body = _basic_manifest(
            install={
                "type": "git",
                "url": "https://example.com/x.git",
                "ref": "v1.0.0",  # Tag-shaped
                "bootstrap": [],
            },
            transport={
                "type": "stdio",
                "command": "${INSTALL_DIR}/run.sh",
                "args": [],
            },
        )
        _write_manifest(catalog_dir, "demo", body)

        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import _do_git_install, get_entry

        calls = []

        class _FakeProc:
            def __init__(self, returncode):
                self.returncode = returncode

        def fake_run(argv, *args, **kwargs):
            calls.append(list(argv))
            return _FakeProc(returncode=0)

        monkeypatch.setattr(mcp_catalog.subprocess, "run", fake_run)
        monkeypatch.setattr(mcp_catalog.shutil, "which", lambda x: "/usr/bin/git")

        _do_git_install(get_entry("demo"))
        branch_attempts = [c for c in calls if "--branch" in c]
        assert len(branch_attempts) == 1, calls


class TestBootstrapInstallDirExpansion:
    """Regression coverage for the bootstrap ${INSTALL_DIR} substitution bug.

    _run_bootstrap previously ran install.bootstrap commands verbatim
    through the shell without substituting ${INSTALL_DIR} (only
    transport.command/args went through _expand_install_dir). Since
    ${INSTALL_DIR} isn't a real shell/env variable, it silently expanded to
    an empty string, so e.g. `pip install -r ${INSTALL_DIR}/reqs.txt` pointed
    at a nonexistent absolute path on every platform.
    """

    def test_run_bootstrap_substitutes_install_dir(self, monkeypatch, tmp_path):
        from hermes_cli import mcp_catalog

        calls = []

        class _FakeProc:
            returncode = 0

        def fake_run(cmd, cwd=None, shell=None):
            calls.append(cmd)
            return _FakeProc()

        monkeypatch.setattr(mcp_catalog.subprocess, "run", fake_run)
        monkeypatch.setattr(mcp_catalog.sys, "platform", "linux")
        monkeypatch.setattr(mcp_catalog.sys, "executable", "/usr/local/bin/python3.12")

        dest = tmp_path / "MSOffice365MCP"
        dest.mkdir()
        mcp_catalog._run_bootstrap(
            dest,
            [
                "python3 -m venv .venv",
                ".venv/bin/pip install -r ${INSTALL_DIR}/optional-mcps/MSOffice365MCP/requirements.txt",
            ],
        )

        assert calls[0] == '"/usr/local/bin/python3.12" -m venv .venv'
        assert "${INSTALL_DIR}" not in calls[1]
        assert calls[1] == (
            f".venv/bin/pip install -r {dest}/optional-mcps/MSOffice365MCP/requirements.txt"
        )

    def test_run_bootstrap_raises_on_failed_step(self, monkeypatch, tmp_path):
        from hermes_cli import mcp_catalog

        class _FakeProc:
            returncode = 1

        monkeypatch.setattr(
            mcp_catalog.subprocess, "run", lambda *a, **k: _FakeProc()
        )
        monkeypatch.setattr(mcp_catalog.sys, "platform", "linux")

        with pytest.raises(mcp_catalog.CatalogError):
            mcp_catalog._run_bootstrap(tmp_path, ["false"])

    def test_adapt_bootstrap_command_rewrites_for_windows(self, monkeypatch):
        from hermes_cli import mcp_catalog

        monkeypatch.setattr(mcp_catalog.sys, "platform", "win32")
        monkeypatch.setattr(mcp_catalog.sys, "executable", r"C:\Python311\python.exe")

        adapted = mcp_catalog._adapt_bootstrap_command("python3 -m venv .venv")
        assert adapted == r'"C:\Python311\python.exe" -m venv .venv'

        adapted_pip = mcp_catalog._adapt_bootstrap_command(
            ".venv/bin/pip install -r requirements.txt"
        )
        assert adapted_pip == ".venv/Scripts/pip install -r requirements.txt"

    def test_adapt_bootstrap_command_noop_on_non_windows(self, monkeypatch):
        """.venv/bin/... paths are left untouched on non-Windows, but a bare
        `python3` is still replaced with sys.executable everywhere -- see
        test_adapt_bootstrap_command_rewrites_python3_on_all_platforms."""
        from hermes_cli import mcp_catalog

        monkeypatch.setattr(mcp_catalog.sys, "platform", "darwin")
        cmd = ".venv/bin/pip install -r requirements.txt"
        assert mcp_catalog._adapt_bootstrap_command(cmd) == cmd

    def test_adapt_bootstrap_command_rewrites_python3_on_all_platforms(self, monkeypatch):
        """Regression: a literal `python3` in a bootstrap command must never
        be run verbatim, even on macOS/Linux. A Desktop-app child process
        launched without the user's shell PATH (no Homebrew/pyenv) resolves
        a bare `python3` to the OS-bundled Python (e.g. macOS's ancient
        CommandLineTools Python 3.9 with pip 21.2.4), which fails to resolve
        modern packages. sys.executable -- the interpreter running Hermes
        itself -- is always used instead."""
        from hermes_cli import mcp_catalog

        monkeypatch.setattr(mcp_catalog.sys, "platform", "darwin")
        monkeypatch.setattr(mcp_catalog.sys, "executable", "/opt/homebrew/bin/python3.12")
        adapted = mcp_catalog._adapt_bootstrap_command("python3 -m venv .venv")
        assert adapted == '"/opt/homebrew/bin/python3.12" -m venv .venv'

    def test_adapt_venv_executable_path_rewrites_for_windows(self, monkeypatch):
        from hermes_cli import mcp_catalog

        monkeypatch.setattr(mcp_catalog.sys, "platform", "win32")
        adapted = mcp_catalog._adapt_venv_executable_path(
            "/home/user/.hermes/mcp-installs/MSOffice365MCP/.venv/bin/python"
        )
        assert adapted == (
            "/home/user/.hermes/mcp-installs/MSOffice365MCP/.venv/Scripts/python.exe"
        )

    def test_adapt_venv_executable_path_noop_on_non_windows(self, monkeypatch):
        from hermes_cli import mcp_catalog

        monkeypatch.setattr(mcp_catalog.sys, "platform", "darwin")
        path = "/home/user/.hermes/mcp-installs/MSOffice365MCP/.venv/bin/python"
        assert mcp_catalog._adapt_venv_executable_path(path) == path


# ---------------------------------------------------------------------------
# Existing tools_config converged to tools.include
# ---------------------------------------------------------------------------


class TestToolsConfigIncludeMode:
    def test_configure_mcp_writes_include_not_exclude(self, monkeypatch, tmp_path):
        """`_configure_mcp_tools_interactive` in tools_config.py must write
        `tools.include` (whitelist), matching the rest of the codebase. The
        old behavior wrote `tools.exclude`, which produced inconsistent
        on-disk shapes depending on which UI the user used last."""
        # Build a minimal mcp_servers config + mock probe + checklist
        cfg = {
            "_config_version": 23,
            "mcp_servers": {
                "demo": {
                    "command": "npx",
                    "args": ["-y", "demo-mcp"],
                    "enabled": True,
                }
            },
        }

        import hermes_cli.tools_config as tc
        # Mock the probe to return three tools
        monkeypatch.setattr(
            "tools.mcp_tool.probe_mcp_server_tools",
            lambda: {"demo": [("a", "desc"), ("b", "desc"), ("c", "desc")]},
        )
        # Mock the checklist to return just the first tool
        monkeypatch.setattr(
            "hermes_cli.curses_ui.curses_checklist",
            lambda title, labels, pre_selected, **kw: {0},
        )
        # Mock save_config so we can inspect the write
        saved = {}

        def fake_save(config):
            saved.update(config)

        monkeypatch.setattr(tc, "save_config", fake_save)

        tc._configure_mcp_tools_interactive(cfg)

        # Must have written include, not exclude
        srv = saved["mcp_servers"]["demo"]["tools"]
        assert srv.get("include") == ["a"], srv
        assert "exclude" not in srv, srv


class TestShippedCatalog:
    def test_all_shipped_manifests_parse(self, monkeypatch):
        """Every manifest in optional-mcps/ must parse cleanly.

        This is a contract test — CI will fail if a PR adds a malformed
        manifest. Intentionally NOT a snapshot of catalog names (those are
        expected to change as PRs land).
        """
        # Use the actual repo's optional-mcps directory (no HERMES_OPTIONAL_MCPS
        # override) so this test catches real manifests.
        monkeypatch.delenv("HERMES_OPTIONAL_MCPS", raising=False)
        from hermes_cli.mcp_catalog import _catalog_root, _parse_manifest

        root = _catalog_root()
        if not root.exists():
            pytest.skip("optional-mcps/ not present in this checkout")

        manifests = list(root.glob("*/manifest.yaml"))
        # Don't assert minimum count — change-detector test rule. Just parse
        # whatever exists.
        for m in manifests:
            entry = _parse_manifest(m)
            assert entry.name
            assert entry.description
            assert entry.transport.type in ("stdio", "http")


class TestInstallProvenanceAndUpdate:
    """`install_source` provenance and the `hermes mcp update` path.

    A catalog install clones into ~/.hermes/mcp-installs/<name> and is never
    touched again, so a fix in the manifest's repo never reaches an existing
    install. Nothing recorded which commit an install came from, so staleness
    could not even be detected.
    """

    @staticmethod
    def _git_manifest():
        return _basic_manifest(
            install={
                "type": "git",
                "url": "https://example.com/demo.git",
                "ref": "main",
                "bootstrap": [],
            },
            transport={"type": "stdio", "command": "${INSTALL_DIR}/run.sh"},
        )

    def test_install_records_commit_url_and_ref(self, catalog_dir, tmp_path):
        _write_manifest(catalog_dir, "demo", self._git_manifest())

        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        clone = tmp_path / "clone"
        clone.mkdir()

        with patch.object(mcp_catalog, "_do_git_install", return_value=clone), patch.object(
            mcp_catalog, "installed_commit", return_value="a" * 40
        ):
            install_entry(_entry("demo"), enable=True)

        source = load_config()["mcp_servers"]["demo"]["install_source"]
        assert source["commit"] == "a" * 40
        assert source["url"] == "https://example.com/demo.git"
        assert source["ref"] == "main"
        assert source["dir"] == str(clone)
        assert source["installed_at"].endswith("+00:00")

    def test_non_git_entry_records_no_provenance(self, catalog_dir):
        _write_manifest(catalog_dir, "demo", _basic_manifest())

        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.config import load_config

        install_entry(_entry("demo"), enable=True)

        assert "install_source" not in load_config()["mcp_servers"]["demo"]

    def test_installed_commit_returns_none_outside_a_repo(self, tmp_path):
        from hermes_cli.mcp_catalog import installed_commit

        assert installed_commit(tmp_path) is None

    def test_update_reinstalls_and_reports_the_new_commit(self, catalog_dir, tmp_path, capsys):
        _write_manifest(catalog_dir, "demo", self._git_manifest())

        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.mcp_picker import update_by_name
        from hermes_cli.config import load_config

        clone = tmp_path / "clone"
        clone.mkdir()

        with patch.object(mcp_catalog, "_do_git_install", return_value=clone), patch.object(
            mcp_catalog, "installed_commit", return_value="a" * 40
        ):
            install_entry(_entry("demo"), enable=True)

        # Upstream moved on; the update path must re-clone and record it.
        with patch.object(mcp_catalog, "_do_git_install", return_value=clone) as clone_mock, patch.object(
            mcp_catalog, "installed_commit", return_value="b" * 40
        ):
            rc = update_by_name("demo")

        assert rc == 0
        assert clone_mock.call_count == 1
        assert load_config()["mcp_servers"]["demo"]["install_source"]["commit"] == "b" * 40
        assert "aaaaaaaa → bbbbbbbb" in capsys.readouterr().out

    def test_update_keeps_a_disabled_server_disabled(self, catalog_dir, tmp_path):
        _write_manifest(catalog_dir, "demo", self._git_manifest())

        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli.mcp_picker import update_by_name
        from hermes_cli.config import load_config

        clone = tmp_path / "clone"
        clone.mkdir()

        with patch.object(mcp_catalog, "_do_git_install", return_value=clone), patch.object(
            mcp_catalog, "installed_commit", return_value="a" * 40
        ):
            install_entry(_entry("demo"), enable=False)

        with patch.object(mcp_catalog, "_do_git_install", return_value=clone), patch.object(
            mcp_catalog, "installed_commit", return_value="b" * 40
        ):
            assert update_by_name("demo") == 0

        assert load_config()["mcp_servers"]["demo"]["enabled"] is False

    def test_update_refuses_an_uninstalled_server(self, catalog_dir):
        from hermes_cli.mcp_picker import update_by_name

        assert update_by_name("nope") == 1


class TestRefreshStaleInstalls:
    """`hermes update` must carry fixes into ~/.hermes/mcp-installs.

    Without this the agent checkout gets the fix and the clone that actually
    runs stays on whatever commit it was installed at — which is how a
    fixed MSOffice365MCP kept failing for hours after the fix shipped.
    """

    @staticmethod
    def _git_manifest():
        return _basic_manifest(
            install={
                "type": "git",
                "url": "https://example.com/demo.git",
                "ref": "main",
                "bootstrap": [],
            },
            transport={"type": "stdio", "command": "${INSTALL_DIR}/run.sh"},
        )

    def _install(self, catalog_dir, tmp_path, commit):
        _write_manifest(catalog_dir, "demo", self._git_manifest())

        from hermes_cli import mcp_catalog
        from hermes_cli.mcp_catalog import install_entry

        clone = tmp_path / "clone"
        clone.mkdir(exist_ok=True)

        with patch.object(mcp_catalog, "_do_git_install", return_value=clone), patch.object(
            mcp_catalog, "installed_commit", return_value=commit
        ):
            install_entry(_entry("demo"), enable=True)

        return clone

    def test_reclones_when_upstream_moved_ahead(self, catalog_dir, tmp_path):
        clone = self._install(catalog_dir, tmp_path, "a" * 40)

        from hermes_cli import mcp_catalog, mcp_picker
        from hermes_cli.config import load_config

        with patch.object(mcp_picker, "_remote_head", return_value="b" * 40), patch.object(
            mcp_catalog, "_do_git_install", return_value=clone
        ) as clone_mock, patch.object(mcp_catalog, "installed_commit", return_value="b" * 40):
            result = mcp_picker.refresh_stale_installs(quiet=True)

        assert result["updated"] == ["demo"]
        assert clone_mock.call_count == 1
        assert load_config()["mcp_servers"]["demo"]["install_source"]["commit"] == "b" * 40

    def test_leaves_an_up_to_date_install_alone(self, catalog_dir, tmp_path):
        clone = self._install(catalog_dir, tmp_path, "a" * 40)

        from hermes_cli import mcp_catalog, mcp_picker

        with patch.object(mcp_picker, "_remote_head", return_value="a" * 40), patch.object(
            mcp_catalog, "_do_git_install", return_value=clone
        ) as clone_mock:
            result = mcp_picker.refresh_stale_installs(quiet=True)

        assert result["updated"] == []
        assert result["checked"] == ["demo"]
        assert clone_mock.call_count == 0

    def test_unreachable_remote_does_not_reclone(self, catalog_dir, tmp_path):
        """No network must never mean "wipe the working install and retry"."""
        clone = self._install(catalog_dir, tmp_path, "a" * 40)

        from hermes_cli import mcp_catalog, mcp_picker

        with patch.object(mcp_picker, "_remote_head", return_value=None), patch.object(
            mcp_catalog, "_do_git_install", return_value=clone
        ) as clone_mock:
            result = mcp_picker.refresh_stale_installs(quiet=True)

        assert result["updated"] == []
        assert clone_mock.call_count == 0

    def test_missing_install_dir_is_skipped(self, catalog_dir, tmp_path):
        self._install(catalog_dir, tmp_path, "a" * 40)

        from hermes_cli import mcp_picker
        from hermes_cli.config import load_config, save_config

        cfg = load_config()
        cfg["mcp_servers"]["demo"]["install_source"]["dir"] = str(tmp_path / "gone")
        save_config(cfg)

        result = mcp_picker.refresh_stale_installs(quiet=True)

        assert result["skipped"] == ["demo"]
        assert result["checked"] == []

    def test_non_git_entry_is_ignored(self, catalog_dir):
        _write_manifest(catalog_dir, "demo", _basic_manifest())

        from hermes_cli.mcp_catalog import install_entry
        from hermes_cli import mcp_picker

        install_entry(_entry("demo"), enable=True)
        result = mcp_picker.refresh_stale_installs(quiet=True)

        assert result["checked"] == []
        assert result["updated"] == []
