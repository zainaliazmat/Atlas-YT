"""The production spine: playbook ordering, contract validation per stage, the two
human gates as pause-and-resume, and the fact-check block that cannot be approved away.

Pure-unit + offline: the default producers are the stub specialists, so nothing here
touches the network or an API. Progress is sent to a silent sink.
"""
import pathlib

import pytest

import chat_state
import contracts
import pipeline
from adapters import sage, stubs
from progress import Progress

SILENT = Progress(sink=lambda m: None)


# ----------------------------------------------------------------------
# Build step #2 wired the REAL Sage engine into the factcheck stage. To keep this
# suite offline + deterministic, mock that ONE seam: a tiny engine whose verdict is
# driven by whether each claim's source_ref resolves to a brief source (which is
# exactly what makes coherent stub content pass and an injected bad ref block).
# ----------------------------------------------------------------------
class _FakeFactcheckEngine:
    def factcheck(self, script, brief, *, quiet=True):
        sources = brief.get("sources") or []
        claims = []
        for scene in script.get("scenes", []):
            for c in scene.get("claims", []):
                sref = c.get("source_ref")
                ok = isinstance(sref, int) and not isinstance(sref, bool) \
                    and 0 <= sref < len(sources)
                claims.append({"claim_id": c.get("claim_id", "c?"),
                               "scene_no": scene.get("scene_no", 0),
                               "claim_text": c.get("text", ""),
                               "status": "verified" if ok else "flagged",
                               "sources": [], "note": "" if ok else "unresolved ref"})
        summary = {"verified": sum(c["status"] == "verified" for c in claims),
                   "flagged": sum(c["status"] == "flagged" for c in claims),
                   "unverifiable": 0}
        return {"verdict": "block" if summary["flagged"] else "pass",
                "summary": summary, "claims": claims}


@pytest.fixture(autouse=True)
def _offline_factcheck(monkeypatch):
    monkeypatch.setattr(sage, "_factcheck_engine", lambda: _FakeFactcheckEngine())


# Build step #3 wired Marlow's REAL engine into the script stage. To keep this suite
# offline + deterministic, pin the script producer back to the offline stub here.
# (Marlow's engine — including the claim-traceability guard — is unit-tested in
# scriptwriter/tests/; THIS suite tests the SPINE: ordering, validation, gates.)
# Build step #8 wired Sage's REAL engine into the research stage (it hits the web +
# the LLM). To keep this suite offline + deterministic, pin the research producer back
# to the offline stub here — its coherent placeholder facts are what the downstream
# stub script + real fact-check gate depend on. (Sage's research engine is unit-tested
# in topic-researcher/tests/ and the producer seam in test_research_producer.py; THIS
# suite tests the SPINE: ordering, validation, gates.)
@pytest.fixture(autouse=True)
def _offline_research(monkeypatch):
    research_stage = next(s for s in pipeline.STAGES if s.key == "research")
    monkeypatch.setattr(research_stage, "producer", stubs.produce_research)


@pytest.fixture(autouse=True)
def _offline_script(monkeypatch):
    sw_stage = next(s for s in pipeline.STAGES if s.key == "script")
    monkeypatch.setattr(sw_stage, "producer", stubs.produce_script)


# Step #4 wired Iris's REAL engine into the style + storyboard stages. To keep this
# suite offline + deterministic, pin those two producers back to the offline stubs
# here. (Iris's engine — vocabularies, the signature-beat + budget invariants — is
# unit-tested in art-director/tests/; THIS suite tests the SPINE: ordering, validation,
# gates.)
@pytest.fixture(autouse=True)
def _offline_art_director(monkeypatch):
    for key, producer in (("treatment", stubs.produce_treatment),
                          ("narrative_intent", stubs.produce_narrative_intent),
                          ("motion_mood_board", stubs.produce_motion_mood_board),
                          ("style", stubs.produce_style),
                          ("storyboard", stubs.produce_storyboard)):
        stage = next(s for s in pipeline.STAGES if s.key == key)
        monkeypatch.setattr(stage, "producer", producer)


