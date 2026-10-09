"""AIS-481: one local code-signing identity per machine for main-channel builds."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli import macos_local_signing as mls

SHA1 = "E0F1E86BA4A0E89E11948E17A54BA5DDE4A312FB"


class FakeMac:
    """Simulates security/openssl/codesign with a user keychain search list."""

    def __init__(self, fail: str = ""):
        self.search = ["/Users/x/Library/Keychains/login.keychain-db"]
        self.keychains: dict[str, str] = {}  # path -> common name of the imported identity
        self.calls: list[list[str]] = []
        self.sign_search: list[list[str]] = []
        self.fail = fail
        self.last_cn = ""

    def __call__(self, args, **kwargs):
        args = [str(a) for a in args]
        self.calls.append(args)
        tool, sub = Path(args[0]).name, args[1] if len(args) > 1 else ""
        ok = subprocess.CompletedProcess(args, 0, "", "")
        if self.fail and self.fail in (tool, sub):
            return subprocess.CompletedProcess(args, 1, "", f"{sub} failed")
        if tool == "openssl" and sub == "req":
            cfg = Path(args[args.index("-config") + 1]).read_text()
            self.last_cn = cfg.split("CN = ", 1)[1].split("\n", 1)[0]
            return ok
        if tool == "security":
            if sub == "list-keychains":
                if "-s" in args:
                    self.search = args[args.index("-s") + 1:]
                    return ok
                return subprocess.CompletedProcess(args, 0, "".join(f'    "{k}"\n' for k in self.search), "")
            if sub == "create-keychain":
                path = args[-1]
                self.keychains[path] = ""
                self.search = self.search + [path]  # like the real tool
                Path(path).write_text("kc")
                return ok
            if sub == "import":
                self.keychains[args[args.index("-k") + 1]] = self.last_cn
                return ok
            if sub == "find-identity":
                cn = self.keychains.get(args[-1], "")
                out = f'  1) {SHA1} "{cn}" (CSSMERR_TP_NOT_TRUSTED)\n' if cn else "\n"
                return subprocess.CompletedProcess(args, 0, out, "")
            if sub == "unlock-keychain":
                return ok if args[-1] in self.keychains else subprocess.CompletedProcess(args, 1, "", "no keychain")
            if sub == "delete-keychain":
                self.keychains.pop(args[-1], None)
                return ok
            return ok
        if tool == "codesign":
            self.sign_search.append(list(self.search))
            return ok
        return ok


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr("hermes_cli.install_identity.get_install_id", lambda *a, **k: "abc123")
    return tmp_path


def _logs():
    lines: list[str] = []
    return lines, lines.append


def test_first_use_creates_the_identity_without_touching_the_search_list(home):
    mac = FakeMac()
    lines, log = _logs()
    identity = mls.ensure_local_identity(home, run=mac, log=log, platform="darwin")

    assert identity is not None and identity.sha1 == SHA1
    assert identity.common_name == "Hermes Local Signing abc123"
    assert mac.search == ["/Users/x/Library/Keychains/login.keychain-db"], "create-keychain's entry is removed again"
    signing = home / "signing"
    assert stat.S_IMODE((signing / "keychain-password").stat().st_mode) == 0o600
    assert stat.S_IMODE(signing.stat().st_mode) == 0o700
    partition = [c for c in mac.calls if c[:2] == ["security", "set-key-partition-list"]]
    assert partition and "apple-tool:,apple:,codesign:" in partition[0]
    assert any("✓ Local signing identity ready" in line for line in lines)


def test_second_use_reuses_the_identity(home):
    mac = FakeMac()
    first = mls.ensure_local_identity(home, run=mac, log=lambda m: None, platform="darwin")
    mac.calls.clear()
    second = mls.ensure_local_identity(home, run=mac, log=lambda m: None, platform="darwin")

    assert second == first
    assert not any(c[:2] == ["security", "create-keychain"] for c in mac.calls)


def test_a_damaged_keychain_is_regenerated_and_says_so(home):
    mac = FakeMac()
    mls.ensure_local_identity(home, run=mac, log=lambda m: None, platform="darwin")
    (home / "signing" / "identity.json").write_text('{"common_name": "x", "sha1": "OTHER"}')
    lines, log = _logs()

    identity = mls.ensure_local_identity(home, run=mac, log=log, platform="darwin")

    assert identity is not None
    assert any("macOS will ask once more" in line for line in lines)
    assert any(c[:2] == ["security", "delete-keychain"] for c in mac.calls)


def test_creation_failure_returns_none_and_logs(home):
    lines, log = _logs()
    assert mls.ensure_local_identity(home, run=FakeMac(fail="import"), log=log, platform="darwin") is None
    assert any("security import failed" in line for line in lines)


def test_not_on_macos():
    assert mls.ensure_local_identity(Path("/nonexistent"), run=FakeMac(), platform="linux") is None


def test_signing_puts_the_keychain_on_the_search_list_only_while_signing(home):
    mac = FakeMac()
    identity = mls.ensure_local_identity(home, run=mac, log=lambda m: None, platform="darwin")
    app = home / "Hermes.app"
    app.mkdir()

    assert mls.sign_app(app, identity, run=mac) is True
    assert str(identity.keychain) in mac.sign_search[-1]
    assert mac.search == ["/Users/x/Library/Keychains/login.keychain-db"]
    codesign = [c for c in mac.calls if c[0] == "codesign"][-1]
    assert codesign[:5] == ["codesign", "--force", "--deep", "--sign", SHA1]


def test_signing_failure_still_restores_the_search_list(home):
    mac = FakeMac()
    identity = mls.ensure_local_identity(home, run=mac, log=lambda m: None, platform="darwin")
    mac.fail = "codesign"
    assert mls.sign_app(home / "Hermes.app", identity, run=mac) is False
    assert mac.search == ["/Users/x/Library/Keychains/login.keychain-db"]


class TestRelaunchableFixup:
    def _app(self, tmp_path):
        exe = tmp_path / "release" / "mac-arm64" / "Hermes.app" / "Contents" / "MacOS" / "Hermes"
        exe.parent.mkdir(parents=True)
        exe.write_text("bin")
        return exe

    def test_uses_the_local_identity_instead_of_ad_hoc(self, tmp_path, monkeypatch, capsys):
        from hermes_cli import main as hm

        exe = self._app(tmp_path)
        monkeypatch.setattr(hm.sys, "platform", "darwin")
        monkeypatch.delenv("CSC_LINK", raising=False)
        monkeypatch.delenv("APPLE_SIGNING_IDENTITY", raising=False)
        identity = mls.LocalIdentity(tmp_path / "kc", "pw", SHA1, "Hermes Local Signing abc123")
        with patch.object(hm, "_desktop_packaged_executable", return_value=exe), \
             patch.object(hm.shutil, "which", return_value="/usr/bin/codesign"), \
             patch.object(hm.subprocess, "run") as run, \
             patch.object(mls, "ensure_local_identity", return_value=identity), \
             patch.object(mls, "sign_app", return_value=True) as sign:
            hm._desktop_macos_relaunchable_fixup(tmp_path)

        sign.assert_called_once()
        assert not any("-" == c.args[0][-2] for c in run.call_args_list if c.args and "codesign" in str(c.args[0][0]))
        assert "Signed with the local signing identity" in capsys.readouterr().out

    def test_falls_back_to_ad_hoc_and_says_so(self, tmp_path, monkeypatch, capsys):
        from hermes_cli import main as hm

        exe = self._app(tmp_path)
        monkeypatch.setattr(hm.sys, "platform", "darwin")
        monkeypatch.delenv("CSC_LINK", raising=False)
        monkeypatch.delenv("APPLE_SIGNING_IDENTITY", raising=False)
        with patch.object(hm, "_desktop_packaged_executable", return_value=exe), \
             patch.object(hm.shutil, "which", return_value="/usr/bin/codesign"), \
             patch.object(hm.subprocess, "run") as run, \
             patch.object(mls, "ensure_local_identity", return_value=None):
            hm._desktop_macos_relaunchable_fixup(tmp_path)

        adhoc = [c.args[0] for c in run.call_args_list if c.args and c.args[0][0] == "/usr/bin/codesign"]
        assert adhoc and adhoc[-1][-2] == "-"
        assert "macOS will ask again" in capsys.readouterr().out
