"""Windows shortcuts follow the desktop app's exe after an update (AIS-476).

install.ps1 creates ``<App>.lnk`` in the Start menu and on the Desktop that
point straight at ``apps\\desktop\\release\\win-unpacked\\<App>.exe``; users pin
the app to the taskbar from there. When the rename release replaces
``Hermes.exe`` with the new product's exe, those shortcuts (and the taskbar
pin) would point at a file that no longer exists.

:func:`repair_desktop_shortcuts` runs after ``hermes desktop --build-only``
(the update pipeline) and re-targets every shortcut of this install — any
``.lnk`` in the Start menu, on the Desktop or in the taskbar pins whose target
lies in this install's ``release`` dir — at the current exe. Start-menu and
Desktop shortcuts also take the app's name; taskbar pins keep their file
name, because renaming a pinned ``.lnk`` drops the pin. Best-effort: it never
raises and never touches shortcuts of other apps.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional

logger = logging.getLogger(__name__)

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$exe = $env:HERMES_SHORTCUT_EXE
$root = $env:HERMES_SHORTCUT_RELEASE_DIR
$name = [System.IO.Path]::GetFileNameWithoutExtension($exe)
$workDir = Split-Path -Parent $exe
$ico = Join-Path $workDir 'resources\icon.ico'
$icon = if (Test-Path $ico) { "$ico,0" } else { "$exe,0" }
$taskbar = Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar'
$shell = New-Object -ComObject WScript.Shell
foreach ($folder in @([Environment]::GetFolderPath('Programs'), [Environment]::GetFolderPath('Desktop'), $taskbar)) {
    if (-not (Test-Path $folder)) { continue }
    foreach ($lnk in Get-ChildItem -Path $folder -Filter *.lnk -File -ErrorAction SilentlyContinue) {
        try {
            $sc = $shell.CreateShortcut($lnk.FullName)
            $target = [string]$sc.TargetPath
            if (-not $target -or -not $target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { continue }
            if ($target -ne $exe) {
                $sc.TargetPath = $exe
                $sc.WorkingDirectory = $workDir
                $sc.IconLocation = $icon
                $sc.Save()
                Write-Output "retargeted $($lnk.FullName)"
            }
            if ($folder -ne $taskbar -and $lnk.BaseName -ne $name) {
                $renamed = Join-Path $folder "$name.lnk"
                if (Test-Path $renamed) { Remove-Item -Force $lnk.FullName } else { Move-Item -Path $lnk.FullName -Destination $renamed }
                Write-Output "renamed $($lnk.FullName) -> $renamed"
            }
        } catch {
            Write-Output "skipped $($lnk.FullName): $($_.Exception.Message)"
        }
    }
}
"""


def repair_desktop_shortcuts(
    exe: Path,
    *,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
    platform: str = sys.platform,
) -> List[str]:
    """Re-target this install's Windows shortcuts at *exe*; returns what changed."""
    if platform != "win32" or not exe:
        return []
    release_dir = Path(exe).parent.parent
    env = {**os.environ, "HERMES_SHORTCUT_EXE": str(exe), "HERMES_SHORTCUT_RELEASE_DIR": str(release_dir)}
    try:
        result = (runner or subprocess.run)(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("desktop shortcut repair skipped: %s", exc)
        return []
    changes = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    for line in changes:
        logger.info("desktop shortcuts: %s", line)
    return changes
