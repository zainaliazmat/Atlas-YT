"""The registry holds all seven production roles, with the right tools + statuses.
Every specialist is now built — no stubs remain — though the StubAdapter dispatch path
itself stays exercised (a slot CAN still be a stub; today none are).

Vera (reference_analyst) was added later as a PURELY ADDITIVE delegable job + persona —
she DEFINES the standard, she is NOT one of the seven production-pipeline roles and is
NOT a stage in pipeline.py. So the seven pipeline roles are asserted as a subset here,
and the only non-pipeline registry addition is called out explicitly."""
import registry
import tools
from progress import list_progress

SEVEN = {"scout", "sage", "scriptwriter", "art_director", "asset_sourcer",
         "audio", "composition_engineer"}
# Cadence filled the Audio / Sound Designer slot (build step #7) — the LAST stub —
# joining Mason (#6), Magpie (#5), Iris (#4), Marlow (#3), scout, and sage. All seven
# are now ready; no slot is a stub.
NO_STUBS: set[str] = set()
READY = set(SEVEN)
# Additive (non-pipeline) agents that live in the registry alongside the seven roles:
# Vera (reference_analyst) defines the standard; Quill (editorial_coach) + Flux
# (production_coach) are the self-improvement loop's two domain coaches (Phase-2 step 3).
ADDITIVE = {"reference_analyst", "editorial_coach", "production_coach"}


def test_all_seven_roles_registered():
    names = {e.name for e in registry.REGISTRY}
    assert SEVEN <= names                       # every pipeline role is present
    assert names - SEVEN == ADDITIVE            # the only extras are additive agents


def test_no_stubs_remain_all_seven_ready():
    stubs = {e.name for e in registry.REGISTRY if e.stub}
    ready = {e.name for e in registry.REGISTRY if not e.stub}
    assert stubs == NO_STUBS
    assert READY <= ready                        # all seven pipeline roles are ready
    assert ADDITIVE <= ready                     # Vera is a real, built specialist too


def test_sage_keeps_research_and_gains_factcheck():
    sage = registry.get_entry("sage")
    job_names = {j.name for j in sage.jobs}
    assert "research" in job_names          # unchanged real job
    assert "factcheck" in job_names         # evolved real pass-2 job
    assert sage.stub is False               # Sage's slot is real, not a stub


def test_scriptwriter_slot_is_filled_by_marlow():
    from adapters.scriptwriter import ScriptwriterAdapter
    sw = registry.get_entry("scriptwriter")
    assert sw.stub is False                       # the slot is real now, not a stub
    assert sw.adapter_cls is ScriptwriterAdapter  # wired to Marlow's engine
    assert {j.name for j in sw.jobs} == {"write_script"}  # same job surface as the stub
    assert sw.role == "Scriptwriter"


def test_art_director_slot_is_filled_by_iris():
    from adapters.art_director import ArtDirectorAdapter
    ad = registry.get_entry("art_director")
    assert ad.stub is False                       # the slot is real now, not a stub
    assert ad.adapter_cls is ArtDirectorAdapter   # wired to Iris's engine
    assert {j.name for j in ad.jobs} == {"design_style", "build_storyboard"}  # same surface
    assert ad.role == "Art Director"
    assert ad.display == "Iris"


def test_asset_sourcer_slot_is_filled_by_magpie():
    from adapters.asset_sourcer import AssetSourcerAdapter
    as_ = registry.get_entry("asset_sourcer")
    assert as_.stub is False                       # the slot is real now, not a stub
    assert as_.adapter_cls is AssetSourcerAdapter   # wired to Magpie's engine
    assert {j.name for j in as_.jobs} == {"source_assets"}  # same surface as the stub
    assert as_.role == "Asset Sourcer & Licensing"
    assert as_.display == "Magpie"


