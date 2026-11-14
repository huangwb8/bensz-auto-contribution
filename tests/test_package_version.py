"""Keep audit tool versions tied to distribution metadata."""

from pathlib import Path
import runpy
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch


PACKAGE_INIT = Path(__file__).resolve().parents[1] / "src" / "bac" / "__init__.py"


def test_runtime_version_follows_distribution_metadata():
    with patch("importlib.metadata.version", return_value="9.8.7"):
        assert runpy.run_path(str(PACKAGE_INIT))["__version__"] == "9.8.7"


def test_uninstalled_checkout_does_not_claim_a_release_version():
    with patch("importlib.metadata.version", side_effect=PackageNotFoundError):
        assert runpy.run_path(str(PACKAGE_INIT))["__version__"] == "0+unknown"
