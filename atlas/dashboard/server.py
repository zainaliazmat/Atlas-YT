"""Entrypoint: `cd atlas && python -m dashboard.server [--port 8848] [--projects DIR]`.

Thin uvicorn launcher over create_app(). Kept separate from app.py so tests import
the factory without binding a port.
"""
from __future__ import annotations

import argparse

import uvicorn

from dashboard.app import create_app


def main() -> None:
    ap = argparse.ArgumentParser(description="YT-AGENTS Control Room dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--projects", default=None,
                    help="projects dir (default: atlas/projects)")
    args = ap.parse_args()
    app = create_app(args.projects)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
