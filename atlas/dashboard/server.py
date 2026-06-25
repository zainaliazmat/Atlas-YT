"""Entrypoint: `cd atlas && python -m dashboard.server [--port 8848] [--projects DIR]`.

Thin uvicorn launcher over create_app(). Kept separate from app.py so tests import
the factory without binding a port.
"""
from __future__ import annotations

import argparse

import uvicorn

from dashboard.app import _get_dispatcher, create_app


def main() -> None:
    ap = argparse.ArgumentParser(description="YT-AGENTS Control Room dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--projects", default=None,
                    help="projects dir (default: atlas/projects)")
    args = ap.parse_args()
    app = create_app(args.projects)
    # The belt's in-flight state is ephemeral (spec §6.2): a prior session that died
    # mid-stage left 'zombie' videos stuck at running/queued on disk. Park them as
    # `interrupted` on startup so the belt is honest and they can be Re-run on demand.
    parked = _get_dispatcher(app).reconcile_interrupted()
    if parked:
        print(f"reconciled {len(parked)} interrupted video(s): {', '.join(parked)}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
