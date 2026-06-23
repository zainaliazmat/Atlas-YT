"""Offline, deterministic tests for Quill's coach engine — NO network, NO API key.

Run:  pytest tests/test_coach_engine.py   (or the whole tests/ dir)

The ONLY thing in coach_engine that would touch an LLM is the `chat_fn` seam, and
every test here either injects a fake `chat_fn` or never calls the live path. So the
suite proves the engine's plumbing fully offline:
  - LLM path: an injected chat_fn returning a fixed string -> source=="llm", the
    addendum is wrapped with the "## Coach note (Quill …)" header and carries both
    the band_id and the injected text.
  - rule fallback: a chat_fn that raises -> source=="rule", the deterministic
    addendum carries the direction, the band_id, and the preserve text (never raises).
  - the pure, no-network helpers: coaches_stage / DOMAIN / COACHED_STAGES.

We never call propose_addendum with chat_fn=None here (that would hit the default
llm.chat -> the network); the stage/constant checks cover the offline-only surface.
"""
import coach_engine  # noqa: E402  (path set in conftest.py)


# ----------------------------------------------------------------------
# LLM path — an injected chat_fn authors the body
# ----------------------------------------------------------------------
def test_llm_path_wraps_injected_text_and_marks_source_llm():
    injected = "Cut to one claim per scene; expand the narration so the runtime holds."
    result = coach_engine.propose_addendum(
        band_id="script:info_density",
        direction="LOWER it to about 2.75",
        preserve=" Keep script:runtime_fit in [60,90].",
        owner="Marlow",
        chat_fn=lambda system, user: injected,
    )
    assert result["source"] == "llm"
    # wrapped with the fixed Coach-note header, carrying the band id
    assert result["addendum"].startswith(
        "## Coach note (Quill · editorial · target script:info_density)")
    assert "script:info_density" in result["addendum"]
    # the injected text is the body
    assert injected in result["addendum"]
    # the rest of the contract dict
    assert result["band_id"] == "script:info_density"
    assert result["direction"] == "LOWER it to about 2.75"
    assert result["domain"] == "editorial"
    assert result["owner"] == "Marlow"


def test_llm_empty_reply_falls_back_to_rule():
    # An empty/whitespace reply is not a usable note -> keep the deterministic one.
    result = coach_engine.propose_addendum(
        band_id="assets:relevance",
        direction="RAISE it",
        chat_fn=lambda system, user: "   ",
    )
    assert result["source"] == "rule"
    assert "assets:relevance" in result["addendum"]


# ----------------------------------------------------------------------
# Rule fallback — the brain raises, the engine degrades gracefully (never raises)
# ----------------------------------------------------------------------
def test_rule_fallback_when_chat_fn_raises():
    def boom(system, user):
        raise RuntimeError("brain offline")

    preserve = " Keep these in range: script:runtime_fit in [60,90]."
    result = coach_engine.propose_addendum(
        band_id="script:info_density",
        direction="LOWER it to about 2.75",
        preserve=preserve,
        owner="Marlow",
        chat_fn=boom,
    )
    assert result["source"] == "rule"
    add = result["addendum"]
    # deterministic fallback carries band_id, direction, and the preserve text
    assert "script:info_density" in add
    assert "LOWER it to about 2.75" in add
    assert preserve.strip() in add
    # still wrapped with the Coach-note header
    assert add.startswith("## Coach note (Quill · editorial · target script:info_density)")


def test_propose_addendum_never_raises_on_brain_error():
    # Sanity: the documented "never raises" guarantee holds for any chat_fn blowup.
    def boom(system, user):
        raise ValueError("kaboom")

    result = coach_engine.propose_addendum(
        band_id="research:coverage", direction="RAISE it", chat_fn=boom)
    assert isinstance(result, dict)
    assert result["source"] == "rule"


# ----------------------------------------------------------------------
# Pure, no-network surface — stages and constants
# ----------------------------------------------------------------------
def test_coaches_stage_membership():
    assert coach_engine.coaches_stage("script") is True
    assert coach_engine.coaches_stage("compose") is False


def test_domain_and_coached_stages():
    assert coach_engine.DOMAIN == "editorial"
    for stage in ("research", "script", "factcheck", "assets"):
        assert stage in coach_engine.COACHED_STAGES