# Step #6 wired Mason's REAL engine into the compose + render stages (which also run
# the HyperFrames CLI gate / FFmpeg). To keep this suite offline + deterministic, pin
# those producers back to the offline stubs here and the compose stage's contract back
# to None (the stub emits a placeholder, not a composition_manifest). Mason's engine —
# the vocabulary partials, the determinism self-scan, the manifest shape — is unit-
# tested in composition-engineer/tests/; THIS suite tests the SPINE: ordering,
# validation, gates.
@pytest.fixture(autouse=True)
def _offline_composition(monkeypatch):
    compose_stage = next(s for s in pipeline.STAGES if s.key == "compose")
    monkeypatch.setattr(compose_stage, "producer", stubs.produce_compose)
    monkeypatch.setattr(compose_stage, "contract", None)
    render_stage = next(s for s in pipeline.STAGES if s.key == "render")
    monkeypatch.setattr(render_stage, "producer", stubs.produce_render)


# Step #7 wired Cadence's REAL engine into the narration + audiomix stages (which run
# HyperFrames tts + FFmpeg). To keep this suite offline + deterministic — and fast (real
# per-scene tts is ~11s/scene) — pin those producers back to the offline stubs here.
# Cadence's engine — scene-offset math, the license truth table, the master-bridge, the
# mix recipe — is unit-tested in audio-designer/tests/; THIS suite tests the SPINE.
@pytest.fixture(autouse=True)
def _offline_audio(monkeypatch):
    narr_stage = next(s for s in pipeline.STAGES if s.key == "narration")
    monkeypatch.setattr(narr_stage, "producer", stubs.produce_narration)
    mix_stage = next(s for s in pipeline.STAGES if s.key == "audiomix")
    monkeypatch.setattr(mix_stage, "producer", stubs.produce_audiomix)

EXPECTED_ORDER = ["research", "treatment", "narrative_intent", "motion_mood_board",
                  "script", "factcheck", "style", "storyboard", "assets", "narration",
                  "compose", "audiomix", "render"]

ARTIFACT_CONTRACTS = {
    "research_brief.json": "research_brief",
    "script.json": "script",
    "factcheck_report.json": "factcheck_report",
    "style_guide.json": "style_guide",
    "storyboard.json": "storyboard",
    "asset_manifest.json": "asset_manifest",
    "audio/narration.transcript.json": "narration_transcript",
    "audio/audio_manifest.json": "audio_manifest",
    "project.json": "project",
}


# ----------------------------------------------------------------------
# Ordering + the playbook shape
# ----------------------------------------------------------------------
def test_playbook_order_matches_the_build_plan():
    assert [s.key for s in pipeline.STAGES] == EXPECTED_ORDER


def test_factcheck_precedes_art_and_render_is_last():
    order = [s.key for s in pipeline.STAGES]
    assert order.index("factcheck") < order.index("style")
    assert order.index("factcheck") < order.index("storyboard")
    assert order[-1] == "render"


def test_assets_and_narration_are_a_parallel_group():
    groups = {s.key: s.group for s in pipeline.STAGES}
    assert groups["assets"] == "parallel"
    assert groups["narration"] == "parallel"


# ----------------------------------------------------------------------
# Thematic-anchor awareness — informs (returns a tier), never blocks.
# ----------------------------------------------------------------------
def test_check_thematic_anchor_true_when_present(tmp_path):
    (tmp_path / "research_brief.json").write_text(
        '{"thematic_anchor": {"thesis_statement": "A long enough thesis to count here.", '
        '"confidence": "high"}}')
    assert pipeline._check_thematic_anchor(tmp_path) is True


def test_check_thematic_anchor_false_when_absent(tmp_path):
    (tmp_path / "research_brief.json").write_text('{"topic": "x", "verified_facts": []}')
    assert pipeline._check_thematic_anchor(tmp_path) is False


