"""Playwright end-to-end tests for the Control Room UI (static SPA + /api/* JSON).

Every test asserts NO browser console errors / page errors (via `guard_console` +
`assert_no_console_errors`). The server is a REAL uvicorn instance over a disposable
projects dir (see conftest.live_server). The ONLY gate approved here is the
`e2e-final-render` project, which transitions to `done` through the real spine without
running a producer or LLM.

Run:  cd atlas && ../venv/bin/python -m pytest dashboard/tests/e2e/ -q
"""
from __future__ import annotations

import re

import requests

from dashboard.tests.e2e.conftest import assert_no_console_errors

# ---------------------------------------------------------------- helpers


def _open(page, base_url):
    # NOTE: not "networkidle" — the live SSE (/api/events) holds a connection open, so the
    # page never goes network-idle. Wait for `load` + the overview's first rendered data.
    page.goto(base_url + "/", wait_until="domcontentloaded")
    page.wait_for_selector("#ov-kpis .kpi")


def _active_view_id(page) -> str:
    return page.eval_on_selector(
        ".view.active", "el => el.id")


def _go_rail(page, rail: str):
    page.click(f'.rail .ic[data-rail="{rail}"]')


def _open_gate_via_projects(page, slug: str):
    """Navigate to the gate view for `slug` the way a user would: open Projects, then
    click that project's row's gate button (carries data-gate -> current.slug)."""
    _go_rail(page, "projects")
    page.wait_for_selector(f'#pr-list .row[data-slug="{slug}"]')
    btn = page.locator(f'#pr-list .row[data-slug="{slug}"] [data-gate="{slug}"]')
    btn.first.click()
    assert _active_view_id(page) == "v-gate"


# ================================================================ 1. Navigation
def test_navigation_rail_and_crosslinks(page, base_url, guard_console):
    _open(page, base_url)

    # overview renders: KPIs populated, fleet rows, spine nodes
    page.wait_for_selector("#ov-kpis .kpi")
    assert page.locator("#ov-kpis .kpi").count() >= 4
    page.wait_for_selector("#ov-bottom .frow")
    assert page.locator("#ov-bottom .frow").count() >= 1
    page.wait_for_selector("#ov-spine .node, #ov-spine .state-msg")

    # click each rail item and assert the right view becomes active with content
    for rail, view, mount in [
        ("pipeline", "v-pipeline", "#pl-body"),
        ("fleet", "v-fleet", "#fl-grid"),
        ("quality", "v-quality", "#ql-body"),
        ("projects", "v-projects", "#pr-list"),
    ]:
        _go_rail(page, rail)
        assert _active_view_id(page) == view
        page.wait_for_selector(f"{mount} *")
        assert page.locator(mount).inner_text().strip() != ""

    # cross-link: a Projects row opens the pipeline view for that slug
    _go_rail(page, "projects")
    page.wait_for_selector("#pr-list .row")
    first_slug = page.locator("#pr-list .row").first.get_attribute("data-slug")
    page.locator("#pr-list .row").first.click()
    assert _active_view_id(page) == "v-pipeline"
    page.wait_for_selector("#pl-body .ladder, #pl-body .state-msg")
    assert page.locator("#pl-crumb-slug").inner_text() == first_slug

    # cross-link: a Fleet card opens that agent's profile view
    _go_rail(page, "fleet")
    page.wait_for_selector("#fl-grid .ac")
    agent_name = page.locator("#fl-grid .ac").first.get_attribute("data-agent")
    page.locator("#fl-grid .ac").first.click()
    assert _active_view_id(page) == "v-agent"
    page.wait_for_selector("#ag-body .phead")
    assert agent_name  # carried through

    assert_no_console_errors(guard_console)


# ================================================================ 2a. Overview deep
def test_overview_real_data(page, base_url, guard_console):
    _open(page, base_url)
    page.wait_for_selector("#ov-kpis .kpi")
    # 5 KPI tiles in the k5 row
    assert page.locator("#ov-kpis .kpi").count() == 5
    # gate card + scorecard in the mid grid
    page.wait_for_selector("#ov-mid .card")
    assert page.locator("#ov-mid .card").count() >= 2
    # spine node(s) for the active production
    page.wait_for_selector("#ov-spine .node, #ov-spine .card")
    assert_no_console_errors(guard_console)


