"""End-to-end tests for Slice 3: Video Detail (event history + clickable stages), the
Stage/Agent Inspector drawer (depth 2), the Fleet "now on" enrichment, and the Live
Activity feed.

Read surfaces run against the shared `live_server` (real spine, no triggering). The
failure-surface + RETRY flow uses `belt_fail_server` (a fake spine that parks a transient
failure — no engine, no LLM). The cancel-from-inspector path also uses a fake spine.
"""
from __future__ import annotations

from dashboard.tests.e2e.conftest import assert_no_console_errors


def _open_pipeline(page, base_url, slug):
    """Open a project's Video Detail page via the Projects list (deterministic nav)."""
    page.goto(base_url + "/", wait_until="load")
    page.wait_for_selector("#ov-belt")
    page.click('.rail .ic[data-rail="projects"]')
    page.wait_for_selector('#pr-list .row[data-slug="' + slug + '"]')
    page.click('#pr-list .row[data-slug="' + slug + '"]')
    page.wait_for_selector("#v-pipeline.active")
    page.wait_for_selector("#pl-body .ladder")


# ---------------------------------------------------------------- Video Detail
def test_video_detail_has_event_history_and_clickable_stages(page, base_url, e2e_slugs,
                                                              guard_console):
    _open_pipeline(page, base_url, e2e_slugs["done"])
    assert page.locator("#v-pipeline .stage.clickable").count() >= 1
    assert page.locator("#v-pipeline .hist").count() == 1     # event-history card present
    assert_no_console_errors(guard_console)


# ---------------------------------------------------------------- Stage Inspector (depth 2)
def test_stage_inspector_opens_shows_flow_and_valid_stamp(page, base_url, e2e_slugs,
                                                          guard_console):
    _open_pipeline(page, base_url, e2e_slugs["done"])
    page.click('#v-pipeline .stage.clickable[data-stage="script"]')
    page.wait_for_selector(".dw-panel .insp-head")
    assert page.locator(".dw-panel .insp-flow").count() == 1
    assert page.locator(".dw-panel .insp-head .brain").count() == 1      # effective brain
    # the gold script validates → a VALID contract stamp
    page.wait_for_selector(".dw-panel .stamp.ok")
    # Escape closes the drawer cleanly (no nested dialogs, no console errors)
    page.keyboard.press("Escape")
    page.wait_for_selector(".dw-panel", state="detached")
    assert_no_console_errors(guard_console)


def test_stage_inspector_shows_invalid_contract_stamp(page, base_url, e2e_slugs,
                                                      guard_console):
    # the `corrupt` fixture has a garbage script.json → INVALID contract verdict
    _open_pipeline(page, base_url, e2e_slugs["corrupt"])
    page.click('#v-pipeline .stage.clickable[data-stage="script"]')
    page.wait_for_selector(".dw-panel .stamp.bad")
    assert page.locator(".dw-panel .slip").count() == 1                  # rejection slip
    assert_no_console_errors(guard_console)


def test_inspector_failure_surface_and_retry(page, belt_fail_server, guard_console):
    base = belt_fail_server["base_url"]
    page.goto(base + "/", wait_until="load")
    page.wait_for_selector("#ov-belt")
    page.click("#ov-generate")
    page.fill("#dialog-root #lm-topic", "transient hiccup")
    page.click("#dialog-root .dlg-primary")
    page.wait_for_selector("#dialog-root .dlg", state="detached")
    # it parks as failed (auto-retry off)
    page.wait_for_selector("#ov-belt .spine-row .pill-state.failed", timeout=15000)
    row = page.locator("#ov-belt .spine-row").first
    slug = row.get_attribute("data-slug")
    row.click()
    page.wait_for_selector("#v-pipeline.active")
    # open the failed stage → the honest transient-failure surface with RETRY + CANCEL
    page.click('#v-pipeline .stage.clickable[data-stage="script"]')
    page.wait_for_selector(".dw-panel .insp-fail.tr")
    assert page.locator('.dw-panel [data-act="retry"]').count() == 1
    assert page.locator('.dw-panel [data-act="cancel"]').count() == 1
    page.click('.dw-panel [data-act="retry"]')
    # the retry drives it to done on the belt (no real engine — the fake succeeds 2nd time)
    page.wait_for_function(
        """slug => fetch('/api/belt').then(r => r.json())
             .then(b => (b.videos.find(v => v.slug === slug) || {}).belt_state === 'done')""",
        arg=slug, timeout=20000)
    assert_no_console_errors(guard_console)


def test_inspector_healthy_stage_is_read_only(page, base_url, e2e_slugs, guard_console):
    """A healthy (non-failed) stage shows the read-only inspector with NO fix actions — the
    UNDERSTAND/RETRY/CANCEL vocab belongs to the failure surface only. (The deterministic →
    no-retry classification itself is covered by test_stage_api.py.)"""
    _open_pipeline(page, base_url, e2e_slugs["done"])
    page.click('#v-pipeline .stage.clickable[data-stage="research"]')
    page.wait_for_selector(".dw-panel .insp-foot")          # read-only footer, no actions
    assert page.locator('.dw-panel [data-act="retry"]').count() == 0
    assert_no_console_errors(guard_console)


# ---------------------------------------------------------------- Fleet "now on"
def test_fleet_shows_current_video_for_running_agent(page, base_url, guard_console):
    page.goto(base_url + "/", wait_until="load")
    page.wait_for_selector("#ov-belt")
    page.click('.rail .ic[data-rail="fleet"]')
    page.wait_for_selector("#fl-grid .ac")
    # the `corrupt` fixture is running with the script stage running → Marlow shows "now on"
    page.wait_for_selector('#fl-grid .ac[data-agent="scriptwriter"] .nowon')
    assert "script" in page.locator(
        '#fl-grid .ac[data-agent="scriptwriter"] .nowon .onstage').inner_text().lower()
    assert_no_console_errors(guard_console)


# ---------------------------------------------------------------- Live Activity feed
def test_activity_feed_lists_events_with_initiator_plane(page, belt_server, guard_console):
    base = belt_server["base_url"]
    page.goto(base + "/", wait_until="load")
    page.wait_for_selector("#ov-belt")
    # trigger a production so the event ring has rows
    page.click("#ov-generate")
    page.fill("#dialog-root #lm-topic", "audit me")
    page.click("#dialog-root .dlg-primary")
    page.wait_for_selector("#dialog-root .dlg", state="detached")
    page.wait_for_selector("#ov-belt .spine-row")
    # open the Activity feed
    page.click('.rail .ic[data-rail="activity"]')
    page.wait_for_selector("#v-activity.active")
    page.wait_for_selector("#ac-feed .ev-row")
    assert page.locator("#ac-filters .tab").count() >= 2          # kind filters
    # the §4 audit property: the trigger row carries the CEO initiator plane
    page.wait_for_selector("#ac-feed .ev-row .plane.p-ceo")
    # filtering by an initiator plane keeps the feed coherent (no crash, rows still render)
    page.click('#ac-filters .iflt[data-i="ceo"]')
    page.wait_for_selector("#ac-feed .ev-row .plane.p-ceo")
    assert_no_console_errors(guard_console)
