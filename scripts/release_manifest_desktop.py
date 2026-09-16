#!/usr/bin/env python3
"""Add the prebuilt desktop assets to ``hermes-release.json`` (AIS-353).

Usage: release_manifest_desktop.py <hermes-release.json> <asset>...

Each asset is a ``Hermes-<version>-<os>-<arch>.zip`` produced by the
``build-desktop`` job (electron-builder ``artifactName``). The manifest gets a
``desktop`` map::

    "desktop": {
      "mac-arm64": {"name": "Hermes-0.7.6-mac-arm64.zip", "sha256": "…", "size": 123},
      "win-x64":   {"name": "Hermes-0.7.6-win-x64.zip",   "sha256": "…", "size": 456}
    }

and every asset a ``<asset>.sha256`` sidecar in ``sha256sum -c`` format.
Clients that predate the field ignore it (validators skip unknown keys);
clients that know it install the signed app instead of building locally.
Keep the asset name rule in sync with hermes_cli/release_update.py
(``_DESKTOP_ASSET_RE``) and apps/desktop/electron/update-channels.cjs.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

ASSET_RE = re.compile(
    r"^Hermes-(?P<version>\d+\.\d+\.\d+(?:-rc\.\d+)?)-(?P<platform>(?:mac|win|linux)-(?:arm64|x64|ia32))\.zip$"
)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    manifest_path = Path(argv[1])
    assets = [Path(a) for a in argv[2:]]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "hermes-release-v1":
        print(f"error: {manifest_path} is not a hermes-release-v1 manifest", file=sys.stderr)
        return 1
    version = str(manifest.get("version") or "")
    desktop: dict[str, dict] = {}
    for asset in assets:
        match = ASSET_RE.match(asset.name)
        if not match:
            print(f"error: {asset.name} is not a Hermes-<version>-<os>-<arch>.zip desktop asset", file=sys.stderr)
            return 1
        if match.group("version") != version:
            print(
                f"error: {asset.name} carries version {match.group('version')} but the manifest says {version}",
                file=sys.stderr,
            )
            return 1
        platform = match.group("platform")
        if platform in desktop:
            print(f"error: two assets for platform {platform}", file=sys.stderr)
            return 1
        digest = sha256_of(asset)
        size = asset.stat().st_size
        desktop[platform] = {"name": asset.name, "sha256": digest, "size": size}
        asset.with_name(asset.name + ".sha256").write_text(f"{digest}  {asset.name}\n", encoding="utf-8")
        print(f"  {platform}: {asset.name} ({size} bytes, sha256 {digest[:12]}…)")
    manifest["desktop"] = dict(sorted(desktop.items()))
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"desktop assets recorded in {manifest_path}: {', '.join(sorted(desktop))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
