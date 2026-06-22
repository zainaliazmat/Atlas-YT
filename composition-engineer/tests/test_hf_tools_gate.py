"""Pure-unit tests for the HyperFrames gate wrappers — NO Node, NO subprocess.

The `_run` seam (the only thing that touches the CLI) is mocked, so these exercise the
pass/fail LOGIC of run_lint/run_validate/run_inspect/run_gate. Focus: fail-closed
behavior when the CLI exits 0 but emits no parseable JSON (M3 vacuous-PASS bug).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import hf_tools  # noqa: E402


def _fake_run(monkeypatch, returncode=0, json=None, error=None, stderr=""):
    """Patch hf_tools._run to return a canned CLI result for every command."""
    def fake(cmd, scene_dir, *extra, timeout):
        return {"ran": True, "returncode": returncode, "json": json,
                "stderr": stderr, "error": error}
    monkeypatch.setattr(hf_tools, "_run", fake)


SCENE = pathlib.Path("/tmp/scene-01")


# ----------------------------------------------------------------------
# M3 — rc==0 but garbage/unparseable JSON must FAIL the gate (fail-closed),
# never yield a vacuous PASS from zero findings.
# ----------------------------------------------------------------------
def test_lint_fails_closed_on_unparseable_json(monkeypatch):
    _fake_run(monkeypatch, returncode=0, json=None)   # parse returned None
    out = hf_tools.run_lint(SCENE)
    assert out["ok"] is False
    assert "parseable" in out.get("note", "")


def test_validate_fails_closed_on_unparseable_json(monkeypatch):
    _fake_run(monkeypatch, returncode=0, json=None)
    out = hf_tools.run_validate(SCENE)
    assert out["ok"] is False


def test_inspect_fails_closed_on_unparseable_json(monkeypatch):
    _fake_run(monkeypatch, returncode=0, json=None)
    out = hf_tools.run_inspect(SCENE)
    assert out["ok"] is False


def test_run_gate_does_not_vacuously_pass_on_garbage_stdout(monkeypatch):
    # The headline bug: CLI rc=0, stdout is garbage (no JSON) -> _parse_json -> None.
    _fake_run(monkeypatch, returncode=0, json=None, stderr="")
    gate = hf_tools.run_gate(SCENE)
    # short-circuits at lint, which now fails closed; the gate must NOT pass.
    assert gate["lint"]["ok"] is False
    assert not all((gate.get(k) or {}).get("ok") for k in ("lint", "validate", "inspect"))


# ----------------------------------------------------------------------
# Guard: valid JSON still passes/fails on the real signal (no regression).
# ----------------------------------------------------------------------
def test_lint_passes_on_clean_valid_json(monkeypatch):
    _fake_run(monkeypatch, returncode=0, json={"errorCount": 0, "findings": []})
    assert hf_tools.run_lint(SCENE)["ok"] is True


def test_lint_fails_on_valid_json_with_errors(monkeypatch):
    _fake_run(monkeypatch, returncode=0,
              json={"errorCount": 2, "findings": [
                  {"code": "x", "severity": "error", "message": "m"}]})
    assert hf_tools.run_lint(SCENE)["ok"] is False


def test_missing_binary_still_fails_closed(monkeypatch):
    # The pre-existing fail-closed behavior (no npx) must be preserved.
    _fake_run(monkeypatch, error="npx not found")
    assert hf_tools.run_lint(SCENE)["ok"] is False
    assert hf_tools.run_validate(SCENE)["ok"] is False
    assert hf_tools.run_inspect(SCENE)["ok"] is False
