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

import pathlib

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from dashboard import data, media, security

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

    # ---------------- the ONE write: gate approval ----------------
    @app.post("/api/gate/{slug}/approve")
    async def approve(slug: str, request: Request):
        return _approve_gate(app, slug, await _json_body(request))

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
