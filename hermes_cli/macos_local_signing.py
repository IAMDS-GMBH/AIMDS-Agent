"""A per-machine code-signing identity for locally built macOS desktop apps (AIS-481).

On the ``main`` channel every update rebuilds the desktop app locally. It used
to be ad-hoc signed: each build had a new code identity, so macOS treated it as
a new app and asked again for the Files & Folders grant (``~/Documents/...``)
after every update — on 2026-10-04 the boot waited 50 minutes on that dialog.

This module creates one self-signed code-signing certificate per machine, in a
dedicated keychain under ``~/.hermes/signing`` (the login keychain stays
untouched), and signs every local build with it. The designated requirement
becomes ``identifier <bundle id> and certificate leaf = H"<sha1>"``: the same
for every build, so the grant survives updates.

Facts this relies on (checked on macOS 26): ``codesign`` signs with an
untrusted self-signed identity without any dialog once the key's partition
list allows ``codesign:``, but it only finds the identity when its keychain is
on the user's search list — the keychain is added for the signing call and the
original list is restored right after. ``security create-keychain`` adds the
new keychain to that list too, so creation restores it as well.

Never silent: a missing or broken keychain is regenerated (macOS asks once
more), and when signing with the identity fails the caller falls back to the
ad-hoc signature and says so.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, List, Optional

SIGNING_DIRNAME = "signing"
KEYCHAIN_NAME = "hermes-local.keychain-db"
PASSWORD_NAME = "keychain-password"
IDENTITY_NAME = "identity.json"
CERT_DAYS = 3650
#: LibreSSL in the base system writes PKCS#12 files ``security import`` reads
#: (OpenSSL 3 defaults to AES, which older macOS releases reject).
OPENSSL = "/usr/bin/openssl"

Runner = Callable[..., subprocess.CompletedProcess]


@dataclass(frozen=True)
class LocalIdentity:
    keychain: Path
    password: str
    sha1: str
    common_name: str


def _run(run: Runner, args: List[str], **kwargs) -> subprocess.CompletedProcess:
    return run(args, capture_output=True, text=True, check=False, **kwargs)


def _signing_dir(home: Optional[Path]) -> Path:
    if home is None:
        from hermes_constants import get_hermes_home

        home = Path(get_hermes_home())
    return Path(home) / SIGNING_DIRNAME


def _search_list(run: Runner) -> List[str]:
    res = _run(run, ["security", "list-keychains", "-d", "user"])
    return [line.strip().strip('"') for line in (res.stdout or "").splitlines() if line.strip()]


@contextmanager
def _keychain_on_search_list(keychain: Path, run: Runner) -> Iterator[None]:
    """Put *keychain* on the user's search list for the duration, then restore."""
    original = _search_list(run)
    added = str(keychain) not in original
    if added:
        _run(run, ["security", "list-keychains", "-d", "user", "-s", *original, str(keychain)])
    try:
        yield
    finally:
        if added and original:
            _run(run, ["security", "list-keychains", "-d", "user", "-s", *original])


def _find_sha1(keychain: Path, common_name: str, run: Runner) -> str:
    """SHA-1 of the codesigning identity *common_name* in *keychain* ("" if absent)."""
    res = _run(run, ["security", "find-identity", "-p", "codesigning", str(keychain)])
    for line in (res.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].endswith(")") and common_name in line:
            return parts[1]
    return ""


