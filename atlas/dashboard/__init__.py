"""atlas.dashboard — an ADDITIVE, read-mostly monitoring dashboard over the Showrunner.

A small FastAPI service that reads real system state (registry, project.json,
artifacts, souls, eval scorecards) and serves the approved "Control Room" UI as
typed JSON + static assets. It is read-mostly: the ONLY mutation is a gate
approval, and that is delegated unchanged to `session.AtlasSession.approve_gate`
(→ `pipeline.produce(slug, approve=[gate])`). It never reorders stages, edits a
contract, touches a gate's logic, or writes chat_state.json.

Run it from the atlas/ directory (same convention as `python -m eval.inspector`):

    cd atlas
    python -m dashboard.server            # or: uvicorn dashboard.app:app

Intra-package imports use `from dashboard import ...`; atlas siblings
(`registry`, `project_view`, `pipeline`, `contracts`, `chat_state`, `rubric`,
`eval`) are imported top-level because the process runs with CWD=atlas.
"""
