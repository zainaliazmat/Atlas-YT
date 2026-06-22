"""Shared pytest fixtures for the Scout test suite.

The memory/state tests take a `tmp` parameter for an isolated temp directory but
no `tmp` fixture was defined anywhere (no project conftest), so every such test
errored with "fixture 'tmp' not found". This aliases pytest's built-in `tmp_path`
so those tests run against a real, per-test temp dir.
"""
import pytest


@pytest.fixture
def tmp(tmp_path):
    """A fresh, isolated temp directory (alias for pytest's tmp_path)."""
    return tmp_path