def test_check_thematic_anchor_low_confidence_still_true(tmp_path, caplog=None):
    (tmp_path / "research_brief.json").write_text(
        '{"thematic_anchor": {"thesis_statement": "A long enough thesis to count here.", '
        '"confidence": "low"}}')
    # Low confidence is a warning, not a downgrade: the anchor still counts as present.
    assert pipeline._check_thematic_anchor(tmp_path) is True


def test_check_thematic_anchor_missing_file_is_false(tmp_path):
    assert pipeline._check_thematic_anchor(tmp_path) is False


# ----------------------------------------------------------------------
# Stub producers each write a CONTRACT-VALID artifact
# ----------------------------------------------------------------------
def test_each_stub_producer_writes_a_valid_artifact(tmp_path):
    # Run them in dependency order; each reads upstream + writes its own.
    stubs.produce_research(tmp_path, "espresso")
    mmb = stubs.produce_motion_mood_board(tmp_path, "espresso")
    ok, errors = contracts.validate("motion_mood_board", mmb.data)
    assert ok, errors
    stubs.produce_script(tmp_path, "espresso")
    stubs.produce_factcheck(tmp_path, "espresso")
    stubs.produce_style(tmp_path, "espresso")
    stubs.produce_storyboard(tmp_path, "espresso")
    stubs.produce_assets(tmp_path, "espresso")
    stubs.produce_narration(tmp_path, "espresso")
    stubs.produce_compose(tmp_path, "espresso")
    stubs.produce_audiomix(tmp_path, "espresso")
    stubs.produce_render(tmp_path, "espresso")

    for fname, contract in ARTIFACT_CONTRACTS.items():
        if contract == "project":
            continue
        ok, errors = contracts.validate(contract, chat_state.load_json(tmp_path / fname, {}))
        assert ok, (fname, errors)
    assert (tmp_path / "audio" / "narration.wav").exists()
    assert (tmp_path / "scenes" / "scene-01" / "index.html").exists()
    assert (tmp_path / "video.mp4").exists()


def test_style_guide_carries_the_signature_highlight(tmp_path):
    art = stubs.produce_style(tmp_path, "espresso")
    assert art.data["palette"]["signature_highlight"] == "#FFD000"


# ----------------------------------------------------------------------
# Gates ON: pause-and-resume through project.json
# ----------------------------------------------------------------------
def test_gated_run_pauses_at_factcheck_then_render_then_finishes(tmp_path):
    r = pipeline.produce("home espresso", root=tmp_path, progress=SILENT)
    assert r["status"] == "blocked" and r["gate"] == "factcheck"
    # state persisted to disk
    proj = chat_state.load_json(pathlib.Path(r["project_dir"]) / "project.json", {})
    assert proj["status"] == "blocked_at_factcheck"
    assert proj["gates"]["factcheck"]["status"] == "blocked"

    slug = r["slug"]
    r = pipeline.produce(slug=slug, approve=["factcheck"], root=tmp_path, progress=SILENT)
    assert r["status"] == "blocked" and r["gate"] == "final_render"
    assert "details" in r and "plan" in r["details"]

    r = pipeline.produce(slug=slug, approve=["final_render"], root=tmp_path, progress=SILENT)
    assert r["status"] == "done"
    assert pathlib.Path(r["video"]).exists()


def test_resume_is_idempotent_after_done(tmp_path):
    r = pipeline.produce("topic one", root=tmp_path, unattended=True, progress=SILENT)
    assert r["status"] == "done"
    again = pipeline.produce(slug=r["slug"], root=tmp_path, progress=SILENT)
    assert again["status"] == "done"
    assert again["video"] == r["video"]


