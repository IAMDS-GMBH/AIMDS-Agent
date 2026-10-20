"""AIS-476: Windows shortcuts follow the desktop exe after an update."""

import subprocess
from pathlib import Path

from hermes_cli import desktop_shortcuts as ds

EXE = Path(r"C:\Users\x\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\AIMDS Agent.exe")


def test_noop_outside_windows():
    called = []
    assert ds.repair_desktop_shortcuts(EXE, runner=lambda *a, **k: called.append(a), platform="darwin") == []
    assert called == []


def test_runs_powershell_with_this_install_release_dir():
    seen = {}

    def runner(cmd, **kwargs):
        seen["cmd"], seen["env"] = cmd, kwargs["env"]
        return subprocess.CompletedProcess(cmd, 0, stdout="retargeted C:\\x\\Hermes.lnk\nrenamed a -> b\n\n", stderr="")

    changes = ds.repair_desktop_shortcuts(EXE, runner=runner, platform="win32")
    assert changes == ["retargeted C:\\x\\Hermes.lnk", "renamed a -> b"]
    assert seen["cmd"][0] == "powershell" and "User Pinned\\TaskBar" in seen["cmd"][-1]
    assert seen["env"]["HERMES_SHORTCUT_EXE"] == str(EXE)
    assert seen["env"]["HERMES_SHORTCUT_RELEASE_DIR"] == str(EXE.parent.parent)


def test_a_failing_powershell_never_raises():
    def runner(*a, **k):
        raise OSError("powershell missing")

    assert ds.repair_desktop_shortcuts(EXE, runner=runner, platform="win32") == []


def test_script_parses():
    import shutil

    pwsh = shutil.which("pwsh")
    if not pwsh:
        return
    check = (
        "$e=$null; [System.Management.Automation.Language.Parser]::ParseInput($env:S,[ref]$null,[ref]$e) | Out-Null; "
        "if ($e) { exit 1 }"
    )
    result = subprocess.run([pwsh, "-NoProfile", "-Command", check], env={"S": ds._SCRIPT, "PATH": "/usr/bin:/bin"}, check=False)
    assert result.returncode == 0
