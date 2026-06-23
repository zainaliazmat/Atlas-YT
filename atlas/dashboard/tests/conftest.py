"""Pytest fixtures: disposable projects dir + TestClient over a tmp-pointed app.

Every fixture is function-scoped and tmp-backed so no test ever touches the real
`atlas/projects/` or the real `atlas/chat_state.json`.
"""
from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from dashboard.app import create_app
from dashboard.tests import fixtures


@pytest.fixture
def disposable_projects(tmp_path) -> tuple[pathlib.Path, dict]:
    return fixtures.build_projects(tmp_path)


@pytest.fixture
def slugs(disposable_projects) -> dict:
    return disposable_projects[1]


@pytest.fixture
def client(disposable_projects):
    pdir, _ = disposable_projects
    app = create_app(projects_dir=pdir)
    with TestClient(app) as c:
        c._app = app  # expose for tests that inject app.state.produce_fn / session
        yield c


@pytest.fixture
def empty_client(tmp_path):
    pdir = fixtures.build_empty(tmp_path)
    app = create_app(projects_dir=pdir)
    with TestClient(app) as c:
        c._app = app
        yield c
