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


# ---------------------------------------------------------------- gate approve (T2)
# The T2 approve now resumes through the belt dispatcher (spec §4): the deterministic
# surface satisfies the gate, the resumed run shares the belt's station locks, and the
# outcome is read back from DISK (the belt's source of truth). So we inject a fake
# produce_fn (dispatcher-compatible signature) that walks the project to `done` on disk —
# never a real engine/LLM. initiator="ceo" is recorded; no T2 write ever comes from chat.
def _inject_fake_produce(client, on_call, tmp_path):
    """Put a dispatcher-compatible fake on app.state.produce_fn BEFORE the belt builds, so
    the T2 resume runs the fake (never the real spine). `on_call(slug, approve, root)` does
    the disk transition + may record the call. Returns nothing — disk is the truth."""
    client._app.state.produce_fn = on_call


def _finish_on_disk(root, slug, *, write_video=True):
    """Flip a project's status to done on disk (what a resumed render does)."""
    import chat_state
    pp = pathlib.Path(root) / slug / "project.json"
    proj = chat_state.load_json(pp, {})
    proj["status"] = "done"
    for st in (proj.get("stages", {}) or {}).values():
        st["status"] = "done"
    chat_state.atomic_write_json(pp, proj)
    if write_video:
        (pathlib.Path(root) / slug / "video.mp4").write_bytes(b"\x00")


def test_approve_hard_block_409_routed_back(client, slugs, tmp_path):
    # Even with a fake produce injected, a hard block must be refused BEFORE any resume.
    calls = []

    def fake_produce(slug=None, approve=None, root=None, progress=None,
                     station_locks=None, should_cancel=None):
        calls.append((slug, list(approve or [])))
        _finish_on_disk(root, slug)
        return {"status": "done"}

    _inject_fake_produce(client, fake_produce, tmp_path)
    r = client.post(f"/api/gate/{slugs['hard_block']}/approve",
                    json={"gate": "factcheck"})
    assert r.status_code == 409
    body = r.json()
    assert body["result"] == "routed_back"
    assert body["approvable"] is False
    # the spine was NEVER resumed for a hard block — refused on the deterministic surface
    assert calls == []


def test_approve_clean_block_relays_disk_status(client, slugs, tmp_path):
    calls = []

    def fake_produce(slug=None, approve=None, root=None, progress=None,
                     station_locks=None, should_cancel=None):
        calls.append((slug, list(approve or [])))
        if progress is not None and hasattr(progress, "emit"):
            progress.emit("rendering")
        _finish_on_disk(root, slug)
        return {"status": "done"}

    _inject_fake_produce(client, fake_produce, tmp_path)
    r = client.post(f"/api/gate/{slugs['blocked_clean']}/approve",
                    json={"gate": "factcheck"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "approved"
    assert body["gate"] == "factcheck"
    assert body["status"] == "done"          # read back from disk after the resume
    assert body["video"] == "video.mp4"
    # the resumed run invoked the spine once with our gate (on a belt worker)
    assert calls == [(slugs["blocked_clean"], ["factcheck"])]
    # the §4 audit: the gate_approved event came from the CEO plane, never chat
    disp = client._app.state.dispatcher
    ga = [e for e in disp.events.since(0) if e["kind"] == "gate_approved"]
    assert ga and ga[-1]["initiator"] == "ceo"
    # and the relayed payload carries no leaks
    _assert_clean(r.text, "approve response")


def test_approve_not_at_gate_409(client, slugs, tmp_path):
    def fake_produce(slug=None, approve=None, root=None, progress=None,
                     station_locks=None, should_cancel=None):
        raise AssertionError("the spine must not be resumed for a non-gated project")

    _inject_fake_produce(client, fake_produce, tmp_path)
    r = client.post(f"/api/gate/{slugs['done']}/approve", json={"gate": "factcheck"})
    assert r.status_code == 409
    assert r.json().get("error") == "not at a gate"