def _load(signing_dir: Path, run: Runner) -> Optional[LocalIdentity]:
    keychain = signing_dir / KEYCHAIN_NAME
    try:
        password = (signing_dir / PASSWORD_NAME).read_text(encoding="utf-8").strip()
        meta = json.loads((signing_dir / IDENTITY_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not keychain.exists() or not password:
        return None
    common_name = str(meta.get("common_name") or "")
    if _run(run, ["security", "unlock-keychain", "-p", password, str(keychain)]).returncode != 0:
        return None
    sha1 = _find_sha1(keychain, common_name, run)
    if not sha1 or sha1.upper() != str(meta.get("sha1") or "").upper():
        return None
    return LocalIdentity(keychain, password, sha1, common_name)


def _create(signing_dir: Path, run: Runner, log: Callable[[str], None]) -> Optional[LocalIdentity]:
    signing_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(signing_dir, 0o700)
    keychain = signing_dir / KEYCHAIN_NAME
    if keychain.exists():
        _run(run, ["security", "delete-keychain", str(keychain)])
        keychain.unlink(missing_ok=True)
    try:
        from hermes_cli.install_identity import get_install_id

        install_id = get_install_id() or secrets.token_hex(4)
    except Exception:
        install_id = secrets.token_hex(4)
    common_name = f"Hermes Local Signing {install_id}"
    password = secrets.token_urlsafe(24)
    p12_password = secrets.token_urlsafe(16)

    with tempfile.TemporaryDirectory(prefix="hermes-sign-") as tmp:
        tmp_dir = Path(tmp)
        config = tmp_dir / "cert.cnf"
        config.write_text(
            "[req]\ndistinguished_name = dn\nx509_extensions = ext\nprompt = no\n"
            f"[dn]\nCN = {common_name}\n"
            "[ext]\nbasicConstraints = critical,CA:false\nkeyUsage = critical,digitalSignature\n"
            "extendedKeyUsage = critical,codeSigning\n",
            encoding="utf-8",
        )
        key, cert, p12 = tmp_dir / "key.pem", tmp_dir / "cert.pem", tmp_dir / "id.p12"
        steps = [
            [OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key), "-out", str(cert),
             "-days", str(CERT_DAYS), "-config", str(config)],
            [OPENSSL, "pkcs12", "-export", "-inkey", str(key), "-in", str(cert), "-out", str(p12),
             "-passout", f"pass:{p12_password}"],
        ]
        for step in steps:
            res = _run(run, step)
            if res.returncode != 0:
                log(f"local signing identity: {Path(step[0]).name} {step[1]} failed: {(res.stderr or '').strip()[:200]}")
                return None
        original = _search_list(run)
        created = _run(run, ["security", "create-keychain", "-p", password, str(keychain)])
        if original:  # create-keychain adds itself to the search list
            _run(run, ["security", "list-keychains", "-d", "user", "-s", *original])
        if created.returncode != 0:
            log(f"local signing identity: create-keychain failed: {(created.stderr or '').strip()[:200]}")
            return None
        for step in (
            ["security", "set-keychain-settings", str(keychain)],  # never auto-lock
            ["security", "unlock-keychain", "-p", password, str(keychain)],
            ["security", "import", str(p12), "-k", str(keychain), "-P", p12_password, "-T", "/usr/bin/codesign"],
            ["security", "set-key-partition-list", "-S", "apple-tool:,apple:,codesign:", "-s", "-k", password,
             str(keychain)],
        ):
            res = _run(run, step)
            if res.returncode != 0:
                log(f"local signing identity: security {step[1]} failed: {(res.stderr or '').strip()[:200]}")
                return None

    sha1 = _find_sha1(keychain, common_name, run)
    if not sha1:
        log("local signing identity: imported identity not found in its keychain")
        return None
    password_file = signing_dir / PASSWORD_NAME
    password_file.write_text(password + "\n", encoding="utf-8")
    os.chmod(password_file, 0o600)
    (signing_dir / IDENTITY_NAME).write_text(
        json.dumps({
            "common_name": common_name,
            "sha1": sha1,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, indent=2) + "\n",
        encoding="utf-8",
    )
    return LocalIdentity(keychain, password, sha1, common_name)


def ensure_local_identity(
    home: Optional[Path] = None,
    *,
    run: Runner = subprocess.run,
    log: Callable[[str], None] = print,
    platform: str = sys.platform,
) -> Optional[LocalIdentity]:
    """The machine's signing identity, created on first use (macOS only)."""
    if platform != "darwin":
        return None
    if run is subprocess.run and not shutil.which("security"):
        return None
    signing_dir = _signing_dir(home)
    identity = _load(signing_dir, run)
    if identity is not None:
        return identity
    if (signing_dir / KEYCHAIN_NAME).exists():
        log("  ⚠ The local signing keychain is missing or damaged — creating a new one; "
            "macOS will ask once more for folder access.")
    identity = _create(signing_dir, run, log)
    if identity is not None:
        log(f"  ✓ Local signing identity ready ({identity.common_name})")
    return identity


def sign_app(app: Path, identity: LocalIdentity, *, run: Runner = subprocess.run) -> bool:
    """Deep-sign *app* with *identity*; True on success."""
    if _run(run, ["security", "unlock-keychain", "-p", identity.password, str(identity.keychain)]).returncode != 0:
        return False
    with _keychain_on_search_list(identity.keychain, run):
        res = _run(run, ["codesign", "--force", "--deep", "--sign", identity.sha1, str(app)])
    return res.returncode == 0