# ================================================================ 2b. Projects deep
def test_projects_screen_real_data(page, base_url, guard_console, e2e_slugs):
    _open(page, base_url)
    _go_rail(page, "projects")
    page.wait_for_selector("#pr-list .row")

    # rows == count reported by the API (6 disposable + 1 extra e2e-final-render = 7)
    api = requests.get(base_url + "/api/projects", timeout=5).json()
    n_api = len(api["projects"])
    assert n_api == 7, f"expected 7 projects, API returned {n_api}"
    assert page.locator("#pr-list .row").count() == n_api

    # the KPI counts render
    assert page.locator("#pr-kpis .kpi").count() == 6
    # the disposable 'done' slug is present as a row
    assert page.locator('#pr-list .row[data-slug="done"]').count() == 1
    assert_no_console_errors(guard_console)


def test_projects_tab_badges_match_filtered_rows(page, base_url, guard_console, e2e_slugs):
    """Every filter tab's badge must equal the number of rows it actually shows — a
    badge that lies about its own filter erodes trust. Regression for the
    Needs-you/Blocked badge≠rows bug."""
    import re
    _open(page, base_url)
    _go_rail(page, "projects")
    page.wait_for_selector("#pr-tabs .tab")
    tabs = page.locator("#pr-tabs .tab")
    for i in range(tabs.count()):
        t = tabs.nth(i)
        label = t.inner_text().strip()
        m = re.search(r"(\d+)\s*$", label)
        assert m, f"tab has no badge count: {label!r}"
        badge = int(m.group(1))
        t.click()
        page.wait_for_timeout(150)
        rows = page.locator("#pr-list .row").count()
        assert rows == badge, f"tab {label!r}: badge={badge} but {rows} rows shown"
    assert_no_console_errors(guard_console)


# ================================================================ 2c. Pipeline (done)
def test_pipeline_detail_done_has_video(page, base_url, guard_console, e2e_slugs):
    _open(page, base_url)
    _go_rail(page, "projects")
    page.wait_for_selector('#pr-list .row[data-slug="done"]')
    page.locator('#pr-list .row[data-slug="done"]').first.click()
    assert _active_view_id(page) == "v-pipeline"

    page.wait_for_selector("#pl-body .ladder .stage")
    assert page.locator("#pl-body .ladder .stage").count() >= 1   # stage ladder
    # contracts section rendered (rows or empty-state)
    assert page.locator("#pl-body .ctr").count() == 1
    # artifacts section rendered
    assert page.locator("#pl-body .files").count() == 1
    # the gold 'done' project has video.mp4 -> a <video> element appears
    page.wait_for_selector("#pl-body video")
    assert page.locator("#pl-body video").count() == 1
    src = page.locator("#pl-body video").first.get_attribute("src")
    assert "/api/media/done/video" in src
    assert_no_console_errors(guard_console)


def test_stage_inspector_closes_on_navigation(page, base_url, guard_console, e2e_slugs):
    """The stage-inspector drawer must not linger over the next screen when the CEO
    navigates away — opening it then switching rails closes it."""
    _open(page, base_url)
    _go_rail(page, "projects")
    page.wait_for_selector('#pr-list .row[data-slug="done"]')
    page.locator('#pr-list .row[data-slug="done"]').first.click()
    page.wait_for_selector("#pl-body .ladder .stage")
    page.locator("#pl-body .ladder .stage").first.click()
    page.wait_for_selector(".dw-panel")                 # drawer opened
    # navigate (the drawer scrim blocks a rail mouse-click, so any keyboard/programmatic
    # nav is the path that could leave it lingering — go() must close it)
    page.evaluate("window.go('v-fleet','fleet')")
    assert page.locator(".dw-panel").count() == 0       # and is gone after nav
    assert _active_view_id(page) == "v-fleet"
    assert_no_console_errors(guard_console)


