"""The FastAPI app — typed JSON endpoints for the six screens + one sanctioned write.

Read-mostly: every GET reads real state via dashboard.data / media. The ONE mutation
is POST /api/gate/{slug}/approve, which delegates UNCHANGED to
session.AtlasSession.approve_gate → pipeline.produce(slug, approve=[gate]). The gate
logic (incl. a fact-check `block` that can never be approved away) runs in the spine,
not here — this endpoint only refuses to OFFER an approve the spine would reject, and
relays whatever the spine returns.

Run from the atlas/ dir:  uvicorn dashboard.app:app   (or python -m dashboard.server)
"""
from __future__ import annotations

import json
import pathlib

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from dashboard import data, media, security, settings_store

HERE = pathlib.Path(__file__).resolve().parent
STATIC = HERE / "static"


def _projects_dir(app: FastAPI) -> pathlib.Path:
    return app.state.projects_dir


def create_app(projects_dir: pathlib.Path | str | None = None) -> FastAPI:
    """App factory. `projects_dir` is injectable so tests point at a disposable copy
    instead of the real projects (never test the gold fixture's write path directly)."""
    app = FastAPI(title="YT-AGENTS Control Room", docs_url="/api/docs",
                  openapi_url="/api/openapi.json")
    app.state.projects_dir = pathlib.Path(
        projects_dir) if projects_dir else data.DEFAULT_PROJECTS_DIR
    # the AtlasSession used for the sanctioned gate write; built lazily (heavy import)
    app.state.session = None
    app.state.produce_fn = None  # tests inject a fake pipeline.produce here
    # the assembly-line dispatcher (the belt); built lazily, injectable for tests
    app.state.dispatcher = None
    app.state.max_in_flight = 2  # spec §6.6 honest target: ~2–3 videos in flight
    app.state.max_retries = 1    # bounded transient auto-retry; injectable for tests
    # Control-Room settings (niches/defaults/channels) — dashboard-owned JSON, injectable
    app.state.settings_path = settings_store.DEFAULT_PATH

    def J(payload):
        """Redact every payload as the final pass before it leaves the process."""
        return JSONResponse(security.redact(payload))

    # ---------------- screens (read-only) ----------------
    @app.get("/api/overview")
    def overview():
        return J(data.overview(_projects_dir(app)))

    @app.get("/api/projects")
    def projects():
        return J(data.list_projects(_projects_dir(app)))

    @app.get("/api/projects/{slug}")
    def project(slug: str):
        try:
            security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        det = data.project_detail(_projects_dir(app), slug)
        if det is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return J(det)

    @app.get("/api/projects/{slug}/stage/{key}")
    def stage(slug: str, key: str):
        try:
            security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        if not security.safe_segment(key):
            return JSONResponse({"error": "bad stage"}, status_code=400)
        det = data.stage_detail(_projects_dir(app), slug, key)
        if det is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return J(det)

    @app.get("/api/fleet")
    def fleet():
        return J(data.fleet(_projects_dir(app)))

    @app.get("/api/agents/{name}")
    def agent(name: str):
        if not security.safe_segment(name):
            return JSONResponse({"error": "bad name"}, status_code=400)
        det = data.agent_detail(_projects_dir(app), name)
        if det is None:
            return JSONResponse({"error": "unknown agent"}, status_code=404)
        return J(det)

    @app.get("/api/quality")
    def quality(slug: str | None = Query(None)):
        if slug is not None and not security.safe_segment(slug):
            return JSONResponse({"error": "bad slug"}, status_code=400)
        return J(data.quality(_projects_dir(app), slug))

    @app.get("/api/gate/{slug}")
    def gate(slug: str):
        try:
            security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        det = data.gate_detail(_projects_dir(app), slug)
        if det is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return J(det)

    # ---------------- media (streamed) ----------------
    @app.get("/api/media/{slug}/video")
    def video(slug: str, request: Request):
        try:
            pdir = security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        return media.serve_video(pdir, request.headers.get("range"))

    @app.get("/api/media/{slug}/draft/{rel:path}")
    def draft(slug: str, rel: str, request: Request):
        try:
            pdir = security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        return media.serve_relative_media(pdir, rel, request.headers.get("range"))

    @app.get("/api/artifact/{slug}/{name:path}")
    def artifact(slug: str, name: str):
        try:
            pdir = security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        return media.serve_artifact(pdir, name)

    # ---------------- the ONE write: gate approval (T2 — deterministic surface) ----
    @app.post("/api/gate/{slug}/approve")
    async def approve(slug: str, request: Request):
        return _approve_gate(app, slug, await _json_body(request))

    # ---------------- the belt: trigger / cancel / live state (T1 — reversible) ----
    @app.get("/api/belt")
    def belt():
        payload = data.belt(_projects_dir(app))
        payload["live"] = _get_dispatcher(app).live_state()
        return J(payload)

    @app.post("/api/trigger")
    async def trigger(request: Request):
        body = await _json_body(request)
        topic = (body.get("topic") or "").strip()
        brief = (body.get("brief") or "").strip()
        if not topic and not brief:
            return JSONResponse({"error": "a topic or brief is required"},
                                status_code=400)
        niche = body.get("niche")
        # Resolve the target length DASHBOARD-side: an explicit choice wins, else a niche's
        # configured default flows in from settings. The value is passed INTO the pipeline as
        # an arg — a pure engine never reads settings globally (§3/§11 decoupling).
        length = body.get("length")
        if not length and niche:
            length = settings_store.length_for_niche(
                settings_store.load_settings(app.state.settings_path), niche)
        out = _get_dispatcher(app).trigger(
            brief=brief or None, topic=topic or None, length=length,
            niche=niche, gates=bool(body.get("gates", True)), initiator="ceo")
        return J(out)

    @app.post("/api/cancel/{slug}")
    def cancel(slug: str):
        try:
            security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        return J(_get_dispatcher(app).cancel(slug, initiator="ceo"))

    @app.post("/api/retry/{slug}")
    def retry(slug: str):
        # T1 reversible: re-run a PARKED failed video. Only the deterministic UI offers it,
        # and only for a transient failure (the spine still won't retry a deterministic one).
        try:
            security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        if data.project_detail(_projects_dir(app), slug) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return J(_get_dispatcher(app).retry(slug, initiator="ceo"))

    # ---------------- the live activity feed: a snapshot of the event ring (§4 audit) ----
    @app.get("/api/activity")
    def activity(since: int = Query(0), kind: str | None = Query(None),
                 initiator: str | None = Query(None)):
        """Newest-first snapshot of the dispatcher's event ring — the same events the SSE
        stream tails, so the feed renders history immediately then live-tails. Carries the
        `initiator` plane on every row (the §4 audit property). Optional kind/initiator
        filters are applied server-side."""
        disp = _get_dispatcher(app)
        evs = disp.events.since(since)
        if kind:
            evs = [e for e in evs if e.get("kind") == kind]
        if initiator:
            evs = [e for e in evs if e.get("initiator") == initiator]
        evs = sorted(evs, key=lambda e: e["id"], reverse=True)[:200]
        return J({"events": evs, "last_id": disp.events.last_id})

    # ---------------- live updates: SSE with Last-Event-ID backfill (spec §10) ----
    @app.get("/api/events")
    async def events(request: Request):
        disp = _get_dispatcher(app)
        raw = (request.headers.get("last-event-id")
               or request.query_params.get("last_event_id") or "0")
        try:
            start = int(raw)
        except (TypeError, ValueError):
            start = 0
        return StreamingResponse(
            _event_stream(disp, start, request),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache",
                     "X-Accel-Buffering": "no", "Connection": "keep-alive"})

    # ---------------- settings: niches / defaults / channels (T1 reversible) ----------
    @app.get("/api/settings")
    def get_settings():
        return J(settings_store.public_settings(app.state.settings_path))

    @app.put("/api/settings")
    async def put_settings(request: Request):
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 — unparseable body
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"error": "settings must be a JSON object"},
                                status_code=400)
        ok, errors, _clean = settings_store.validate_settings(body)
        saved = settings_store.save_settings(app.state.settings_path, body)
        pub = settings_store.public_settings(app.state.settings_path)
        return J({"ok": ok, "errors": errors, "settings": saved, "public": pub})

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "projects_dir": str(_projects_dir(app).name)}

    # ---------------- static Control Room UI ----------------
    if STATIC.exists():
        app.mount("/", StaticFiles(directory=str(STATIC), html=True), name="static")
    else:  # pragma: no cover - static built by the frontend workstream
        @app.get("/")
        def _placeholder():
            return HTMLResponse("<h1>Control Room API up — static UI not built yet.</h1>")

    return app


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:  # noqa: BLE001 — empty/invalid body is just {}
        return {}


