"""End-to-end (Playwright) fixtures: a REAL uvicorn server over a DISPOSABLE
projects dir, plus a console/page-error guard that fails any test on browser errors.

Run from the atlas/ dir:
    ../venv/bin/python -m pytest dashboard/tests/e2e/ -q

What this gives the tests
-------------------------
* `live_server` — boots `create_app(projects_dir=<tmp copy>)` on a free port in a
  background thread, wires `app.state.produce_fn = partial(pipeline.produce, root=<tmp>)`
  so the ONE sanctioned gate write runs the REAL spine against the disposable dir, waits
  for `/healthz` 200, and yields `{base_url, projects_dir, slugs}`. Torn down after.
* `e2e_slugs` — the slug map, incl. the extra `e2e-final-render` project (a full copy of
  the gold project flipped to `blocked_at_final_render` with EVERY stage still `done`).
  Approving THAT gate transitions the project to `done` via the spine WITHOUT running any
  heavy producer or LLM — it is the ONLY project the e2e suite actually approves.
* `guard_console` (autouse) — collects console error-level messages + page errors for the
  `page`; `assert_no_console_errors(page)` (called at the end of each test) fails if any
  appeared.

Safety: ANTHROPIC_API_KEY is never set here. The factcheck `blocked_clean` project is
NEVER approved in the UI (that would re-run Sage's real engine/LLM). Only
`e2e-final-render` is approved. Nothing touches the real atlas/projects or chat_state.json.
"""
from __future__ import annotations

import contextlib
import functools
import json
import pathlib
import shutil
import socket
import threading
import time

import pytest
import requests
import uvicorn

from dashboard.app import create_app
from dashboard.tests import fixtures

ATLAS = pathlib.Path(__file__).resolve().parents[3]
GOLD = ATLAS / "projects" / "gpt-4o-vs-claude-vs-gemini-vs-deepseek-comparison--20260621-013345-67a3"

# The extra approvable project: a gold copy flipped to a final-render block but with
# every stage left `done`, so approving advances it to `done` with no producer/LLM run.
FINAL_RENDER_SLUG = "e2e-final-render"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_final_render_blocked(projects_dir: pathlib.Path) -> str:
    """Full copy of the gold project, mutated to blocked_at_final_render (stages done)."""
    dst = projects_dir / FINAL_RENDER_SLUG
    shutil.copytree(GOLD, dst)
    pj = json.loads((dst / "project.json").read_text())
    pj["slug"] = FINAL_RENDER_SLUG
    pj["title"] = "E2E final-render approvable"
    pj["status"] = "blocked_at_final_render"
    pj.setdefault("gates", {}).setdefault("final_render", {})
    pj["gates"]["final_render"]["status"] = "blocked"
    # leave ALL stages `done` — that is what makes the approve a no-producer transition
    (dst / "project.json").write_text(json.dumps(pj, indent=2))
    return FINAL_RENDER_SLUG


@pytest.fixture(scope="session")
def live_server(tmp_path_factory):
    """Real uvicorn server over a disposable projects dir; yields connection info."""
    if not GOLD.exists():
        pytest.skip("gold reference project not present")

    tmp_root = tmp_path_factory.mktemp("e2e_projects")
    projects_dir, slugs = fixtures.build_projects(tmp_root)
    slugs = dict(slugs)
    slugs["final_render"] = _make_final_render_blocked(projects_dir)

    import pipeline  # lazy, mirrors the real dashboard write path

    app = create_app(projects_dir=projects_dir)
    # the ONE sanctioned mutation runs the REAL spine, pinned to the disposable dir
    app.state.produce_fn = functools.partial(pipeline.produce, root=projects_dir)

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # wait for /healthz before yielding
    deadline = time.time() + 30
    while time.time() < deadline:
        with contextlib.suppress(Exception):
            if requests.get(f"{base_url}/healthz", timeout=1).status_code == 200:
                break
        time.sleep(0.1)
    else:
        server.should_exit = True
        raise RuntimeError("live server did not become healthy in time")

    try:
        yield {"base_url": base_url, "projects_dir": projects_dir, "slugs": slugs}
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@pytest.fixture(scope="session")
def base_url(live_server) -> str:
    # Overrides pytest-base-url's session-scoped `base_url` with our live server URL.
    return live_server["base_url"]


@pytest.fixture(scope="session")
def e2e_slugs(live_server) -> dict:
    return live_server["slugs"]


# ---------------------------------------------------------------- console guard
class _ConsoleGuard:
    def __init__(self):
        self.errors: list[str] = []

    def attach(self, page):
        def on_console(msg):
            if msg.type == "error":
                self.errors.append(f"console.error: {msg.text}")

        def on_pageerror(exc):
            self.errors.append(f"pageerror: {exc}")

        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        return self


@pytest.fixture
def guard_console(page) -> _ConsoleGuard:
    return _ConsoleGuard().attach(page)


def assert_no_console_errors(guard: _ConsoleGuard):
    assert not guard.errors, "browser console/page errors:\n" + "\n".join(guard.errors)
