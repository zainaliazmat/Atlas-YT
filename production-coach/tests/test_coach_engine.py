"""Offline, deterministic proof of the Flux coaching engine — NO network, NO LLM.

Run:  pytest tests/test_coach_engine.py   (or: python tests/test_coach_engine.py)

The engine is PURE + INJECTABLE: the LLM call is the `chat_fn` seam, so every test
passes a fake. We prove:
- an injected chat_fn that returns text -> source=="llm", wrapped + carries the text,
- a chat_fn that raises -> graceful fallback to the deterministic rule addendum,
- the stage-membership / domain / coached-stages contract.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import coach_engine  # noqa: E402


# ----------------------------------------------------------------------
# propose_addendum — the LLM path (injected chat_fn returns a fixed string)
# ----------------------------------------------------------------------
def test_llm_path_uses_injected_text_and_marks_source_llm():
    injected = "Add a push-in and a count-up beat to lift motion modulation."
    out = coach_engine.propose_addendum(
        band_id="compose:motion_energy",
        direction="RAISE it to about 10",
        owner="Mason",
        chat_fn=lambda system, user: injected,
    )
    assert out["source"] == "llm"
    assert out["band_id"] == "compose:motion_energy"
    assert out["domain"] == "production"
    assert out["owner"] == "Mason"
    # wrapped with the standard Flux header carrying the band id
    assert out["addendum"].startswith("## Coach note (Flux")
    assert "compose:motion_energy" in out["addendum"]
    # the injected text is carried verbatim into the addendum body
    assert injected in out["addendum"]


def test_llm_seam_receives_system_and_user():
    seen = {}

    def fake(system, user):
        seen["system"] = system
        seen["user"] = user
        return "do the thing"

    coach_engine.propose_addendum(
        band_id="audiomix:loudness", direction="RAISE it", owner="AudioEng",
        measured_value=-23.0, preserve=" Keep intelligibility in band.",
        chat_fn=fake)
    # the philosophy/role is in the system prompt; the diagnosis is in the user prompt
    assert "Flux" in seen["system"]
    assert "audiomix:loudness" in seen["user"]
    assert "RAISE it" in seen["user"]
    assert "AudioEng" in seen["user"]
    assert "-23.0" in seen["user"]


def test_empty_llm_reply_falls_back_to_rule():
    # a chat_fn that returns blank text must NOT be treated as a real authoring
    out = coach_engine.propose_addendum(
        band_id="style:type_hierarchy", direction="TIGHTEN it",
        chat_fn=lambda s, u: "   ")
    assert out["source"] == "rule"
    assert "style:type_hierarchy" in out["addendum"]


# ----------------------------------------------------------------------
# propose_addendum — the graceful rule fallback (chat_fn raises)
# ----------------------------------------------------------------------
def test_chat_fn_raises_falls_back_to_rule_and_never_raises():
    def boom(system, user):
        raise RuntimeError("brain offline")

    out = coach_engine.propose_addendum(
        band_id="narration:modulation",
        direction="RAISE the vocal modulation",
        preserve=" Keep intelligibility in band.",
        owner="VO",
        chat_fn=boom,
    )
    assert out["source"] == "rule"
    assert out["domain"] == "production"
    # the deterministic fallback contains the band id, the direction, and the preserve text
    assert "narration:modulation" in out["addendum"]
    assert "RAISE the vocal modulation" in out["addendum"]
    assert "Keep intelligibility in band." in out["addendum"]
    assert out["addendum"].startswith("## Coach note (Flux")


def test_rule_fallback_is_the_default_when_no_chat_fn_and_seam_unavailable(monkeypatch):
    # With no chat_fn, the engine uses llm.chat. Force that to raise so the test
    # stays fully offline; the engine must still produce the deterministic rule note.
    import llm
    monkeypatch.setattr(llm, "chat", lambda s, u: (_ for _ in ()).throw(RuntimeError("no net")))
    out = coach_engine.propose_addendum(
        band_id="render:av_coherence", direction="IMPROVE picture/sound sync")
    assert out["source"] == "rule"
    assert "render:av_coherence" in out["addendum"]


# ----------------------------------------------------------------------
# Stage membership + domain contract (the interface atlas depends on)
# ----------------------------------------------------------------------
def test_coaches_stage_membership():
    assert coach_engine.coaches_stage("compose") is True
    assert coach_engine.coaches_stage("script") is False


def test_domain_is_production():
    assert coach_engine.DOMAIN == "production"


def test_coached_stages_cover_the_craft_side():
    for stage in ("style", "storyboard", "narration", "compose", "audiomix", "render"):
        assert stage in coach_engine.COACHED_STAGES
        assert coach_engine.coaches_stage(stage) is True


def test_returned_dict_shape():
    out = coach_engine.propose_addendum(
        band_id="compose:pacing", direction="SLOW it down",
        chat_fn=lambda s, u: "hold the cut a half-second longer")
    assert set(out.keys()) == {"band_id", "direction", "domain", "owner",
                               "addendum", "source", "research"}


if __name__ == "__main__":
    import traceback
    import inspect

    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        # skip the monkeypatch-fixture test in the bare-runner path
        if "monkeypatch" in inspect.signature(fn).parameters:
            print(f"skip  {fn.__name__} (needs pytest fixture)")
            continue
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len([f for f in fns]) - failed} checks ok (excluding fixture tests)")
    sys.exit(1 if failed else 0)


# ----------------------------------------------------------------------
# Roundtable process data — folded into the prompt when supplied (additive)
# ----------------------------------------------------------------------
def test_roundtable_context_reaches_the_prompt():
    seen = {}

    def chat_fn(system, user):
        seen["user"] = user
        return "Lock the push-in peak to the emphasized syllable."

    ctx = {
        "roundtable_used": True, "specialist": "Mason", "process_health": "healthy",
        "criticisms": [{"severity": "major", "principle": "Rule 1",
                        "diagnosis": "easing contradicts the urgency beat"}],
        "research_quality": {"total_findings": 1, "findings_with_sources": 0},
        "craftsman_impact": {"scenes_modified": 0},
    }
    result = coach_engine.propose_addendum(
        band_id="compose:motion_energy", direction="RAISE it", owner="Mason",
        chat_fn=chat_fn, roundtable_context=ctx)
    assert result["source"] == "llm"
    assert "INTERNAL CREATIVE PROCESS DATA" in seen["user"]
    assert "easing contradicts the urgency beat" in seen["user"]
    assert "Mason" in seen["user"]


def test_no_roundtable_context_leaves_prompt_clean():
    seen = {}

    def chat_fn(system, user):
        seen["user"] = user
        return "Add a single signature push-in."

    coach_engine.propose_addendum(
        band_id="compose:motion_energy", direction="RAISE it", chat_fn=chat_fn)
    assert "INTERNAL CREATIVE PROCESS DATA" not in seen["user"]
