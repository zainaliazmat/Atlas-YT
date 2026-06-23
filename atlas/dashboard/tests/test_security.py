"""Leak + traversal + gate-approval safety tests.

- Path traversal slugs never return 200-with-content.
- Non-whitelisted artifact names are refused.
- No response body leaks home paths / secrets.
- redact() collapses an injected absolute path.
- Gate approve: hard_block -> 409 routed_back; approvable -> 200 approved via an
  INJECTED fake produce_fn (no real pipeline/LLM, no real chat_state.json write).
"""
from __future__ import annotations

import pathlib

from dashboard import security


# ---------------------------------------------------------------- traversal
# Slugs that actually REACH the {slug} route over HTTP. Bare "."/".." are dropped:
# the HTTP client (httpx/RFC-3986) normalizes "/api/projects/." -> "/api/projects/"
# before transmission, so they never arrive as a slug — they're exercised directly
# against the guard in test_guard_rejects_dot_segments instead.
TRAVERSAL_SLUGS = [
    "..%2f..%2f..%2fetc%2fpasswd",
    "%2e%2e%2f%2e%2e%2fetc%2fpasswd",
    "..%2fdone",
    "%2e%2e",
    "%2e",
    "....//....//etc/passwd",
    "done%2f..%2f..%2fetc%2fpasswd",
    "etc%2fpasswd",
]


def test_guard_rejects_dot_segments():
    # The backend guard itself must reject "."/".." and traversal, independent of any
    # client-side URL normalization.
    import pathlib
    for bad in (".", "..", "../x", "a/b", "", "a/../b"):
        assert security.safe_segment(bad) is False, bad
    root = pathlib.Path("/tmp")
    for bad in (".", "..", "../../etc", "/etc/passwd"):
        try:
            security.resolve_project_dir(root, bad)
            raised = False
        except security.UnsafePathError:
            raised = True
        assert raised, f"resolve_project_dir accepted {bad!r}"


def test_traversal_project_detail(client):
    for s in TRAVERSAL_SLUGS:
        r = client.get(f"/api/projects/{s}")
        assert r.status_code in (400, 404), f"{s!r} -> {r.status_code}"
        assert "root:" not in r.text  # never the contents of /etc/passwd


def test_traversal_gate(client):
    for s in TRAVERSAL_SLUGS:
        r = client.get(f"/api/gate/{s}")
        assert r.status_code in (400, 404), f"{s!r} -> {r.status_code}"


def test_traversal_video(client):
    for s in TRAVERSAL_SLUGS:
        r = client.get(f"/api/media/{s}/video")
        assert r.status_code in (400, 404), f"{s!r} -> {r.status_code}"
        assert "root:" not in r.text


def test_traversal_artifact(client, slugs):
    # escape attempts through the artifact name (whitelist + containment both block)
    for name in ("../project.json", "../../etc/passwd", "..%2fproject.json",
                 "audio/../../project.json"):
        r = client.get(f"/api/artifact/{slugs['done']}/{name}")
        assert r.status_code in (400, 404), f"{name!r} -> {r.status_code}"
        assert "root:" not in r.text


def test_non_whitelisted_artifact_400(client, slugs):
    for name in ("video.mp4", "chat_state.json", ".env", "passwd"):
        r = client.get(f"/api/artifact/{slugs['done']}/{name}")
        assert r.status_code == 400, f"{name!r} -> {r.status_code}"


# ---------------------------------------------------------------- leak scan
# Secret VALUES / filesystem layout that must never reach a response body.
# NOTE: the agent's env-var SWITCH NAME (e.g. "SAGE_LLM") is intentionally surfaced
# (data.provider_for -> {"switch": var}); it is a config knob name, NOT a secret
# value, so "_LLM" is excluded from the raw-text scan and checked structurally below.
_FORBIDDEN = ["/home/", str(pathlib.Path.home()), "api_key", "API_KEY", "sk-",
              "AIza", ".env"]


def _assert_clean(text: str, where: str):
    for needle in _FORBIDDEN:
        assert needle not in text, f"LEAK: {needle!r} found in {where}"


def test_no_leak_across_endpoints(client, slugs):
    fleet = client.get("/api/fleet").json()
    paths = ["/api/overview", "/api/projects", "/api/fleet", "/api/quality"]
    paths += [f"/api/agents/{a['name']}" for a in fleet["agents"]]
    paths += [f"/api/projects/{s}" for s in slugs.values()]
    paths += [f"/api/gate/{s}" for s in slugs.values()]
    for p in paths:
        r = client.get(p)
        _assert_clean(r.text, p)


