"""Install identity for telemetry and support cases (AIS-449).

The support tool needs to know which reports come from the same machine and
which Hermes version that machine runs. Before this module the answer
depended on who asked:

* telemetry keyed a client by ``hostname-user`` — on macOS the hostname
  follows the network, so one Mac turned into several "clients";
* support cases sent only the user name, so the server keyed them
  differently again;
* the version fell back to ``pyproject.toml`` (0.7.4), the desktop's
  ``app.getVersion()`` or a hard-coded ``"v1.0.75"``.

The install id is a random token written once to ``<hermes root>/.install-id``.
The desktop reads and creates the same file (``electron/install-id.cjs``), so
the CLI, the backend and the desktop all report one id. It carries no
personal data; the support tool hashes it once more.
"""

from __future__ import annotations

import os
import socket
import uuid
from pathlib import Path
from typing import Optional

INSTALL_ID_FILENAME = ".install-id"
_INSTALL_ID_PREFIX = "inst-"


def install_id_path(root: Optional[Path] = None) -> Path:
    if root is None:
        from hermes_constants import get_default_hermes_root

        root = get_default_hermes_root()
    return Path(root) / INSTALL_ID_FILENAME


def _valid(value: str) -> bool:
    return value.startswith(_INSTALL_ID_PREFIX) and 8 < len(value) <= 64 and value.replace("-", "").isalnum()


def get_install_id(root: Optional[Path] = None, *, create: bool = True) -> str:
    """The install id, created on first use. ``""`` when it cannot be read or written."""
    path = install_id_path(root)
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if _valid(existing):
            return existing
    except (OSError, UnicodeDecodeError):
        pass
    if not create:
        return ""
    new_id = f"{_INSTALL_ID_PREFIX}{uuid.uuid4().hex}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # O_EXCL: when the desktop and the backend race on first start, the
        # loser reads the winner's id instead of overwriting it.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return ""
        return existing if _valid(existing) else ""
    except OSError:
        return ""
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(new_id + "\n")
    return new_id


def legacy_client_id() -> str:
    """The pre-AIS-449 ``hostname-user`` id, sent once more so the server can merge the old row."""
    user = os.getenv("USER") or os.getenv("USERNAME") or "user"
    try:
        import getpass

        user = getpass.getuser() or user
    except Exception:
        pass
    return f"{socket.gethostname()}-{user}"


def hermes_version() -> str:
    """The installed Hermes version: release marker, then git tag/describe, then package metadata."""
    try:
        from hermes_cli import __version__

        return str(__version__ or "")
    except Exception:
        return ""