def _get_dispatcher(app: FastAPI):
    """Lazily build the assembly-line dispatcher (the belt). Uses the test-injected
    `produce_fn` when present (so e2e never runs a real engine), else the real spine.
    One dispatcher per process; its belt state is rebuildable from disk on restart."""
    if app.state.dispatcher is None:
        import dispatcher as dmod
        app.state.dispatcher = dmod.Dispatcher(
            projects_dir=app.state.projects_dir,
            produce_fn=app.state.produce_fn,
            max_in_flight=app.state.max_in_flight,
            max_retries=getattr(app.state, "max_retries", 1))
    return app.state.dispatcher


async def _event_stream(disp, start: int, request: Request):
    """SSE generator (spec §10): replay events since `start` (Last-Event-ID backfill), then
    live-tail the ring; emit a keepalive comment when idle and stop on client disconnect.
    Each connection has its own generator → natural multi-tab fan-out."""
    import asyncio
    last_id = start
    while True:
        if await request.is_disconnected():
            break
        evs = disp.events.since(last_id)
        for ev in evs:
            last_id = ev["id"]
            yield f"id: {ev['id']}\ndata: {json.dumps(security.redact(ev))}\n\n"
        if not evs:
            yield ": keepalive\n\n"
        await asyncio.sleep(1.0)


