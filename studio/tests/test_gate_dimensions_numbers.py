from studio.gate.types import load_thresholds
from studio.gate import dimensions as D

T = load_thresholds()


def test_motion_energy_low_fails_with_diagnostic():
    ev = {"global": {"motion_energy": 0.9}, "motion": {"scenes": [
        {"scene_no": 1, "motion_energy": 0.5}, {"scene_no": 2, "motion_energy": 7.0}]}}
    r = D.score_motion_energy(ev, T)
    assert r.passed is False and r.score < T["dimensions"]["motion_energy"]["floor"]
    assert any("static" in d.lower() or "scene 1" in d.lower() for d in r.diagnostics)


def test_motion_energy_healthy_passes():
    ev = {"global": {"motion_energy": 6.0}, "motion": {"scenes": []}}
    r = D.score_motion_energy(ev, T)
    assert r.passed is True and r.score >= T["dimensions"]["motion_energy"]["floor"]


def test_motion_energy_unmeasurable_is_none():
    r = D.score_motion_energy({"global": {"motion_energy": None}, "motion": {}}, T)
    assert r.score is None and r.passed is None


def test_audio_off_target_fails():
    ev = {"loudness": {"integrated_lufs": -22.0, "true_peak_dbtp": -3.0, "clipping": False}}
    r = D.score_audio(ev, T)
    assert r.passed is False
    assert any("LUFS" in d or "lufs" in d.lower() for d in r.diagnostics)


def test_audio_clipping_forces_fail():
    ev = {"loudness": {"integrated_lufs": -14.0, "true_peak_dbtp": -0.2, "clipping": True}}
    r = D.score_audio(ev, T)
    assert r.passed is False and any("clip" in d.lower() for d in r.diagnostics)


def test_pacing_in_ideal_band_passes():
    r = D.score_pacing({"global": {"cut_rhythm": 4.0}}, T)
    assert r.passed is True
