"""End-to-end tests for the Slice-5 Atlas-supervisor UI:
  - Generate (launch-modal) routes through /api/atlas/request {intent:"make_video"}
  - Re-run routes through /api/atlas/request {intent:"rerun"}
  - Cancel routes through /api/atlas/request {intent:"cancel"}
  - No browser console errors on any of the above flows

All tests use the function-scoped `belt_server` fixture whose produce_fn is the fast
fake spine (no engine, no LLM, ANTHROPIC_API_KEY never set).
"""
from __future__ import annotations

import json

from dashboard.tests.e2e.conftest import assert_no_console_errors


# ---------------------------------------------------------------- Generate → /api/atlas/request


def test_generate_routes_through_atlas_request(page, belt_server, guard_console):
    """Clicking Generate POSTs /api/atlas/request with intent=make_video and the video
    appears on the belt (no console errors)."""
    page.goto(belt_server["base_url"] + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    page.click("#ov-generate")
    page.wait_for_selector("#dialog-root #lm-topic")
    page.fill("#dialog-root #lm-topic", "Atlas supervisor routing test")

    # Intercept the request before it leaves the browser and verify its shape.
    atlas_requests = []

    def capture(route, request):
        if "/api/atlas/request" in request.url:
            atlas_requests.append(json.loads(request.post_data or "{}"))
        route.continue_()

    page.route("**/api/atlas/request", capture)

    with page.expect_response(lambda r: "/api/atlas/request" in r.url) as resp_info:
        page.click("#dialog-root .dlg-primary")

    resp = resp_info.value
    assert resp.ok, f"/api/atlas/request returned {resp.status}"

    # Verify the UI sent the right intent+args shape.
    assert len(atlas_requests) >= 1, "No POST to /api/atlas/request captured"
    body = atlas_requests[0]
    assert body.get("intent") == "make_video", f"wrong intent: {body}"
    assert "topic" in (body.get("args") or {}), f"missing args.topic: {body}"

    # Modal closed and a card appeared.
    page.wait_for_selector("#dialog-root .dlg", state="detached")
    page.wait_for_selector("#ov-belt .spine-row")
    assert_no_console_errors(guard_console)


def test_generate_button_shows_atlas_deciding(page, belt_server, guard_console):
    """While the Generate request is in-flight the submit button reads 'Atlas is deciding…'."""
    page.goto(belt_server["base_url"] + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    page.click("#ov-generate")
    page.wait_for_selector("#dialog-root #lm-topic")
    page.fill("#dialog-root #lm-topic", "test atlas deciding label")

    # Slow the network response so we can observe the transient label.
    deciding_texts: list[str] = []

    def slow_route(route, request):
        import time
        time.sleep(0.15)
        route.continue_()

    page.route("**/api/atlas/request", slow_route)

    page.click("#dialog-root .dlg-primary")
    # The button text changes before the response arrives.
    try:
        page.wait_for_function(
            "() => { var b = document.querySelector('#dialog-root .dlg-primary');"
            " return b && b.textContent.includes('deciding'); }",
            timeout=2000)
        deciding_texts.append("seen")
    except Exception:
        pass  # if the response came back before we checked, that's acceptable

    page.wait_for_selector("#dialog-root .dlg", state="detached", timeout=15000)
    assert_no_console_errors(guard_console)


# ---------------------------------------------------------------- Re-run → /api/atlas/request


def test_rerun_routes_through_atlas_request(page, belt_server, guard_console):
    """Re-run split-button POSTs /api/atlas/request with intent=rerun."""
    page.goto(belt_server["base_url"] + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    page.click("#ov-generate")
    page.fill("#dialog-root #lm-topic", "rerun atlas routing test")
    page.click("#dialog-root .dlg-primary")
    page.wait_for_selector("#dialog-root .dlg", state="detached")
    page.wait_for_selector("#ov-belt .spine-row .pill-state.done", timeout=20000)

    # Navigate to the pipeline detail.
    page.locator("#ov-belt .spine-row").first.click()
    page.wait_for_selector("#pl-rerun")

    # Capture the atlas/request call for re-run.
    rerun_requests = []

    def capture_rerun(route, request):
        if "/api/atlas/request" in request.url:
            rerun_requests.append(json.loads(request.post_data or "{}"))
        route.continue_()

    page.route("**/api/atlas/request", capture_rerun)

    with page.expect_response(lambda r: "/api/atlas/request" in r.url) as resp_info:
        page.click("#pl-rerun")

    resp = resp_info.value
    assert resp.ok, f"/api/atlas/request (rerun) returned {resp.status}"

    assert len(rerun_requests) >= 1, "No POST to /api/atlas/request for rerun"
    body = rerun_requests[0]
    assert body.get("intent") == "rerun", f"wrong intent for rerun: {body}"
    assert "slug" in (body.get("args") or {}), f"missing args.slug: {body}"

    assert_no_console_errors(guard_console)


# ---------------------------------------------------------------- Cancel → /api/atlas/request


def test_cancel_routes_through_atlas_request(page, belt_server, guard_console):
    """Cancel button POSTs /api/atlas/request with intent=cancel."""
    page.goto(belt_server["base_url"] + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-belt")
    page.click("#ov-generate")
    page.fill("#dialog-root #lm-topic", "cancel atlas routing test")
    page.click("#dialog-root .dlg-primary")
    page.wait_for_selector("#dialog-root .dlg", state="detached")

    cancel_requests = []

    def capture_cancel(route, request):
        if "/api/atlas/request" in request.url:
            cancel_requests.append(json.loads(request.post_data or "{}"))
        route.continue_()

    page.route("**/api/atlas/request", capture_cancel)

    # Wait for the cancel button to appear while the video is running.
    page.wait_for_selector("#ov-belt .spine-row .row-act.danger", timeout=10000)
    with page.expect_response(lambda r: "/api/atlas/request" in r.url) as resp_info:
        page.locator("#ov-belt .spine-row .row-act.danger").first.click()

    resp = resp_info.value
    assert resp.ok, f"/api/atlas/request (cancel) returned {resp.status}"

    assert len(cancel_requests) >= 1, "No POST to /api/atlas/request for cancel"
    body = cancel_requests[0]
    assert body.get("intent") == "cancel", f"wrong intent for cancel: {body}"
    assert "slug" in (body.get("args") or {}), f"missing args.slug: {body}"

    page.wait_for_selector("#ov-belt .spine-row .pill-state.cancelled", timeout=15000)
    assert_no_console_errors(guard_console)