def test_generated_tools_cover_every_role_plus_produce_video():
    adapters = registry.build_adapters()
    prog, _ = list_progress()
    _server, allowed = tools.build_server(adapters, prog)
    for t in ("scout_find_topics", "sage_research", "sage_factcheck",
              "scriptwriter_write_script", "art_director_design_style",
              "art_director_build_storyboard", "asset_sourcer_source_assets",
              "audio_record_narration", "audio_mix_audio",
              "composition_engineer_compose_scenes",
              "composition_engineer_render_video"):
        assert f"mcp__atlas__{t}" in allowed, t
    # the one non-registry tool: the production spine
    assert "mcp__atlas__produce_video" in allowed
    # persona tool for every role
    for n in SEVEN:
        assert f"mcp__atlas__ask_{n}" in allowed


def test_roster_shows_role_and_status():
    r = registry.roster()
    assert "ready" in r
    assert "stub (slot reserved" not in r          # every slot is built now
    assert "Scriptwriter" in r and "Composition Engineer" in r
    assert "Audio / Sound Designer" in r           # Cadence's role on the call sheet
    assert registry.status_label(registry.get_entry("scout")) == "ready"
    assert registry.status_label(registry.get_entry("audio")) == "ready"


def test_stub_adapter_dispatch_is_honest():
    from adapters.stubs import StubAdapter
    entry = registry.get_entry("asset_sourcer")  # a still-unbuilt specialist slot
    adapter = StubAdapter(entry)
    prog, lines = list_progress()
    out = adapter.run_job("source_assets", prog, topic="x")
    assert out["ok"] is True
    assert "stub" in out["text"].lower() or "placeholder" in out["text"].lower()
    assert lines  # emitted a progress line


class _FakeFactcheckEngine:
    """Offline stand-in for Sage's factcheck engine: source_ref resolves -> verified."""
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


def test_sage_factcheck_job_runs_engine_and_returns_digest(tmp_path, monkeypatch):
    # Build step #2: the factcheck job now runs Sage's REAL engine (mocked offline),
    # writes a contract-valid report, and returns a verdict digest.
    import chat_state
    import contracts
    from adapters import sage as sage_mod
    from adapters.sage import SageAdapter

    pdir = tmp_path / "proj"
    pdir.mkdir()
    chat_state.atomic_write_json(pdir / "research_brief.json", {
        "schema_version": "1.0", "topic": "x", "overview": "o",
        "verified_facts": [{"claim": "a"}],
        "sources": [{"url": "https://a.example"}]})
    chat_state.atomic_write_json(pdir / "script.json", {
        "schema_version": "1.0", "working_title": "t",
        "scenes": [{"scene_no": 1, "point": "p", "narration": "n",
                    "claims": [{"claim_id": "c1", "text": "a", "source_ref": 0}]}]})

    monkeypatch.setattr(sage_mod, "_resolve_project_dir", lambda topic: pdir)
    monkeypatch.setattr(sage_mod, "_factcheck_engine", lambda: _FakeFactcheckEngine())

    adapter = SageAdapter(registry.get_entry("sage"))
    prog, _ = list_progress()
    out = adapter.run_job("factcheck", prog, topic="x")

    assert out["ok"] is True
    assert "verdict" in out["text"].lower() and "pass" in out["text"].lower()
    report = chat_state.load_json(pdir / "factcheck_report.json", {})
    ok, errors = contracts.validate("factcheck_report", report)
    assert ok, errors
    assert report["schema_version"]  # atlas stamped the envelope


def test_sage_factcheck_job_reports_missing_project(monkeypatch):
    # No resolvable project -> an honest failure, not a crash.
    from adapters import sage as sage_mod
    from adapters.sage import SageAdapter
    monkeypatch.setattr(sage_mod, "_resolve_project_dir", lambda topic: None)
    adapter = SageAdapter(registry.get_entry("sage"))
    prog, _ = list_progress()
    out = adapter.run_job("factcheck", prog, topic="nothing here")
    assert out["ok"] is False
    assert adapter._engine is None  # research engine never loaded for a factcheck
