"""Minimal smoke tests for the Phase 0 package foundation."""

import orqen


def test_package_version() -> None:
    assert orqen.__version__ == "0.2.0"


def test_public_exports_are_accessible() -> None:
    for symbol in orqen.__all__:
        assert hasattr(orqen, symbol), f"Missing exported symbol: {symbol}"
