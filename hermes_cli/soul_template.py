"""The shipped SOUL template: seeding, hash-gated update, template notice (AIS-795).

One source: ``installer/skills-hidden/aimds-loadout/identity/SOUL.md`` (the
co-worker identity) and ``SOUL.dev.md`` (the developer posture of the terminal
CLI). New profiles are seeded from it. A profile file that still equals a text
we shipped before — matched by a normalised sha256 listed in
``shipped-hashes.json`` next to the template — is updated in place. Anything
else was edited by its owner and is never overwritten; it only reports that a
newer template exists, with the diff, until the owner dismisses that version.

Run as ``python -m hermes_cli.soul_template [HERMES_HOME]`` (installers): seeds
a missing SOUL.md and updates every profile still on a shipped version.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

IDENTITY_DIR = (
    Path(__file__).resolve().parent.parent / "installer" / "skills-hidden" / "aimds-loadout" / "identity"
)
SHIPPED_HASHES_FILE = IDENTITY_DIR / "shipped-hashes.json"
# Per profile: the template hash whose notice the owner dismissed.
ACK_FILE = ".soul-template-ack"

# Profile file name → template file name. The co-worker SOUL is what every
# profile carries; a SOUL.dev.md in a profile only exists when someone copied
# it there, and is treated the same way.
VARIANT_FILES = ("SOUL.md", "SOUL.dev.md")

STATUS_MISSING = "missing"
STATUS_CURRENT = "current"
STATUS_SHIPPED = "shipped"  # an older shipped text → update in place
STATUS_EDITED = "edited"  # the owner's text → never touched


def normalized_hash(text: str) -> str:
    """sha256 of *text* with CRLF→LF, BOM and outer whitespace removed."""
    norm = text.replace("\r\n", "\n").lstrip("﻿").strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def template_text(name: str = "SOUL.md") -> str:
    """The shipped template, or ``""`` when this install has no loadout."""
    try:
        return (IDENTITY_DIR / name).read_text(encoding="utf-8")
    except OSError:
        return ""


@lru_cache(maxsize=1)
def _shipped_hashes() -> Dict[str, frozenset]:
    try:
        data = json.loads(SHIPPED_HASHES_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("shipped SOUL hashes unavailable: %s", exc)
        return {}
    return {
        name: frozenset(e["sha256"] for e in data.get(name, []) if isinstance(e, dict) and e.get("sha256"))
        for name in VARIANT_FILES
    }


def shipped_hashes(name: str = "SOUL.md") -> frozenset:
    return _shipped_hashes().get(name, frozenset())


def soul_status(path: Path, name: str = "SOUL.md") -> str:
    """Classify the SOUL file at *path* against the template *name*."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return STATUS_MISSING
    except OSError:
        return STATUS_EDITED  # unreadable: never touch it
    template = template_text(name)
    digest = normalized_hash(text)
    if template and digest == normalized_hash(template):
        return STATUS_CURRENT
    if digest in shipped_hashes(name):
        return STATUS_SHIPPED
    return STATUS_EDITED


def seed_soul(home: Path) -> bool:
    """Write the template to ``home/SOUL.md`` when the profile has none."""
    path = Path(home) / "SOUL.md"
    if path.exists():
        return False
    template = template_text("SOUL.md")
    if not template:
        return False
    path.write_text(template, encoding="utf-8")
    return True


def migrate_soul(home: Path) -> Dict[str, str]:
    """Update this profile's SOUL files that are still a shipped version.

    Returns ``{file name: status}`` for the files present, where a replaced
    file reports ``"updated"``. Edited files are left exactly as they are.
    """
    home = Path(home)
    result: Dict[str, str] = {}
    for name in VARIANT_FILES:
        path = home / name
        status = soul_status(path, name)
        if status == STATUS_MISSING:
            continue
        template = template_text(name) if status == STATUS_SHIPPED else ""
        if template:
            try:
                path.write_text(template, encoding="utf-8")
                status = "updated"
            except OSError as exc:
                logger.warning("could not update %s to the new template: %s", path, exc)
        result[name] = status
    return result


def profile_homes(default_home: Path) -> list[Path]:
    """The default profile plus every named profile under ``profiles/``."""
    default_home = Path(default_home)
    homes = [default_home]
    root = default_home / "profiles"
    if root.is_dir():
        homes += sorted(p for p in root.iterdir() if p.is_dir() and (p / "SOUL.md").exists())
    return homes


def migrate_all(default_home: Path) -> Dict[str, Dict[str, str]]:
    """Seed a missing default SOUL.md, then migrate every profile."""
    default_home = Path(default_home)
    out: Dict[str, Dict[str, str]] = {}
    for home in profile_homes(default_home):
        try:
            seeded = home == default_home and seed_soul(home)
            res = migrate_soul(home)
            if seeded:
                res["SOUL.md"] = "seeded"
            out[str(home)] = res
        except OSError as exc:
            logger.warning("SOUL template update skipped for %s: %s", home, exc)
    return out


def _read_ack(home: Path) -> str:
    try:
        return (Path(home) / ACK_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def template_notice(home: Path) -> Dict[str, Any]:
    """The backend flag behind "Neue Vorlage verfügbar" for one profile.

    ``notice`` is true only for an edited SOUL.md whose owner has not yet
    dismissed this template version; ``diff`` (owner's file → template) and
    ``template`` let the surface show and offer it.
    """
    path = Path(home) / "SOUL.md"
    status = soul_status(path)
    template = template_text("SOUL.md")
    template_hash = normalized_hash(template) if template else ""
    notice = bool(template) and status == STATUS_EDITED and _read_ack(home) != template_hash
    info: Dict[str, Any] = {"status": status, "notice": notice, "template_hash": template_hash}
    if notice:
        try:
            current = path.read_text(encoding="utf-8")
        except OSError:
            current = ""
        info["template"] = template
        info["diff"] = "".join(
            difflib.unified_diff(
                current.splitlines(keepends=True),
                template.splitlines(keepends=True),
                fromfile="SOUL.md",
                tofile="template",
            )
        )
    return info


def acknowledge_template(home: Path, template_hash: Optional[str] = None) -> str:
    """Dismiss the notice for the current (or the given) template version."""
    digest = template_hash or normalized_hash(template_text("SOUL.md"))
    (Path(home) / ACK_FILE).write_text(digest + "\n", encoding="utf-8")
    return digest


def summarize(results: Dict[str, Dict[str, str]]) -> list[str]:
    """One plain line per profile whose SOUL changed or kept a custom text."""
    lines = []
    for home, files in results.items():
        for name, status in files.items():
            if status == "updated":
                lines.append(f"{name} in {home}: updated to the new template")
            elif status == "seeded":
                lines.append(f"{name} in {home}: created from the template")
            elif status == STATUS_EDITED and name == "SOUL.md" and template_notice(Path(home))["notice"]:
                lines.append(f"{name} in {home}: customised, kept (a new template is available)")
    return lines


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        home = Path(args[0]).expanduser()
    else:
        from hermes_constants import get_hermes_home

        home = get_hermes_home()
    if not template_text("SOUL.md"):
        print(f"SOUL template not found under {IDENTITY_DIR}", file=sys.stderr)
        return 1
    home.mkdir(parents=True, exist_ok=True)
    for line in summarize(migrate_all(home)):
        print(line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
