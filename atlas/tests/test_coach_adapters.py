"""Step-3 tests: the two coach adapters wrap their engine correctly.

Offline: the sibling engine is replaced with a fake module (no loader / LLM), so
we test the adapter's contract — param validation, the engine call, and the digest
shape — without the real coach project."""
from __future__ import annotations

import types

from adapters.editorial_coach import EditorialCoachAdapter
from adapters.production_coach import ProductionCoachAdapter
import registry


def _entry(name):
    return registry.get_entry(name)


def _fake_engine(domain):
    m = types.ModuleType("coach_engine")
    def propose_addendum(*, band_id, direction, preserve="", measured_value=None, owner="", research=False):
        return {"band_id": band_id, "direction": direction, "domain": domain,
                "owner": owner, "source": "rule",
                "addendum": f"## Coach note ({domain} · target {band_id})\n{direction}\n"}
    m.propose_addendum = propose_addendum
    return m


def test_editorial_adapter_runs_job():
    a = EditorialCoachAdapter(_entry("editorial_coach"))
    a._engine = _fake_engine("editorial")            # bypass the loader
    res = a.run_job("propose_addendum", None,
                    band_id="script:info_density", direction="LOWER it to about 2.75")
    assert res["ok"] is True and res["domain"] == "editorial"
    assert "script:info_density" in res["text"]


def test_production_adapter_runs_job():
    a = ProductionCoachAdapter(_entry("production_coach"))
    a._engine = _fake_engine("production")
    res = a.run_job("propose_addendum", None,
                    band_id="compose:motion_energy", direction="RAISE it to about 10")
    assert res["ok"] is True and res["domain"] == "production"
    assert "compose:motion_energy" in res["text"]


def test_adapter_requires_band_and_direction():
    a = EditorialCoachAdapter(_entry("editorial_coach"))
    a._engine = _fake_engine("editorial")
    assert a.run_job("propose_addendum", None, band_id="", direction="x")["ok"] is False
    assert a.run_job("propose_addendum", None, band_id="x", direction="")["ok"] is False


def test_adapter_rejects_unknown_job():
    a = EditorialCoachAdapter(_entry("editorial_coach"))
    a._engine = _fake_engine("editorial")
    assert a.run_job("nope", None, band_id="x", direction="y")["ok"] is False


# --- registry wiring -------------------------------------------------------

def test_coaches_are_registered_with_tools():
    names = {e.name for e in registry.REGISTRY}
    assert {"editorial_coach", "production_coach"} <= names
    ed = _entry("editorial_coach")
    assert ed.jobs[0].tool == "editorial_coach_propose_addendum"
    assert ed.persona is True
    pr = _entry("production_coach")
    assert pr.jobs[0].tool == "production_coach_propose_addendum"


def test_build_adapters_instantiates_coaches():
    adapters = registry.build_adapters()
    assert isinstance(adapters["editorial_coach"], EditorialCoachAdapter)
    assert isinstance(adapters["production_coach"], ProductionCoachAdapter)