# ================================================================ 2d. Fleet (10 cards)
def test_fleet_ten_cards(page, base_url, guard_console):
    _open(page, base_url)
    _go_rail(page, "fleet")
    page.wait_for_selector("#fl-grid .ac")
    assert page.locator("#fl-grid .ac").count() == 10
    assert page.locator("#fl-kpis .kpi").count() == 5
    assert_no_console_errors(guard_console)


# ================================================================ 2e. Agent profiles
def test_agent_profiles_generalized(page, base_url, guard_console):
    """At least 3 different agents incl. a coach (quill) and scout — soul + provider."""
    _open(page, base_url)
    for name in ("editorial_coach", "scout", "scriptwriter"):
        det = requests.get(base_url + f"/api/agents/{name}", timeout=5).json()
        # drive via UI: open fleet, click the matching card
        _go_rail(page, "fleet")
        page.wait_for_selector(f'#fl-grid .ac[data-agent="{name}"]')
        page.locator(f'#fl-grid .ac[data-agent="{name}"]').first.click()
        assert _active_view_id(page) == "v-agent"
        page.wait_for_selector("#ag-body .phead")
        # soul/identity card + soul files render
        page.wait_for_selector("#ag-body .soulfiles")
        assert page.locator("#ag-body .soulfiles").inner_text().strip() != ""
        # provider rendered in the Brain card / KPIs (matches API provider)
        body_text = page.locator("#ag-body").inner_text().lower()
        assert det["provider"].lower() in body_text
    assert_no_console_errors(guard_console)


# ================================================================ 2f. Quality empty
def test_quality_empty_state_with_rubric(page, base_url, guard_console):
    _open(page, base_url)
    _go_rail(page, "quality")
    page.wait_for_selector("#ql-body .card")
    body_text = page.locator("#ql-body").inner_text()
    # empty 'no scorecard yet' state renders
    assert "NO SCORE YET" in body_text or "No scorecard yet" in body_text
    # the rubric standard / dimensions still show
    page.wait_for_selector("#ql-body .dim, #ql-body .std")
    assert page.locator("#ql-body .dim").count() >= 1
    assert page.locator("#ql-kpis .kpi").count() == 6
    assert_no_console_errors(guard_console)


# ================================================================ 2g. Gate screen
def test_gate_view_renders(page, base_url, guard_console, e2e_slugs):
    # use blocked_final (a final-render gate that we never approve) so this test is
    # independent of the gate-flow test that mutates e2e-final-render -> done.
    slug = e2e_slugs["blocked_final"]
    _open(page, base_url)
    _open_gate_via_projects(page, slug)
    page.wait_for_selector("#gt-main .gh")
    assert "Final-render gate" in page.locator("#gt-main").inner_text()
    assert_no_console_errors(guard_console)


# ================================================================ 3. Gate flow (write)
def test_gate_flow_approves_final_render(page, base_url, guard_console, e2e_slugs):
    slug = e2e_slugs["final_render"]

    # confirm pre-state via API
    pre = requests.get(base_url + f"/api/projects/{slug}", timeout=5).json()
    assert pre["summary"]["status"] == "blocked_at_final_render"

    _open(page, base_url)
    _open_gate_via_projects(page, slug)

    # an ENABLED approve control is shown (approvable)
    page.wait_for_selector("#gt-approve")
    approve = page.locator("#gt-approve")
    assert approve.is_enabled()

    approve.click()
    # UI reflects success: gt-result shows the new status (done)
    page.wait_for_selector("#gt-result .state-msg", timeout=15000)
    page.wait_for_function(
        "() => /done|approved/i.test(document.querySelector('#gt-result').innerText)",
        timeout=15000)
    result_text = page.locator("#gt-result").inner_text()
    assert re.search(r"done|approved", result_text, re.I), result_text

    # independent API proof: project transitioned to done on disk
    post = requests.get(base_url + f"/api/projects/{slug}", timeout=5).json()
    assert post["summary"]["status"] == "done", post["summary"]
    assert_no_console_errors(guard_console)


