"""The produce_video tool boundary — the new-vs-resume CONTRACT, driven offline.

The original failure was an orchestration bug, not a pipeline bug: produce_video
exposed `slug` as a REQUIRED, undescribed string (the SDK force-marks every
{name: type} param required), so the LLM filled `slug` on a fresh call and tripped
the strict resume path. These tests pin that contract at the tool layer:

- the schema the SDK actually hands the model makes `slug` OPTIONAL + described,
- the handler enforces "exactly one of {brief} (new) / {slug (+approve)} (resume)",
- a resume whose slug doesn't resolve coaches the caller to retry fresh — keyed off
  the STRUCTURED signal (failed + stage None + slug), so a mid-pipeline resume
  failure (a real stage name) is NOT told to "retry without slug",
- two same-second fresh calls for one topic don't collide on a slug.

Pure-unit + offline: every stage producer is pinned to its offline stub and
PROJECTS_DIR is redirected into tmp_path, so nothing here touches the network, an
API, or the real projects/ dir.
"""
import asyncio

import pytest

import chat_state
import contracts
import pipeline
import tools
from adapters import stubs
from progress import Progress

SILENT = Progress(sink=lambda m: None)


def _text(result):
    return result["content"][0]["text"]


def _call(args):
    """Invoke the real produce_video handler the way the SDK does, return its text."""
    produce = tools._make_produce_tool(SILENT)
    return _text(asyncio.run(produce.handler(args)))


def _project_dirs(root):
    return [d for d in root.iterdir() if d.is_dir()]


@pytest.fixture(autouse=True)
def _offline(monkeypatch, tmp_path):
    """Redirect the pipeline at tmp_path and pin every real producer to its stub."""
    monkeypatch.setattr(pipeline, "PROJECTS_DIR", tmp_path)
    pins = {
        "research": stubs.produce_research,
        "script": stubs.produce_script,
        "factcheck": stubs.produce_factcheck,
        "style": stubs.produce_style,
        "storyboard": stubs.produce_storyboard,
        "assets": stubs.produce_assets,
        "narration": stubs.produce_narration,
        "compose": stubs.produce_compose,
        "audiomix": stubs.produce_audiomix,
        "render": stubs.produce_render,
    }
    for key, producer in pins.items():
        stage = next(s for s in pipeline.STAGES if s.key == key)
        monkeypatch.setattr(stage, "producer", producer)
    # the compose stub emits a placeholder, not a composition_manifest -> contract None
    compose = next(s for s in pipeline.STAGES if s.key == "compose")
    monkeypatch.setattr(compose, "contract", None)


# ----------------------------------------------------------------------
# REFINEMENT 3 — assert the schema the SDK ACTUALLY realizes (not the dict we
# authored): the whole bug was the SDK's surprising required = list(properties).
# ----------------------------------------------------------------------
def _realized_schema():
    """The inputSchema the SDK hands the model, pulled through its real list_tools."""
    from mcp.types import ListToolsRequest
    cfg = tools.create_sdk_mcp_server("atlas", tools=[tools._make_produce_tool(SILENT)])
    server = cfg["instance"]
    res = asyncio.run(
        server.request_handlers[ListToolsRequest](ListToolsRequest(method="tools/list")))
    root = getattr(res, "root", res)
    spec = next(t for t in root.tools if t.name == "produce_video")
    return spec.inputSchema


def test_realized_sdk_schema_makes_slug_optional_and_described():
    schema = _realized_schema()
    assert schema["type"] == "object"
    # the bug: slug (and all params) were force-marked required by the SDK.
    assert schema["required"] == []
    assert "slug" not in schema["required"]
    slug_desc = schema["properties"]["slug"]["description"]
    assert "RESUME ONLY" in slug_desc and "OMIT" in slug_desc
    assert schema["properties"]["brief"]["description"]  # brief is described too


# ----------------------------------------------------------------------
# (a) fresh start: brief + no slug -> a new project dir, seeded project.json
# ----------------------------------------------------------------------
def test_brief_without_slug_creates_a_fresh_project(tmp_path):
    text = _call({"brief": "roman roads"})
    assert "PAUSED" in text  # ran Stage 1+ and reached the fact-check gate

    dirs = _project_dirs(tmp_path)
    assert len(dirs) == 1
    proj = chat_state.load_json(dirs[0] / "project.json", None)
    assert isinstance(proj, dict) and proj["slug"] == dirs[0].name
    ok, errors = contracts.validate("project", proj)
    assert ok, errors


# ----------------------------------------------------------------------
# (b) non-existent slug -> coaching, and NO project dir is created
# ----------------------------------------------------------------------
def test_nonexistent_slug_coaches_and_creates_no_project(tmp_path):
    text = _call({"slug": "ghost-project-9999"})
    assert "No project named 'ghost-project-9999'" in text
    assert "no slug" in text.lower()
    assert _project_dirs(tmp_path) == []


# ----------------------------------------------------------------------
# (c) two same-second fresh calls for the same topic -> distinct dirs (no overwrite)
# ----------------------------------------------------------------------
def test_two_same_second_fresh_calls_do_not_collide(tmp_path):
    _call({"brief": "same topic"})
    _call({"brief": "same topic"})
    dirs = sorted(d.name for d in _project_dirs(tmp_path))
    assert len(dirs) == 2, dirs


# ----------------------------------------------------------------------
# (d) neither brief nor slug -> coaching, pipeline NOT called, no project, no crash
# ----------------------------------------------------------------------
def test_bare_call_coaches_without_touching_the_pipeline(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(pipeline, "produce",
                        lambda *a, **k: called.append((a, k)) or {"status": "done"})
    text = _call({})
    assert "Nothing to produce" in text
    assert called == []                       # guard returned BEFORE the pipeline
    assert _project_dirs(tmp_path) == []


# ----------------------------------------------------------------------
# (e) a resume that fails MID-pipeline (real stage name) is NOT told to retry fresh
# ----------------------------------------------------------------------
def test_midpipeline_resume_failure_is_not_told_to_retry_without_slug(tmp_path, monkeypatch):
    text1 = _call({"brief": "midfail topic"})
    assert "PAUSED" in text1
    slug = _project_dirs(tmp_path)[0].name

    # make the stage AFTER the fact-check gate (style) blow up, then resume past the gate.
    style_stage = next(s for s in pipeline.STAGES if s.key == "style")
    def boom(pdir, topic):
        raise RuntimeError("style engine exploded")
    monkeypatch.setattr(style_stage, "producer", boom)

    text2 = _call({"slug": slug, "approve": "factcheck"})
    assert "No project named" not in text2     # NOT the slug-didn't-resolve coaching
    assert "style" in text2                     # the real failing stage surfaced
    assert "failed" in text2.lower()
