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

from dashboard import atlas_request, chat, data, intake, media, publish, security, settings_store

HERE = pathlib.Path(__file__).resolve().parent
STATIC = HERE / "static"


class _RevalidatingStatic(StaticFiles):
    """Serve the Control Room UI with `Cache-Control: no-cache` so a browser always
    revalidates app.js/styles.css/index.html against the ETag the server already
    sends. Unchanged files come back as a cheap 304; a shipped fix is picked up on
    the next refresh with no stale-JS footgun (no manual Ctrl+Shift+R needed)."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers.setdefault("Cache-Control", "no-cache")
        return resp


def _projects_dir(app: FastAPI) -> pathlib.Path:
    return app.state.projects_dir


def create_app(projects_dir: pathlib.Path | str | None = None) -> FastAPI:
    """App factory. `projects_dir` is injectable so tests point at a disposable copy
    instead of the real projects (never test the gold fixture's write path directly)."""
    app = FastAPI(title="YT-AGENTS Control Room", docs_url="/api/docs",
                  openapi_url="/api/openapi.json")
    app.state.projects_dir = pathlib.Path(
        projects_dir) if projects_dir else data.DEFAULT_PROJECTS_DIR
    app.state.produce_fn = None  # tests inject a fake pipeline.produce here
    app.state.decide_fn = None   # tests inject a fake decider here; None → the real LLM decider
    app.state.find_topics_fn = None  # tests inject a fake Scout find_topics here (#1.5)
    app.state.chat_fn = None  # tests inject a fake agentic chat here — never the real LLM
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

    @app.post("/api/gate/{slug}/guide")
    async def gate_guide(slug: str, request: Request):
        try:
            security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await _json_body(request)
        instructions = (body.get("instructions") or "").strip()
        if not instructions:
            return JSONResponse({"error": "instructions required"}, status_code=400)
        out = _get_dispatcher(app).guide(slug, instructions, initiator="ceo")
        return JSONResponse({"result": "guided", "slug": slug, **out})

    @app.post("/api/gate/{slug}/kill")
    async def gate_kill(slug: str, request: Request):
        try:
            security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await _json_body(request)
        out = _get_dispatcher(app).kill(slug, (body.get("reason") or "").strip(),
                                        initiator="ceo")
        return JSONResponse({"result": "killed", "slug": slug, **out})

    # ---------------- the single front door: atlas supervisor request (T1 only) ---------
    @app.post("/api/atlas/request")
    async def atlas_request_route(request: Request):
        body = await _json_body(request)
        intent = body.get("intent")
        args = body.get("args") or {}
        try:
            out = atlas_request.handle_request(_get_dispatcher(app),
                                               app.state.settings_path, intent, args)
            return JSONResponse({"ok": True, **out})
        except atlas_request.UnknownIntent as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        except KeyError as e:
            return JSONResponse({"ok": False, "error": f"missing arg {e}"}, status_code=400)

    # ---------------- T3 publish package (read-only review shell — NO fire path) -------
    @app.get("/api/publish/{slug}")
    def publish_package(slug: str):
        """The exact final package for the T3 review modal (title/description/tags/
        thumbnail/visibility/schedule + routed channel + blockers). Read-only: there is NO
        endpoint that fires a publish — real publishing arrives with Herald (#6), so the
        'no auto-fire-unreviewed' property (§4 T3 / E8) holds by construction here."""
        try:
            security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        pkg = publish.publish_package(_projects_dir(app), slug, app.state.settings_path)
        if pkg is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return J(pkg)

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

    @app.post("/api/rerun/{slug}")
    async def rerun(slug: str, request: Request):
        # T1 reversible: re-run an existing video. No body (or {}) → from the start;
        # {"from_stage": "<stage>"} → that stage + everything downstream (only a stage
        # that already ran). The dispatcher validates from_stage against the spine.
        try:
            security.resolve_project_dir(_projects_dir(app), slug)
        except security.UnsafePathError:
            return JSONResponse({"error": "not found"}, status_code=404)
        if data.project_detail(_projects_dir(app), slug) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        body = await _json_body(request)
        from_stage = body.get("from_stage")
        if from_stage is not None and not security.safe_segment(str(from_stage)):
            return JSONResponse({"error": "bad from_stage"}, status_code=400)
        out = _get_dispatcher(app).rerun(slug, from_stage=from_stage, initiator="ceo")
        if not out.get("rerunning"):
            return JSONResponse(out, status_code=409)
        return J(out)

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

    # ---------------- niche intake: niche → Scout find_topics → candidates (#1.5) -----
    @app.post("/api/intake/topics")
    async def intake_topics(request: Request):
        import asyncio
        body = await _json_body(request)
        niche = (body.get("niche") or "").strip()
        import validate
        ok, reason = validate.validate_niche(niche)
        if not ok:
            return JSONResponse({"error": reason}, status_code=400)
        fn = app.state.find_topics_fn or intake.default_find_topics
        try:
            res = await asyncio.to_thread(fn, niche)
        except Exception as exc:  # noqa: BLE001 — Scout failure degrades, never 500s
            res = {"ok": False, "text": str(exc)}
        res = res or {}
        if not res.get("ok"):
            return J({"ok": False, "niche": niche, "candidates": [],
                      "error": res.get("text") or "Scout found no topics for that niche."})
        settings = settings_store.load_settings(app.state.settings_path)
        mode = (settings.get("defaults", {}) or {}).get("intake_mode", "pick")
        return J({"ok": True, "niche": niche,
                  "candidates": intake.normalize_candidates(res.get("ideas")),
                  "intake_mode": mode, "auto_pick": mode == "auto"})

    # ---------------- agentic chat: read-grounded, T1-ONLY (spec §4/§8) ---------------
    @app.post("/api/chat")
    async def chat_turn(request: Request):
        """Stream one agentic chat turn (SSE frames). The chat lives on the LLM plane: it
        answers grounded questions and may PROPOSE a T1 reversible action, but the final
        frame only ever carries a T1 action — anything else is dropped here as a defence in
        depth, so the LLM plane can never even SURFACE a control that satisfies T2/T3."""
        import asyncio
        body = await _json_body(request)
        message = (body.get("message") or "").strip()
        history = body.get("history") if isinstance(body.get("history"), list) else []
        if not message:
            return JSONResponse({"error": "a message is required"}, status_code=400)
        fn = app.state.chat_fn or _default_chat_fn(app)

        async def gen():
            loop = asyncio.get_running_loop()
            q: asyncio.Queue = asyncio.Queue()
            box = {"result": None}

            def on_text(t: str) -> None:
                loop.call_soon_threadsafe(q.put_nowait, ("text", t))

            async def run():
                try:
                    box["result"] = await asyncio.to_thread(
                        fn, message, history=history, on_text=on_text)
                except Exception as exc:  # noqa: BLE001 — containment; never crash the stream
                    loop.call_soon_threadsafe(q.put_nowait, ("error", str(exc)))
                finally:
                    loop.call_soon_threadsafe(q.put_nowait, ("done", None))

            worker = asyncio.create_task(run())
            err = None
            while True:
                kind, payload = await q.get()
                if kind == "done":
                    break
                if kind == "error":
                    err = payload
                elif kind == "text":
                    yield f"data: {json.dumps({'type': 'text', 't': payload})}\n\n"
            await worker
            res = box["result"] or {}
            if err:
                yield f"data: {json.dumps({'type': 'error', 'error': err})}\n\n"
                return
            action = res.get("action")
            # SAFETY (defence in depth): only a T1 action may ever reach the UI.
            if not (isinstance(action, dict) and chat.is_t1_action(action.get("kind"))):
                action = None
            frame = {"type": "done", "reply": res.get("reply") or "", "action": action}
            yield f"data: {json.dumps(security.redact(frame))}\n\n"

        return StreamingResponse(
            gen(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/chat/act")
    async def chat_act(request: Request):
        """Execute a CEO-CONFIRMED T1 action the chat proposed (the light confirm of §4).
        REJECTS anything outside the T1 set — there is no `approve`/`publish` kind, so this
        endpoint structurally cannot satisfy a T2 gate or a T3 publish. Tagged
        initiator='chat' for the §4 audit (E7)."""
        body = await _json_body(request)
        kind = body.get("kind")
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        if not chat.is_t1_action(kind):
            return JSONResponse(
                {"error": "the chat can only initiate reversible (T1) actions — approving a "
                          "gate or publishing must happen on the deterministic UI",
                 "kind": kind}, status_code=400)
        if kind == "cancel" and not security.safe_segment((args.get("slug") or "").strip()):
            return JSONResponse({"error": "bad slug"}, status_code=400)
        try:
            out = chat.execute_action(_get_dispatcher(app), app.state.settings_path,
                                      kind, args, initiator="chat")
        except (chat.NotReversibleError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return J(out)

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
        app.mount("/", _RevalidatingStatic(directory=str(STATIC), html=True), name="static")
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
    Uses the test-injected `decide_fn` when present (so tests stay offline), else the
    real LLM decider. One dispatcher per process; rebuildable from disk on restart."""
    if app.state.dispatcher is None:
        import dispatcher as dmod
        import atlas_decider
        decide_fn = getattr(app.state, "decide_fn", None)
        if decide_fn is None:
            decide_fn = atlas_decider.make_llm_decider()
        try:
            import dashboard.settings_store as _ss
            _budget = float(_ss.load_settings(app.state.settings_path)["defaults"]
                            .get("render_budget_sec", 600.0))
        except Exception:  # noqa: BLE001 — settings unreadable → safe default
            _budget = 600.0
        app.state.dispatcher = dmod.Dispatcher(
            projects_dir=app.state.projects_dir,
            produce_fn=app.state.produce_fn,
            max_in_flight=app.state.max_in_flight,
            max_retries=getattr(app.state, "max_retries", 1),
            decide_fn=decide_fn,
            decider_model=atlas_decider.DECIDER_MODEL,
            render_budget_sec=_budget)
    return app.state.dispatcher


def _default_chat_fn(app: FastAPI):
    """The real agentic chat seam (Claude Agent SDK, T1-only tool surface). Bound to this
    app's projects dir + settings so the chat reads the live belt/gates/settings. Injectable
    via app.state.chat_fn so e2e/unit fake it — the real LLM never runs under test."""
    def fn(message: str, *, history=None, on_text=None):
        return chat.default_send(
            message, history=history, on_text=on_text,
            projects_dir=app.state.projects_dir, settings_path=app.state.settings_path)
    return fn


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


def _approve_gate(app: FastAPI, slug: str, body: dict) -> JSONResponse:
    """The T2 authorizing surface (spec §4): this is the ONE deterministic place a spine
    gate is satisfied, and the authorizing click reaches it ONLY from the deterministic UI
    (never the chat/LLM plane — `initiator="ceo"`). We resume through the BELT
    (`dispatcher.resume`) so the resumed render shares the belt's station single-occupancy
    instead of running a second, lock-less synchronous produce. We REFUSE a hard fact-check
    `block` (the spine would re-block + route back) and surface that truth instead. Idempotent:
    a second approve on an already-advanced project is `not at a gate` (409), never a re-run."""
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

    # Resume on the belt (shares station locks); wait for the spine's on-disk outcome so the
    # deterministic surface relays a synchronous result. initiator="ceo" — the §4 audit shows
    # no T2 write ever originated from the LLM plane.
    result = _get_dispatcher(app).resume(slug, gate, initiator="ceo", wait=True) or {}
    payload = {"result": "approved", "gate": gate, "slug": slug,
               "status": result.get("status"), "next_gate": result.get("gate"),
               "reason": result.get("reason"), "video": result.get("video")}
    return JSONResponse(security.redact(payload))


# Module-level default app (uvicorn dashboard.app:app) over the real projects dir.
app = create_app()
