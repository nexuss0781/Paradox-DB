"""Shared pytest fixtures for the parad test suite (hermetic, no network)."""

import importlib
import os
import sys
import tempfile
from pathlib import Path

# CRITICAL: parad.config computes CONFIG_DIR at import time, and importing
# anything from parad (e.g. parad.engine below) triggers that import.  Set
# PARADOX_HOME *before* that happens so the very first import is already
# hermetic.  The session fixture below re-points it at a pytest-managed
# tmp dir and re-imports config/state to pick it up.
_HOME = tempfile.mkdtemp(prefix="paradox-test-home-")
os.environ["PARADOX_HOME"] = _HOME
# Auto-generated passphrase announce writes into the config dir (~/.paradox/.env)
# which PARADOX_HOME already redirects to a pytest-managed temp dir.

import pytest

from parad.engine import Engine


@pytest.fixture(scope="session", autouse=True)
def paradox_home(tmp_path_factory):
    """Point PARADOX_HOME at a pytest-managed throwaway dir.

    CONFIG_DIR is computed at import time, so after switching the env var
    we re-import ``parad.config`` (and ``parad.state``, which reads
    CONFIG_DIR from the config module at call time) to pick it up.
    """
    home = tmp_path_factory.mktemp("paradox_home")
    old = os.environ.get("PARADOX_HOME")
    os.environ["PARADOX_HOME"] = str(home)
    for name in ("parad.config", "parad.state"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
    yield Path(home)
    if old is None:
        os.environ.pop("PARADOX_HOME", None)
    else:
        os.environ["PARADOX_HOME"] = old


@pytest.fixture
def make_engine(tmp_path):
    """Factory returning an :class:`Engine` on a temp path.

    ``make_engine("main.db", passphrase="secret")`` -> Engine.  All dbs
    are created inside the function-scoped pytest ``tmp_path``.
    """

    def _make(name="test.db", passphrase="test-passphrase"):
        db_path = tmp_path / name
        return Engine(str(db_path), passphrase)

    return _make
