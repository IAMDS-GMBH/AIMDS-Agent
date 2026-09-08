"""Suite update feed integration with the check and apply command paths."""

import json
import time
from unittest.mock import patch

import pytest

SUITE_HOST = "suite.iamds.com"
REPO = "IAMDS-GMBH/AIMDS-Agent"


def _suite_config():
    return {
        "providers": {"aimds-suite-prod": {"base_url": f"https://{SUITE_HOST}/litellm/v1"}},
        "updates": {"source_repository": REPO, "channel": "prod"},
    }


def test_check_for_updates_reports_suite_version_without_touching_disk(tmp_path, monkeypatch):
    """The periodic check surfaces the Suite feed and only writes .update_check."""
    import hermes_cli.banner as banner
    from hermes_cli.suite_update import SuiteFeed

    repo_dir = tmp_path / "hermes-agent"
    (repo_dir / ".git").mkdir(parents=True)

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_REVISION", raising=False)

    feed = SuiteFeed(
        version="9.9.9",
        target_ref="v9.9.9",
        package_url=f"https://{SUITE_HOST}/client-update/HermesSuite.zip",
        sha256="c" * 64,
        repository=REPO,
    )

    with patch("hermes_cli.config.load_config_readonly", return_value=_suite_config()), \
         patch("hermes_cli.suite_update.check_suite_update", return_value=feed) as mock_check, \
         patch("hermes_cli.suite_update.apply_suite_update") as mock_apply, \
         patch("hermes_cli.banner._check_via_local_git", return_value=0), \
         patch("hermes_cli.banner.check_via_pypi", return_value=0):
        behind = banner.check_for_updates()

    assert behind == banner.UPDATE_AVAILABLE_NO_COUNT
    mock_check.assert_called_once()
    mock_apply.assert_not_called()

    cached = json.loads((tmp_path / ".update_check").read_text())
    assert cached["suite_version"] == "9.9.9"
    assert banner.get_suite_update_version() == "9.9.9"

    # The check must not have downloaded or staged anything into the checkout.
    assert list(repo_dir.iterdir()) == [repo_dir / ".git"]


def test_check_for_updates_skips_suite_feed_without_provider(tmp_path, monkeypatch):
    """No Suite provider configured means no feed probe at all."""
    import hermes_cli.banner as banner

    repo_dir = tmp_path / "hermes-agent"
    (repo_dir / ".git").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_REVISION", raising=False)

    with patch("hermes_cli.config.load_config_readonly", return_value={"providers": {}}), \
         patch("hermes_cli.suite_update.check_suite_update") as mock_check, \
         patch("hermes_cli.banner._check_via_local_git", return_value=0), \
         patch("hermes_cli.banner.check_via_pypi", return_value=0):
        banner.check_for_updates()

    mock_check.assert_not_called()
    assert json.loads((tmp_path / ".update_check").read_text())["suite_version"] is None


@pytest.mark.parametrize("outcome", ["no_feed", "apply_error"])
def test_try_suite_update_falls_back_to_git(outcome, capsys):
    """An unusable feed leaves the git path to run and reports why."""
    import hermes_cli.main as main
    from hermes_cli.suite_update import SuiteFeed, SuiteFeedError

    if outcome == "no_feed":
        check_kwargs = {"return_value": None}
        apply_kwargs = {"return_value": 0}
    else:
        check_kwargs = {
            "return_value": SuiteFeed(
                version="9.9.9",
                target_ref="v9.9.9",
                package_url=f"https://{SUITE_HOST}/client-update/HermesSuite.zip",
                sha256="c" * 64,
                repository=REPO,
            )
        }
        apply_kwargs = {"side_effect": SuiteFeedError("archive sha256 mismatch")}

    with patch("hermes_cli.config.detect_install_method", return_value="git"), \
         patch("hermes_cli.config.load_config", return_value=_suite_config()), \
         patch("hermes_cli.suite_update.check_suite_update", **check_kwargs), \
         patch("hermes_cli.suite_update.apply_suite_update", **apply_kwargs), \
         patch.object(main, "_install_python_dependencies_after_update") as mock_deps, \
         patch.object(main, "_update_node_dependencies") as mock_node, \
         patch.object(main, "_build_web_ui") as mock_web:
        assert main._try_suite_update("https://github.com/IAMDS-GMBH/AIMDS-Agent.git") is False

    mock_deps.assert_not_called()
    mock_node.assert_not_called()
    mock_web.assert_not_called()
    if outcome == "apply_error":
        assert "sha256 mismatch" in capsys.readouterr().out


def test_try_suite_update_skips_non_source_installs():
    """pip/docker installs never consult the Suite source feed."""
    import hermes_cli.main as main

    with patch("hermes_cli.config.detect_install_method", return_value="pip"), \
         patch("hermes_cli.suite_update.check_suite_update") as mock_check:
        assert main._try_suite_update(None) is False

    mock_check.assert_not_called()


def test_try_suite_update_runs_post_steps_on_success():
    """A successful Suite update reuses the git path's rebuild ordering."""
    import hermes_cli.main as main
    from hermes_cli.suite_update import SuiteFeed

    feed = SuiteFeed(
        version="9.9.9",
        target_ref="v9.9.9",
        package_url=f"https://{SUITE_HOST}/client-update/HermesSuite.zip",
        sha256="c" * 64,
        repository=REPO,
    )
    calls = []

    with patch("hermes_cli.config.detect_install_method", return_value="git"), \
         patch("hermes_cli.config.load_config", return_value=_suite_config()), \
         patch("hermes_cli.suite_update.check_suite_update", return_value=feed), \
         patch("hermes_cli.suite_update.apply_suite_update", return_value=7), \
         patch.object(main, "_invalidate_update_cache", lambda: calls.append("cache")), \
         patch.object(main, "_clear_bytecode_cache", lambda _p: calls.append("bytecode") or 0), \
         patch.object(main, "_install_python_dependencies_after_update", lambda: calls.append("deps")), \
         patch.object(main, "_update_node_dependencies", lambda: calls.append("node")), \
         patch.object(main, "_build_web_ui", lambda _p: calls.append("web")):
        assert main._try_suite_update(None) is True

    assert calls == ["cache", "bytecode", "deps", "node", "web"]