def test_unattended_runs_straight_through_and_validates_every_artifact(tmp_path):
    r = pipeline.produce("faceless youtube", root=tmp_path, unattended=True,
                         progress=SILENT)
    assert r["status"] == "done"
    pdir = pathlib.Path(r["project_dir"])
    for fname, contract in ARTIFACT_CONTRACTS.items():
        ok, errors = contracts.validate(contract, chat_state.load_json(pdir / fname, {}))
        assert ok, (fname, errors)
    # every stage marked done
    proj = chat_state.load_json(pdir / "project.json", {})
    assert all(proj["stages"][k]["status"] == "done" for k in EXPECTED_ORDER)


def test_gate_toggle_factcheck_only(tmp_path):
    # final_render off, factcheck on -> pauses ONLY at factcheck, then finishes.
    r = pipeline.produce("toggle topic", root=tmp_path, progress=SILENT,
                         gates={"final_render": False})
    assert r["status"] == "blocked" and r["gate"] == "factcheck"
    r = pipeline.produce(slug=r["slug"], approve=["factcheck"], root=tmp_path,
                         progress=SILENT)
    assert r["status"] == "done"


# ----------------------------------------------------------------------
# Gate ENFORCEMENT: a block cannot be approved away
# ----------------------------------------------------------------------
def _patch_factcheck_block(monkeypatch):
    fc_stage = next(s for s in pipeline.STAGES if s.key == "factcheck")
    orig = fc_stage.producer

    def blocking(pdir, topic):
        art = orig(pdir, topic)
        art.data["claims"][0]["status"] = "flagged"
        art.data["verdict"] = "block"
        art.data["summary"] = {"verified": max(len(art.data["claims"]) - 1, 0),
                               "flagged": 1, "unverifiable": 0}
        chat_state.atomic_write_json(pdir / "factcheck_report.json", art.data)
        return art

    monkeypatch.setattr(fc_stage, "producer", blocking)


def test_factcheck_block_halts_and_cannot_be_approved_away(tmp_path, monkeypatch):
    _patch_factcheck_block(monkeypatch)
    r = pipeline.produce("bad claims", root=tmp_path, progress=SILENT)
    assert r["status"] == "blocked" and r["gate"] == "factcheck"
    assert "route back" in r["reason"].lower()
    proj = chat_state.load_json(pathlib.Path(r["project_dir"]) / "project.json", {})
    assert proj["gates"]["factcheck"]["status"] == "rejected"

    # Even an explicit approval cannot clear a `block` verdict — it routes back.
    r2 = pipeline.produce(slug=r["slug"], approve=["factcheck"], root=tmp_path,
                          progress=SILENT)
    assert r2["status"] == "blocked" and r2["gate"] == "factcheck"
    # art stages never ran
    assert proj["stages"]["style"]["status"] != "done"


def test_unattended_does_not_bypass_a_factcheck_block(tmp_path, monkeypatch):
    # Gates OFF still must not ship unverified claims: a `block` verdict halts.
    _patch_factcheck_block(monkeypatch)
    r = pipeline.produce("bad claims unattended", root=tmp_path, unattended=True,
                         progress=SILENT)
    assert r["status"] == "blocked" and r["gate"] == "factcheck"


# ----------------------------------------------------------------------
# The demo toggle: coherent stub content PASSES the real gate; one injected
# unsupported claim makes the REAL fact-check verdict BLOCK.
# ----------------------------------------------------------------------
def test_coherent_stub_content_passes_the_real_factcheck_gate(tmp_path):
    # No injected claim -> every stub claim's source_ref resolves -> verdict pass ->
    # the gate pauses for human sign-off (clean), it does NOT block/reject.
    r = pipeline.produce("clean topic", root=tmp_path, progress=SILENT,
                         gates={"final_render": False})
    assert r["status"] == "blocked" and r["gate"] == "factcheck"
    assert r["details"]["verdict"] == "pass"
    assert r["details"]["flagged"] == []
    # signing off the (clean) fact-check then proceeds to done — no reject.
    r2 = pipeline.produce(slug=r["slug"], approve=["factcheck"], root=tmp_path,
                          progress=SILENT)
    assert r2["status"] == "done"


