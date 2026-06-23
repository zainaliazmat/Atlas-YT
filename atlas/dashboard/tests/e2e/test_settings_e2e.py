"""End-to-end tests for Slice 4 — the Settings page (niches editor + defaults + channels
shell) and the launch-modal niche pills that read from settings.

Uses `belt_server` (function-scoped, isolated settings_path + fast fake spine) so a PUT to
/api/settings and a trigger never touch real state or a real engine.
"""
from __future__ import annotations

from dashboard.tests.e2e.conftest import assert_no_console_errors


def _open_settings(page, base):
    page.goto(base + "/", wait_until="load")
    page.wait_for_selector("#ov-belt")
    page.click('.rail .ic[data-rail="settings"]')
    page.wait_for_selector("#v-settings.active")
    page.wait_for_selector("#set-body .card")


def test_settings_page_renders_quota_and_sections(page, belt_server, guard_console):
    _open_settings(page, belt_server["base_url"])
    # the channels shell shows the shared ~6/day quota ceiling (spec §9)
    page.wait_for_selector("#v-settings .quota .qn")
    assert page.locator("#v-settings .quota .qn").inner_text().strip() == "6"
    assert "shared across all channels" in page.locator(
        "#v-settings .quota").inner_text().lower()
    assert page.locator("#add-niche").count() == 1
    assert page.locator("#add-channel").count() == 1
    assert_no_console_errors(guard_console)


def test_add_niche_save_and_persist(page, belt_server, guard_console):
    base = belt_server["base_url"]
    _open_settings(page, base)
    page.click("#add-niche")
    page.fill("#v-settings .srow.niche .f-name", "noise-cancelling tech")
    # choose the long default via the per-niche length toggle
    page.click('#v-settings .srow.niche .seg-toggle[data-field="default_length"] button[data-v="long"]')
    page.click("#set-save")
    page.wait_for_selector("#set-state .ok")           # "Saved ✓"
    # round-trips: the API reflects it, and a reload re-renders it
    saved = page.evaluate("() => fetch('/api/settings').then(r => r.json())")
    # (evaluate returns a promise → Playwright awaits it)
    assert any(n["name"] == "noise-cancelling tech" and n["default_length"] == "long"
               for n in saved["niches"])
    page.reload(wait_until="load")
    page.click('.rail .ic[data-rail="settings"]')
    page.wait_for_selector('#v-settings .srow.niche .f-name')
    assert page.locator("#v-settings .srow.niche .f-name").first.input_value() == "noise-cancelling tech"
    assert_no_console_errors(guard_console)


def test_add_channel_shows_state_and_verification_flags(page, belt_server, guard_console):
    _open_settings(page, belt_server["base_url"])
    page.click("#add-channel")
    page.wait_for_selector("#v-settings .chan")
    # a fresh channel reads as disconnected + both verification flags present + Connect shelled
    assert page.locator("#v-settings .chan .conn").first.inner_text().lower().strip() == "disconnected"
    assert page.locator("#v-settings .chan .vf").count() >= 2          # the two YouTube flags
    assert page.locator("#v-settings .chan .conn-btn[disabled]").count() >= 1  # honest: arrives w/ Herald
    assert_no_console_errors(guard_console)


def test_launch_modal_niche_pills_from_settings(page, belt_server, guard_console):
    base = belt_server["base_url"]
    # seed a niche via the API, then open the launch modal — the pill should appear
    page.goto(base + "/", wait_until="load")
    page.wait_for_selector("#ov-belt")
    page.evaluate("""() => fetch('/api/settings', {method:'PUT',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({niches:[{name:'space history', default_length:'long'}]})})""")
    page.click("#ov-generate")
    page.wait_for_selector("#dialog-root #lm-niches .pill-opt")
    pill = page.locator('#dialog-root #lm-niches .pill-opt', has_text="space history")
    pill.click()
    # selecting the niche pill flips the length toggle to the niche default (long)
    assert "on" in (pill.get_attribute("class") or "")
    assert "on" in (page.locator('#dialog-root #lm-len button[data-v="long"]').get_attribute("class") or "")
    page.keyboard.press("Escape")
    page.wait_for_selector("#dialog-root .dlg", state="detached")
    assert_no_console_errors(guard_console)
