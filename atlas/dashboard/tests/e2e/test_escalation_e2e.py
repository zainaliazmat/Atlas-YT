"""End-to-end tests for the Slice-4 escalation UI:
  - attempt-history card on the fact-check gate
  - Guide / Kill buttons present and wired
  - no browser console errors

Uses the shared `live_server` fixture (session-scoped disposable projects dir).
We seed fix_history into the `blocked_clean` project's project.json BEFORE any
test runs, then assert the gate view renders the new section.

Safety: this test NEVER approves the blocked_clean gate (that would call Sage/LLM).
"""
from __future__ import annotations

import json
import time

import pytest

from dashboard.tests.e2e.conftest import assert_no_console_errors

# ---------------------------------------------------------------- helpers


def _go_rail(page, rail: str):
    page.click(f'.rail .ic[data-rail="{rail}"]')


def _open_gate_via_projects(page, slug: str):
    """Navigate to the gate view for `slug` the way a user would."""
    _go_rail(page, "projects")
    page.wait_for_selector(f'#pr-list .row[data-slug="{slug}"]')
    btn = page.locator(f'#pr-list .row[data-slug="{slug}"] [data-gate="{slug}"]')
    btn.first.click()
    page.wait_for_selector("#gt-main")


# ---------------------------------------------------------------- fixture: seed fix_history


@pytest.fixture(scope="module")
def fix_history_slug(live_server):
    """Injects supervisor.fix_history into the blocked_clean project so the gate
    view shows the attempt-history card.  Returns the slug."""
    slug = "blocked_clean"
    projects_dir = live_server["projects_dir"]
    pj_path = projects_dir / slug / "project.json"
    proj = json.loads(pj_path.read_text())
    proj.setdefault("supervisor", {})
    proj["supervisor"]["fix_history"] = {
        "factcheck": [
            {
                "n": 1,
                "ts": time.time() - 120,
                "instructions": "Focus on scene 3 claim about market share.",
                "flagged_before": [
                    {"claim_id": "s3c1", "claim_text": "80% market share claim"},
                    {"claim_id": "s3c2", "claim_text": "revenue doubled in 2025"},
                ],
            },
            {
                "n": 2,
                "ts": time.time() - 60,
                "instructions": "Remove the unverifiable revenue claim entirely.",
                "flagged_before": [
                    {"claim_id": "s3c1", "claim_text": "80% market share claim"},
                ],
            },
        ]
    }
    pj_path.write_text(json.dumps(proj, indent=2))
    return slug


# ---------------------------------------------------------------- tests


def test_gate_renders_attempt_history_section(page, base_url, guard_console, fix_history_slug):
    """The fix_history block appears as 'Atlas auto-fix attempts' on the fact-check gate."""
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-kpis .kpi")
    _open_gate_via_projects(page, fix_history_slug)

    # The sec heading should include 'Atlas auto-fix attempts'
    page.wait_for_selector(".gt-fix-history")
    heading = page.locator(".gt-fix-history .sec").inner_text().lower()
    assert "atlas auto-fix attempts" in heading

    # Two attempt rows
    assert page.locator(".gt-fix-attempt").count() == 2

    # First attempt shows claim ids (CSS text-transform:uppercase — compare lower)
    first_attempt = page.locator(".gt-fix-attempt").first.inner_text().lower()
    assert "attempt 1" in first_attempt
    assert "s3c1" in first_attempt

    assert_no_console_errors(guard_console)


def test_gate_renders_guide_kill_buttons(page, base_url, guard_console, fix_history_slug):
    """Guide textarea + Guide button + Kill button are present on the fact-check gate."""
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-kpis .kpi")
    _open_gate_via_projects(page, fix_history_slug)

    page.wait_for_selector("#gt-guide-text")
    page.wait_for_selector("#gt-guide")
    page.wait_for_selector("#gt-kill")

    # Guide button is disabled when textarea is empty
    assert page.locator("#gt-guide").is_disabled()

    # Typing enables the Guide button
    page.fill("#gt-guide-text", "Re-verify the market share claim with new sources.")
    assert not page.locator("#gt-guide").is_disabled()

    # Clearing disables it again
    page.fill("#gt-guide-text", "")
    assert page.locator("#gt-guide").is_disabled()

    # Kill button is always enabled (not gated on textarea)
    assert not page.locator("#gt-kill").is_disabled()

    assert_no_console_errors(guard_console)


def test_gate_kill_posts_to_api(page, base_url, guard_console, fix_history_slug):
    """Kill button POSTs /api/gate/<slug>/kill and shows 'killed' result."""
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-kpis .kpi")
    _open_gate_via_projects(page, fix_history_slug)

    page.wait_for_selector("#gt-kill")
    with page.expect_response(
        lambda r: f"/api/gate/{fix_history_slug}/kill" in r.url
    ) as resp_info:
        page.click("#gt-kill")
    resp = resp_info.value
    assert resp.ok, f"kill endpoint returned {resp.status}"

    # Result shows killed confirmation
    page.wait_for_selector("#gt-result .state-msg")
    result_text = page.locator("#gt-result .state-msg").inner_text()
    assert "killed" in result_text.lower()

    assert_no_console_errors(guard_console)