def test_injected_unsupported_claim_blocks_the_real_gate(tmp_path, monkeypatch):
    monkeypatch.setenv(stubs.INJECT_UNSUPPORTED_CLAIM_ENV, "1")
    r = pipeline.produce("topic with a bad claim", root=tmp_path, unattended=True,
                         progress=SILENT)
    assert r["status"] == "blocked" and r["gate"] == "factcheck"
    assert r["details"]["verdict"] == "block"
    assert r["details"]["flagged"], "the injected claim should be flagged"
    # and a block cannot be approved away
    r2 = pipeline.produce(slug=r["slug"], approve=["factcheck"], root=tmp_path,
                          progress=SILENT)
    assert r2["status"] == "blocked" and r2["gate"] == "factcheck"


# ----------------------------------------------------------------------
# (a) Slug-free resume: resolve the project waiting at a gate by `approve` alone.
# (b) Fact-check-block loop: a resume RE-RUNS Sage on the revised script (never
#     trusting the on-disk report); a block is re-earned, never approved away.
# ----------------------------------------------------------------------
def test_approve_only_resume_resolves_the_blocked_project(tmp_path):
    # TEST 1: no brief, no slug — just `approve`. Must resolve the one blocked project.
    r = pipeline.produce("approve only", root=tmp_path, progress=SILENT,
                         gates={"final_render": False})
    assert r["status"] == "blocked" and r["gate"] == "factcheck"
    r2 = pipeline.produce(approve=["factcheck"], root=tmp_path, progress=SILENT)
    assert r2["status"] == "done"
    assert r2["slug"] == r["slug"]  # resolved the SAME project, no slug needed


def test_approve_only_resume_errors_on_zero_candidates(tmp_path):
    # TEST 2a: nothing is waiting at the gate -> a clean error, not a crash/blank run.
    r = pipeline.produce(approve=["factcheck"], root=tmp_path, progress=SILENT)
    assert r["status"] == "failed" and r["stage"] is None and r["slug"] is None
    assert "no project" in r["errors"][0].lower()


def test_approve_only_resume_errors_on_ambiguous_candidates(tmp_path):
    # TEST 2b: two projects blocked at the same gate -> a clean disambiguation error.
    a = pipeline.produce("amb one", root=tmp_path, progress=SILENT)
    b = pipeline.produce("amb two", root=tmp_path, progress=SILENT)
    assert a["gate"] == "factcheck" and b["gate"] == "factcheck"
    r = pipeline.produce(approve=["factcheck"], root=tmp_path, progress=SILENT)
    assert r["status"] == "failed" and r["stage"] is None
    assert "more than one" in r["errors"][0].lower()


def test_resume_on_block_reruns_factcheck_and_proceeds_after_fix(tmp_path, monkeypatch):
    # TEST 3: blocked -> revise the script -> resume RE-RUNS Sage -> passes -> proceeds.
    monkeypatch.setenv(stubs.INJECT_UNSUPPORTED_CLAIM_ENV, "1")
    r = pipeline.produce("rerun pass", root=tmp_path, progress=SILENT,
                         gates={"final_render": False})
    assert r["status"] == "blocked" and r["details"]["verdict"] == "block"
    pdir = pathlib.Path(r["project_dir"])

    # CEO-side fix: Marlow revises the script in place so the bad claim is gone.
    monkeypatch.delenv(stubs.INJECT_UNSUPPORTED_CLAIM_ENV)
    stubs.produce_script(pdir, "rerun pass")

    r2 = pipeline.produce(approve=["factcheck"], root=tmp_path, progress=SILENT)
    assert r2["status"] == "done"
    report = chat_state.load_json(pdir / "factcheck_report.json", {})
    assert report["verdict"] == "pass"  # regenerated against the revised script
    proj = chat_state.load_json(pdir / "project.json", {})
    assert proj["stages"]["factcheck"]["status"] == "done"
    assert proj["stages"]["style"]["status"] == "done"  # proceeded past the gate