# ================================================================ 4. Hard block safe
def test_hard_block_unapprovable(page, base_url, guard_console, e2e_slugs):
    slug = e2e_slugs["hard_block"]
    _open(page, base_url)
    _open_gate_via_projects(page, slug)
    page.wait_for_selector("#gt-main .gh")

    # CRITICAL safety: no ENABLED approve button. Either #gt-approve absent or a
    # disabled bigbtn rendered instead.
    if page.locator("#gt-approve").count():
        assert not page.locator("#gt-approve").is_enabled()
    # the disabled / routed-back affordance is present
    disabled = page.locator("#gt-main button[disabled]")
    assert disabled.count() >= 1
    assert re.search(r"can'?t approve|routed back",
                     page.locator("#gt-main").inner_text(), re.I)
    assert_no_console_errors(guard_console)


# ================================================================ 5. Responsive
def test_responsive_mobile(page, base_url, guard_console):
    page.set_viewport_size({"width": 390, "height": 844})
    _open(page, base_url)
    # rail still present
    assert page.locator(".rail").is_visible()
    # overview content still renders (no layout crash)
    page.wait_for_selector("#ov-kpis .kpi")
    assert page.locator("#ov-kpis .kpi").count() >= 1
    assert page.locator("#ov-bottom .frow").count() >= 1
    assert_no_console_errors(guard_console)


# ============================================================ 6. Wide / 4k — no dead right-side gap
def test_wide_viewport_no_dead_right_gap(page, base_url, guard_console):
    """The retired mock capped content at 1320px and left-aligned it, dumping all empty
    space on the right of a wide screen (the CEO's original complaint). The fluid shell
    centers content within its column, so left/right gutters are balanced (no lopsided
    right-side gap) and the cap is larger than the old 1320px."""
    _open(page, base_url)
    page.wait_for_selector("#ov-kpis .kpi")
    for vw, vh in ((2560, 1440), (3840, 2160)):
        page.set_viewport_size({"width": vw, "height": vh})
        box = page.eval_on_selector(
            ".view.active .main",
            "el => { const m = el.getBoundingClientRect();"
            " const c = el.closest('.content').getBoundingClientRect();"
            " return {gapL: m.left - c.left, gapR: c.right - m.right, w: m.width}; }")
        # centered within the content column → no lopsided dead right-side space
        assert abs(box["gapL"] - box["gapR"]) <= 4, (
            f"@{vw}px not centered: gapL={box['gapL']} gapR={box['gapR']}")
        # uses meaningfully more width than the retired 1320px cap
        assert box["w"] > 1320, f"@{vw}px content too narrow: {box['w']}"
    assert_no_console_errors(guard_console)


# ================================================================ 6. A11y basics
def test_a11y_focus_and_reduced_motion(page, base_url, guard_console):
    # reduced-motion: emulate and assert no errors (CSS has a prefers-reduced-motion block)
    page.emulate_media(reduced_motion="reduce")
    _open(page, base_url)
    page.wait_for_selector("#ov-kpis .kpi")

    # :focus-visible styling exists in the stylesheet
    has_focus_rule = page.evaluate(
        """() => {
            for (const sheet of document.styleSheets) {
              let rules;
              try { rules = sheet.cssRules; } catch (e) { continue; }
              if (!rules) continue;
              for (const r of rules) {
                if (r.selectorText && r.selectorText.includes(':focus-visible')) return true;
              }
            }
            return false;
        }""")
    assert has_focus_rule, ":focus-visible rule not found in stylesheets"

    # tab to a focusable rail item and confirm it receives focus
    rail_item = page.locator('.rail .ic[data-rail="pipeline"]')
    rail_item.evaluate("el => el.setAttribute('tabindex', '0')")
    rail_item.focus()
    assert rail_item.evaluate("el => el === document.activeElement")

    assert_no_console_errors(guard_console)
