"""Playwright smoke test for the governance surface in the single chat UI.

Boots the real Chainlit app (web/app.py) against a SANDBOX CEO dir (seeded through
the same boundary/ceo.state seams the app uses), then drives the browser:
  1. /digest renders the CEO digest (state + journal + budget + kill switch);
  2. /requests surfaces a pending publish CHECKPOINT, and clicking ✅ Approve
     records the resolution (the human yes) through the governance seam.

It SKIPS (never fails the suite) if Chainlit can't boot in this environment.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright, expect  # noqa: E402

ATLAS_DIR = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_up(url: str, timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status < 500:
                    return True
        except Exception:  # noqa: BLE001 — not up yet
            time.sleep(0.5)
    return False


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    base = tmp_path_factory.mktemp("govweb")
    ceo_dir = base / "ceo"

    # Seed the sandbox CEO surface through the REAL seams (so the schema matches).
    import boundary
    from ceo import state as ceo_state
    old = boundary.CEO_DIR
    boundary.CEO_DIR = ceo_dir
    try:
        ceo_state.load()                       # writes default state.json (milestones)
        boundary.ceo_log("started the Espresso 101 explainer")
        boundary.ceo_log("fact-check passed clean")
        boundary.request_from_ceo(
            "approval", "make 'Espresso 101' PUBLIC on YouTube",
            "it passed compliance and is uploaded unlisted",
            "review the report, then approve to go public")
    finally:
        boundary.CEO_DIR = old

    port = _free_port()
    chainlit = Path(sys.executable).parent / "chainlit"
    if not chainlit.exists():
        pytest.skip("chainlit CLI not found in this environment")
    env = {**os.environ, "ATLAS_CEO_DIR": str(ceo_dir),
           "ATLAS_ENV_FILE": str(base / ".env")}
    logf = open(base / "chainlit.log", "w")
    proc = subprocess.Popen(
        [str(chainlit), "run", "web/app.py", "--headless", "--host", "127.0.0.1",
         "--port", str(port)],
        cwd=str(ATLAS_DIR), env=env, stdout=logf, stderr=subprocess.STDOUT)
    base_url = f"http://127.0.0.1:{port}"
    try:
        if not _wait_up(base_url, timeout=75):
            proc.terminate()
            pytest.skip("Chainlit did not boot in time")
        yield {"url": base_url, "ceo_dir": ceo_dir}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _composer(page):
    """The Chainlit message input."""
    return page.get_by_role("textbox").last


def _send(page, text: str):
    box = _composer(page)
    expect(box).to_be_visible(timeout=30_000)
    box.click()
    # press_sequentially fires real key events so React enables Enter-to-submit
    # (fill() sets the value but Chainlit's submit can miss the synthetic change).
    box.press_sequentially(text, delay=15)
    box.press("Enter")


def test_digest_and_approval_flow(server):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(server["url"], wait_until="domcontentloaded")
            # wait for on_chat_start to finish streaming the welcome — only then is the
            # websocket session ready to RECEIVE a message (typing earlier is dropped).
            expect(page.get_by_text("In the room")).to_be_visible(timeout=60_000)
            expect(_composer(page)).to_be_visible(timeout=15_000)

            # 1. DIGEST
            _send(page, "/digest")
            expect(page.get_by_text("CEO Digest")).to_be_visible(timeout=30_000)
            expect(page.get_by_text("fact-check passed clean")).to_be_visible(timeout=30_000)
            expect(page.get_by_text("Budget meter")).to_be_visible(timeout=10_000)
            expect(page.get_by_text("Kill switch")).to_be_visible(timeout=10_000)

            # 2. APPROVAL FLOW — a pending publish checkpoint, approved inline.
            # The Approve button only exists if /requests surfaced the checkpoint.
            _send(page, "/requests")
            approve = page.get_by_role("button", name="Approve")
            expect(approve).to_be_visible(timeout=30_000)
            approve.click()
            expect(page.get_by_text("Approved", exact=False).first).to_be_visible(
                timeout=30_000)
        finally:
            browser.close()

    # the human's yes was recorded through the governance seam (not orchestration)
    resolutions = server["ceo_dir"] / "request_resolutions.jsonl"
    rows = [json.loads(l) for l in resolutions.read_text().splitlines() if l.strip()]
    assert any(r["decision"] == "approved" for r in rows)