def test_resume_on_block_reblocks_when_still_failing(tmp_path, monkeypatch):
    # TEST 4: no fix -> the re-run regenerates the flags and re-blocks; art never runs.
    monkeypatch.setenv(stubs.INJECT_UNSUPPORTED_CLAIM_ENV, "1")
    r = pipeline.produce("rerun block", root=tmp_path, progress=SILENT)
    assert r["status"] == "blocked" and r["gate"] == "factcheck"
    pdir = pathlib.Path(r["project_dir"])

    r2 = pipeline.produce(approve=["factcheck"], root=tmp_path, progress=SILENT)
    assert r2["status"] == "blocked" and r2["gate"] == "factcheck"
    assert r2["details"]["verdict"] == "block"
    assert r2["details"]["flagged"], "the re-run regenerates the flags"
    proj = chat_state.load_json(pdir / "project.json", {})
    assert proj["stages"]["style"]["status"] != "done"  # did NOT proceed to art
    assert proj["gates"]["factcheck"]["status"] == "rejected"
    # the re-run actually happened (not a stale re-read)
    assert any("re-running fact-check" in h.get("decision", "")
               for h in proj["history"])


def test_resume_does_not_trust_an_externally_passed_report(tmp_path, monkeypatch):
    # TEST 5 (integrity): a hand-driven 'pass' rewrite can't smuggle a block through —
    # the resume RE-RUNS Sage against the (still-bad) script and re-earns the block.
    monkeypatch.setenv(stubs.INJECT_UNSUPPORTED_CLAIM_ENV, "1")
    r = pipeline.produce("integrity", root=tmp_path, progress=SILENT)
    assert r["status"] == "blocked" and r["details"]["verdict"] == "block"
    pdir = pathlib.Path(r["project_dir"])

    # Out-of-band: flip the on-disk report to pass WITHOUT fixing the script.
    report = chat_state.load_json(pdir / "factcheck_report.json", {})
    report["verdict"] = "pass"
    report["summary"] = {"verified": 99, "flagged": 0, "unverifiable": 0}
    chat_state.atomic_write_json(pdir / "factcheck_report.json", report)

    r2 = pipeline.produce(approve=["factcheck"], root=tmp_path, progress=SILENT)
    assert r2["status"] == "blocked" and r2["gate"] == "factcheck"
    assert r2["details"]["verdict"] == "block"
    regenerated = chat_state.load_json(pdir / "factcheck_report.json", {})
    assert regenerated["verdict"] == "block"  # the re-run overwrote the smuggled pass


def test_fresh_start_never_auto_resumes_even_with_a_blocked_project(tmp_path):
    # TEST 6 (regression): brief + no approve ALWAYS starts a NEW project — the (a)
    # resolver is scoped to `approve`, so it can't reintroduce the auto-latch.
    first = pipeline.produce("blocked one", root=tmp_path, progress=SILENT)
    assert first["status"] == "blocked" and first["gate"] == "factcheck"
    second = pipeline.produce("a different video", root=tmp_path, progress=SILENT)
    assert second["status"] == "blocked" and second["gate"] == "factcheck"
    assert second["slug"] != first["slug"]  # a brand-new project, not a resume
    proj = chat_state.load_json(pathlib.Path(first["project_dir"]) / "project.json", {})
    assert proj["status"] == "blocked_at_factcheck"  # the original is untouched
    assert proj["stages"]["style"]["status"] != "done"


# ----------------------------------------------------------------------
# Deterministic progress lines come from inside the spine
# ----------------------------------------------------------------------
def test_progress_lines_are_emitted_in_order(tmp_path):
    lines = []
    prog = Progress(sink=lines.append)
    pipeline.produce("progress topic", root=tmp_path, unattended=True, progress=prog)
    joined = "\n".join(lines)
    assert "Marlow is drafting the script…" in joined  # the Scriptwriter's display name
    assert "🎬 Done" in joined
