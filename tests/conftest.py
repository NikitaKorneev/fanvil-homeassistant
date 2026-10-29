"""Exercise the independent API transport without replacing Home Assistant APIs."""

import importlib
import sys
import types
from pathlib import Path

import pytest

package = types.ModuleType("fanvil_standalone")
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components" / "fanvil")
]
sys.modules[package.__name__] = package
api = importlib.import_module("fanvil_standalone.api")
const = importlib.import_module("fanvil_standalone.const")
api.REQUEST_GAP = 0  # the real panel needs a pause; local test servers do not


@pytest.fixture
def api_module():
    return api


@pytest.fixture
def const_module():
    return const
