"""BAC contribution attribution ledger."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("bensz-auto-contribution")
except PackageNotFoundError:
    # A source checkout without installed metadata cannot claim a release.
    __version__ = "0+unknown"
