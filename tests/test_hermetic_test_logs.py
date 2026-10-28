"""A test run never writes the developer's real Hermes logs (AIS-728).

``hermes_cli.main`` and ``run_agent`` resolve the Hermes home while they are
imported — at collection time, before any per-test fixture. Their log
handlers stay for the whole process, so ``tests/conftest.py`` points
HERMES_HOME at a session temp dir before the first import and refuses log
files outside the temp dir. These checks never look at the real home: they
only assert that every log file lives below the temp dir.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import pytest

# Imported at collection time on purpose, like test_cmd_update.py and the
# run_agent tests do: this is when the real home used to be bound.
import hermes_cli.main  # noqa: F401 — runs setup_logging() on import
import hermes_logging
import run_agent


def _temp_root() -> str:
    return os.path.realpath(tempfile.gettempdir())


def _is_below_temp(path: str) -> bool:
    resolved = os.path.realpath(path)
    return resolved == os.path.realpath(os.devnull) or resolved.startswith(_temp_root() + os.sep)


def _file_handlers():
    loggers = [logging.getLogger()]
    loggers += [lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger)]
    for logger in loggers:
        for handler in logger.handlers:
            if isinstance(handler, logging.FileHandler):
                yield logger.name, handler.baseFilename


def test_import_time_logging_binds_to_the_temp_home():
    assert _is_below_temp(str(run_agent._hermes_home)), run_agent._hermes_home
    names = {Path(path).name for _logger, path in _file_handlers()}
    assert {"agent.log", "errors.log"} <= names, "hermes_cli.main import should have set up file logging"
    outside = [(logger, path) for logger, path in _file_handlers() if not _is_below_temp(path)]
    assert outside == [], f"log handlers outside the test temp dir: {outside}"
    assert hermes_logging._logging_initialized


def test_a_log_file_outside_the_temp_dir_is_refused():
    target = Path(os.path.abspath(os.sep)) / "hermes-ais-728-guard" / "agent.log"

    with pytest.raises(PermissionError, match="AIS-728"):
        logging.FileHandler(str(target))
    assert not target.parent.exists()