def test_switch_name_is_only_llm_reference(client):
    # The only place an "_LLM" token may appear is the agent's `switch` field (the env
    # var NAME). Its value must look like a switch name (UPPER_SNAKE ending _LLM), never
    # a secret payload, and the env var's VALUE (a provider like 'claude') is never the
    # switch field. This guards against an actual secret ever riding in via that field.
    fleet = client.get("/api/fleet").json()["agents"]
    for a in fleet:
        det = client.get(f"/api/agents/{a['name']}").json()
        switch = det.get("switch", "")
        assert switch.endswith("_LLM"), switch
        assert switch.isupper() and " " not in switch
        # provider/model are surfaced names, not secrets
        assert "sk-" not in det.get("model", "")


def test_redact_collapses_absolute_path():
    home = str(pathlib.Path.home())
    payload = {"path": f"{home}/Documents/YT-AGENTS/atlas/projects/foo/script.json",
               "other": "/home/someone/secret/.env",
               "API_KEY": "sk-should-be-stripped"}
    out = security.redact(payload)
    # absolute home path collapsed to project-relative (no /home/, no real home)
    assert "/home/" not in out["path"]
    assert home not in out["path"]
    assert out["path"].startswith("projects/")
    assert "/home/" not in out["other"]
    # secret-hinting key value dropped entirely
    assert out["API_KEY"] == "***"


def test_redact_strips_secret_keys():
    out = security.redact({"openai_api_key": "sk-xxx", "ATLAS_LLM": "claude",
                           "bearer_token": "abc", "nested": {"password": "p"}})
    assert out["openai_api_key"] == "***"
    assert out["ATLAS_LLM"] == "***"      # _llm$ hint
    assert out["bearer_token"] == "***"
    assert out["nested"]["password"] == "***"


# ---------------------------------------------------------------- gate approve
class _DummyOrch:
    """A no-op orchestrator so AtlasSession.start() never boots the heavy engine."""
    adapters: dict = {}


def _inject_fake_session(client, produce_fn, tmp_path):
    """Pre-build a cheap AtlasSession with a tmp state_path + fake produce, and put it
    on app.state.session so _get_session never lazy-builds the real (heavy) one and the
    real chat_state.json is never touched."""
    import session as session_mod
    app = client._app
    state_path = pathlib.Path(tmp_path) / "disposable_chat_state.json"
    sess = session_mod.AtlasSession.start(
        state_path=state_path,
        build_orch=lambda progress: _DummyOrch(),
        projects_dir=app.state.projects_dir,
        produce_fn=produce_fn,
    )
    app.state.session = sess
    return state_path


def test_approve_hard_block_409_routed_back(client, slugs, tmp_path):
    # Even with a fake produce injected, a hard block must be refused BEFORE produce.
    calls = []

    def fake_produce(slug=None, approve=None, progress=None):
        calls.append((slug, approve))
        return {"status": "done", "video": "video.mp4", "slug": slug}

    state_path = _inject_fake_session(client, fake_produce, tmp_path)
    r = client.post(f"/api/gate/{slugs['hard_block']}/approve",
                    json={"gate": "factcheck"})
    assert r.status_code == 409
    body = r.json()
    assert body["result"] == "routed_back"
    assert body["approvable"] is False
    # produce must NOT have been called for a hard block
    assert calls == []
    # real chat_state.json untouched; our tmp state_path not written either (no distill)
    assert not state_path.exists()


def test_approve_clean_block_relays_fake_status(client, slugs, tmp_path):
    calls = []

    def fake_produce(slug=None, approve=None, progress=None):
        calls.append((slug, list(approve or [])))
        if progress is not None:
            progress.emit("rendering") if hasattr(progress, "emit") else None
        return {"status": "done", "video": "video.mp4", "slug": slug}

    state_path = _inject_fake_session(client, fake_produce, tmp_path)
    r = client.post(f"/api/gate/{slugs['blocked_clean']}/approve",
                    json={"gate": "factcheck"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "approved"
    assert body["gate"] == "factcheck"
    assert body["status"] == "done"          # relays the fake's status
    assert body["video"] == "video.mp4"
    # the fake WAS called exactly once with our gate
    assert calls == [(slugs["blocked_clean"], ["factcheck"])]
    # no real chat_state.json write (distill never runs); tmp state_path absent
    assert not state_path.exists()
    # and the relayed payload carries no leaks
    _assert_clean(r.text, "approve response")


def test_approve_not_at_gate_409(client, slugs, tmp_path):
    def fake_produce(slug=None, approve=None, progress=None):
        raise AssertionError("produce should not be called for a non-gated project")

    _inject_fake_session(client, fake_produce, tmp_path)
    r = client.post(f"/api/gate/{slugs['done']}/approve", json={"gate": "factcheck"})
    assert r.status_code == 409
    assert r.json().get("error") == "not at a gate"