def _get_session(app: FastAPI):
    """Lazily build the AtlasSession used for the sanctioned gate write.

    We construct it DIRECTLY (not via .start()) so the dashboard NEVER touches the
    real chat_state.json and never boots the heavy Orchestrator:
      * state_path → a throwaway temp file (approve_gate only appends to an in-memory
        state dict; nothing is distilled here, so even that temp file stays untouched),
      * build_orch → a no-op (approve_gate uses only `produce_fn`, never the orch),
      * produce_fn → the real pipeline.produce by default (the sanctioned write path),
        or a test-injected fake via app.state.produce_fn.
    The constraint "never touch chat_state.json" is honoured structurally here.
    """
    if app.state.session is None:
        import os
        import tempfile
        import session as session_mod
        # a per-process unique scratch path (never the real chat_state.json, never a
        # shared/predictable temp name). approve_gate only appends to the in-memory
        # state dict, so nothing is actually written here — but if that ever changes
        # it stays isolated to this process.
        scratch = (pathlib.Path(tempfile.gettempdir())
                   / f"atlas_dashboard_session.{os.getpid()}.json")
        app.state.session = session_mod.AtlasSession(
            state={"summary": "", "transcript": [], "pending": None},
            distiller=lambda summary, transcript: summary,
            state_path=scratch,
            build_orch=lambda progress: None,
            projects_dir=app.state.projects_dir,
            produce_fn=app.state.produce_fn,
        )
    return app.state.session


def _approve_gate(app: FastAPI, slug: str, body: dict) -> JSONResponse:
    """Delegate to session.approve_gate. We REFUSE to call approve for a hard
    fact-check `block` (the spine would reject it and route back) — we surface the
    routed-back truth instead. For every approvable gate we relay the spine's result
    verbatim (idempotent: a second approve on an already-advanced project is a no-op
    resume in the spine)."""
    projects_dir = app.state.projects_dir
    try:
        security.resolve_project_dir(projects_dir, slug)
    except security.UnsafePathError:
        return JSONResponse({"error": "not found"}, status_code=404)

    det = data.gate_detail(projects_dir, slug)
    if det is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    gate = body.get("gate") or det.get("gate")
    if not det.get("blocked") or not gate:
        return JSONResponse(
            {"error": "not at a gate", "status": det.get("status")}, status_code=409)
    if det.get("hard_block"):
        # Never offer an approve the spine rejects — report the routed-back reality.
        return JSONResponse({
            "result": "routed_back", "approvable": False, "gate": gate, "slug": slug,
            "reason": "Fact-check returned a BLOCK verdict — it cannot be approved "
                      "away. The script must be revised and re-checked.",
        }, status_code=409)

    sess = _get_session(app)
    statuses: list[str] = []
    result = sess.approve_gate(slug, gate, on_status=statuses.append) or {}
    payload = {"result": "approved", "gate": gate, "slug": slug,
               "status": result.get("status"), "next_gate": result.get("gate"),
               "reason": result.get("reason"), "video": result.get("video"),
               "errors": result.get("errors"), "progress": statuses[-6:]}
    return JSONResponse(security.redact(payload))


# Module-level default app (uvicorn dashboard.app:app) over the real projects dir.
app = create_app()
