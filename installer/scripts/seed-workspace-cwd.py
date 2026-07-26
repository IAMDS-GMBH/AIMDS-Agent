#!/usr/bin/env python3
"""
Seed Hermes Desktop's Electron localStorage with the correct workspace-cwd.

The Desktop app stores hermes.desktop.workspace-cwd in a Chromium LevelDB
Local Storage database. This entry overrides config.yaml's terminal.cwd on
every launch. This script creates a minimal valid LevelDB containing just
that one key so the app picks up the right directory from the very first run.

Usage:
    python3 seed-workspace-cwd.py <leveldb_dir> <workspace_path>

Example (macOS):
    python3 seed-workspace-cwd.py \
        "$HOME/Library/Application Support/Hermes/Local Storage/leveldb" \
        "$HOME/Documents/AIMDS-Suite-WorkingDirectory"
"""

import os
import struct
import sys

# ── CRC32C (Castagnoli) — pure Python, no deps ────────────────────────────────


def _crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0x82F63B78
            else:
                crc >>= 1
    return crc ^ 0xFFFFFFFF


def _masked_crc(data: bytes) -> int:
    """LevelDB stores a rotated+offset CRC rather than raw CRC32C."""
    crc = _crc32c(data)
    return (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF


# ── Varint encoding (LevelDB / protobuf style) ────────────────────────────────


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


# ── LevelDB log record ────────────────────────────────────────────────────────


def _log_record(payload: bytes, record_type: int = 1) -> bytes:
    """Wrap payload in a LevelDB log record (type 1 = FULL)."""
    crc = _masked_crc(bytes([record_type]) + payload)
    header = struct.pack("<IHB", crc, len(payload), record_type)
    return header + payload


# ── LevelDB WriteBatch ────────────────────────────────────────────────────────


def _write_batch(kv_pairs: list, seq: int = 1) -> bytes:
    """Encode a sequence of (key, value) bytes pairs as a WriteBatch."""
    batch = struct.pack("<QI", seq, len(kv_pairs))
    for key, value in kv_pairs:
        batch += b"\x01"  # kTypeValue
        batch += _varint(len(key)) + key
        batch += _varint(len(value)) + value
    return batch


# ── LevelDB VersionEdit (minimal MANIFEST entry) ─────────────────────────────


def _version_edit(log_number: int, next_file: int, last_seq: int) -> bytes:
    comparator = b"leveldb.BytewiseComparator"
    ve = b"\x01" + _varint(len(comparator)) + comparator  # kComparator
    ve += b"\x02" + _varint(log_number)  # kLogNumber
    ve += b"\x03" + _varint(next_file)  # kNextFileNumber
    ve += b"\x04" + _varint(last_seq)  # kLastSequence
    return ve


# ── Main: create the LevelDB directory ───────────────────────────────────────


def seed(db_path: str, workspace_cwd: str) -> None:
    os.makedirs(db_path, exist_ok=True)

    # Chromium localStorage key/value encoding:
    #   key:   b'_file://\x00\x01' + key_name.encode('utf-8')
    #   value: b'\x01'             + value.encode('utf-8')
    ldb_key = b"_file://\x00\x01" + b"hermes.desktop.workspace-cwd"
    ldb_val = b"\x01" + workspace_cwd.encode("utf-8")

    batch = _write_batch([(ldb_key, ldb_val)], seq=1)
    log_rec = _log_record(batch)

    # 000001.log  — the WAL that contains our one WriteBatch
    with open(os.path.join(db_path, "000001.log"), "wb") as f:
        f.write(log_rec)

    # MANIFEST-000002 — minimal VersionEdit
    manifest_data = _version_edit(log_number=1, next_file=3, last_seq=1)
    manifest_record = _log_record(manifest_data)
    with open(os.path.join(db_path, "MANIFEST-000002"), "wb") as f:
        f.write(manifest_record)

    # CURRENT — points to our MANIFEST
    with open(os.path.join(db_path, "CURRENT"), "w", encoding="utf-8", newline="\n") as f:
        f.write("MANIFEST-000002\n")

    # LOCK — empty file, signals database ownership
    open(os.path.join(db_path, "LOCK"), "w", encoding="utf-8").close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <leveldb_dir> <workspace_path>", file=sys.stderr)
        sys.exit(1)

    db_path, workspace_cwd = sys.argv[1], sys.argv[2]
    seed(db_path, workspace_cwd)
    print(f"Seeded workspace-cwd = {workspace_cwd!r}  ->  {db_path}")
