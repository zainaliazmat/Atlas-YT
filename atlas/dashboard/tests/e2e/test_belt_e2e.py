"""End-to-end tests for the live belt + launch modal + needs-you tray (Slice 2 frontend).

Belt-render + launch-modal tests run against the shared `live_server` (existing fixture
projects, no triggering). Trigger / cancel / many-in-flight tests use the function-scoped
`belt_server`, whose produce_fn is a fast fake (no engine, no LLM).
"""
from __future__ import annotations

from dashboard.tests.e2e.conftest import assert_no_console_errors


# ---------------------------------------------------------------- belt render
def test_belt_renders_stations_and_rows(page, base_url, guard_console):
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    page.wait_for_selector("#ov-belt .stations .station")
    assert page.locator("#ov-belt .stations .station").count() == 10   # the 10-station strip
    assert page.locator("#ov-belt .spine-row").count() >= 1            # fixture projects
    assert_no_console_errors(guard_console)


def test_needs_you_tray_lists_blocked(page, base_url, guard_console):
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    page.wait_for_selector("#ov-needs")
    # the fixtures include blocked projects (hard-block + final-render) → tray items
    assert page.locator("#ov-needs .tray-item").count() >= 1
    assert_no_console_errors(guard_console)


def test_spine_row_opens_pipeline(page, base_url, guard_console):
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    page.wait_for_selector("#ov-belt .spine-row")
    slug = page.locator("#ov-belt .spine-row").first.get_attribute("data-slug")
    page.locator("#ov-belt .spine-row").first.click()
    assert page.eval_on_selector(".view.active", "el => el.id") == "v-pipeline"
    assert page.locator("#pl-crumb-slug").inner_text() == slug
    assert_no_console_errors(guard_console)


# ---------------------------------------------------------------- launch modal craft
def test_launch_modal_opens_validates_and_escapes(page, base_url, guard_console):
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    page.click("#ov-generate")
    page.wait_for_selector("#dialog-root .dlg #lm-topic")
    # empty submit is rejected client-side (NO /api/trigger fired against the real spine)
    page.click("#dialog-root .dlg-primary")
    assert page.locator("#lm-err .warn").count() == 1
    # escape closes a T1 modal and returns cleanly (no nested dialogs)
    page.keyboard.press("Escape")
    page.wait_for_selector("#dialog-root .dlg", state="detached")
    assert page.locator("#dialog-root .dlg").count() == 0
    assert_no_console_errors(guard_console)


# ---------------------------------------------------------------- trigger flow (fake spine)
def test_trigger_puts_card_on_belt_and_completes(page, belt_server, guard_console):
    page.goto(belt_server["base_url"] + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    page.click("#ov-generate")
    page.fill("#dialog-root #lm-topic", "noise cancelling headphones")
    page.click("#dialog-root .dlg-primary")
    # modal closes; a card appears on the belt; it reaches done (SSE-driven refresh)
    page.wait_for_selector("#dialog-root .dlg", state="detached")
    page.wait_for_selector("#ov-belt .spine-row")
    page.wait_for_selector("#ov-belt .spine-row .pill-state.done", timeout=15000)
    assert page.locator("#ov-belt .spine-row").count() >= 1
    assert_no_console_errors(guard_console)


def test_many_in_flight(page, belt_server, guard_console):
    page.goto(belt_server["base_url"] + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    for i in range(3):
        page.evaluate(
            """t => fetch('/api/trigger', {method:'POST',
               headers:{'Content-Type':'application/json'},
               body: JSON.stringify({topic: t})})""", f"video {i}")
    page.wait_for_function(
        "() => document.querySelectorAll('#ov-belt .spine-row').length >= 3",
        timeout=15000)
    assert page.locator("#ov-belt .spine-row").count() >= 3
    assert_no_console_errors(guard_console)


def test_rerun_split_button_reruns_from_pipeline(page, belt_server, guard_console):
    """A settled video's pipeline page offers a Re-run split-button: the caret lists the
    previously-run stations, and the main button POSTs /api/rerun to re-run from start."""
    page.goto(belt_server["base_url"] + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    page.click("#ov-generate")
    page.fill("#dialog-root #lm-topic", "rerun this one")
    page.click("#dialog-root .dlg-primary")
    page.wait_for_selector("#dialog-root .dlg", state="detached")
    page.wait_for_selector("#ov-belt .spine-row .pill-state.done", timeout=15000)
    # open the pipeline detail for the finished video
    page.locator("#ov-belt .spine-row").first.click()
    page.wait_for_selector("#pl-rerun")
    # the caret opens a menu of previously-run stations (≥ 1 since the video finished)
    page.click("#pl-rerun-caret")
    page.wait_for_selector("#pl-rerun-menu .ri")
    assert page.locator("#pl-rerun-menu .ri").count() >= 1
    page.click("#pl-rerun-caret")  # close the menu
    # the main button re-runs from the start: POST /api/atlas/request (intent=rerun) succeeds
    with page.expect_response(lambda r: "/api/atlas/request" in r.url) as ri:
        page.click("#pl-rerun")
    assert ri.value.ok
    assert_no_console_errors(guard_console)


def test_cancel_running_video_from_belt(page, belt_server, guard_console):
    page.goto(belt_server["base_url"] + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    page.click("#ov-generate")
    page.fill("#dialog-root #lm-topic", "cancel this one")
    page.click("#dialog-root .dlg-primary")
    page.wait_for_selector("#dialog-root .dlg", state="detached")
    # click the row's cancel while it's still moving down the line
    page.wait_for_selector("#ov-belt .spine-row .row-act.danger", timeout=10000)
    page.locator("#ov-belt .spine-row .row-act.danger").first.click()
    page.wait_for_selector("#ov-belt .spine-row .pill-state.cancelled", timeout=15000)
    assert_no_console_errors(guard_console)
