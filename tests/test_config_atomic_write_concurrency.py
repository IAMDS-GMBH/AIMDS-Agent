"""Concurrency tests for config.yaml's atomic-write + advisory-lock path.

Verifies the fix for a data-loss bug: ``upsert_aimds_defaults.py`` used to
write config.yaml with a non-atomic ``path.write_text()`` call, and there
was no coordination between it (running as a subprocess during
``hermes update``) and any other writer of the same file (desktop app,
gateway, another CLI session). A race between two writers could splice
YAML keys onto one physical line, producing an unparseable config.yaml.

These tests confirm that concurrent writers using ``atomic_yaml_write`` /
``advisory_file_lock`` always leave the file as one complete write or the
other -- never an interleaved/corrupted mix.
"""

import threading
import time
from pathlib import Path
from unittest.mock import patch

import yaml

from utils import advisory_file_lock, atomic_yaml_write


def _slow_yaml_dump(data, f, **kwargs):
    """Stand-in for yaml.dump that writes in two chunks with a delay between
    them, to make an interleaving race far more likely to manifest if the
    atomic-write + lock protections were broken."""
    text = yaml.safe_dump(data, default_flow_style=kwargs.get("default_flow_style", False), sort_keys=kwargs.get("sort_keys", False))
    mid = len(text) // 2
    f.write(text[:mid])
    time.sleep(0.05)
    f.write(text[mid:])


class TestAtomicYamlWriteConcurrency:
    def test_concurrent_atomic_writes_never_interleave(self, tmp_path):
        target = tmp_path / "config.yaml"
        target.write_text(yaml.safe_dump({"initial": True}), encoding="utf-8")

        payload_a = {"writer": "a", "value": "x" * 200}
        payload_b = {"writer": "b", "value": "y" * 200}

        errors = []

        def _write(payload):
            try:
                with patch("utils.yaml.dump", side_effect=_slow_yaml_dump):
                    with advisory_file_lock(target, timeout=5.0):
                        atomic_yaml_write(target, payload)
            except Exception as exc:  # pragma: no cover - surfaced via errors list
                errors.append(exc)

        t1 = threading.Thread(target=_write, args=(payload_a,))
        t2 = threading.Thread(target=_write, args=(payload_b,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors

        final = yaml.safe_load(target.read_text(encoding="utf-8"))
        # The file must be fully one writer's payload or the other's --
        # never a partial/interleaved mix of both.
        assert final in (payload_a, payload_b)

    def test_advisory_lock_serializes_writers(self, tmp_path):
        target = tmp_path / "config.yaml"
        order: list[str] = []

        def _hold_lock(name, hold_time):
            with advisory_file_lock(target, timeout=5.0):
                order.append(f"{name}-start")
                time.sleep(hold_time)
                order.append(f"{name}-end")

        t1 = threading.Thread(target=_hold_lock, args=("first", 0.1))
        t2 = threading.Thread(target=_hold_lock, args=("second", 0.0))
        t1.start()
        time.sleep(0.02)  # ensure t1 acquires the lock first
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # The second writer's start must not appear between the first
        # writer's start and end -- i.e. the lock actually serialized them.
        assert order.index("second-start") > order.index("first-end")

    def test_advisory_lock_fails_open_on_stale_lock(self, tmp_path):
        try:
            import fcntl
        except ImportError:
            import pytest
            pytest.skip("fcntl not available on this platform")

        target = tmp_path / "config.yaml"
        lock_path = target.with_name(target.name + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Simulate an orphaned lock held by another (still-alive) process by
        # holding it in this same process on a separate file descriptor --
        # flock is per-open-file-description, so this blocks a second
        # acquisition attempt just like a real stale lock would.
        blocker_fd = open(lock_path, "a+", encoding="utf-8")
        fcntl.flock(blocker_fd, fcntl.LOCK_EX)
        try:
            start = time.monotonic()
            with advisory_file_lock(target, timeout=0.3):
                # Must proceed here even though the lock couldn't be
                # acquired -- fail-open, not hang/raise.
                atomic_yaml_write(target, {"proceeded": True})
            elapsed = time.monotonic() - start

            assert elapsed < 5.0  # bounded by the short timeout, not hung
            assert yaml.safe_load(target.read_text(encoding="utf-8")) == {"proceeded": True}
        finally:
            fcntl.flock(blocker_fd, fcntl.LOCK_UN)
            blocker_fd.close()
