#!/usr/bin/env python3
"""wsgilaunch — serve one or two WSGI apps (default + health) over a stdlib server.

hkex exposes its hosted endpoint as a WSGI callable (``api.mcp:app``) plus a
separate liveness app (``api.healthz:app``). This dispatcher serves both from one
process on a stdlib ``wsgiref`` server (no gunicorn dependency):

    wsgilaunch --app api.mcp:app --health api.healthz:app --port 8040
"""

from __future__ import annotations

import argparse
import importlib
from wsgiref.simple_server import make_server


def load(spec: str):
    mod_name, _, attr = spec.partition(":")
    return getattr(importlib.import_module(mod_name), attr or "app")


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve WSGI app(s) on a stdlib server.")
    ap.add_argument("--app", required=True, help="module:app (default attr 'app')")
    ap.add_argument(
        "--health", default="", help="module:app answered on the health paths"
    )
    ap.add_argument("--health-paths", default="/healthz,/api/healthz,/api/health")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()
    app = load(args.app)
    health = load(args.health) if args.health else None
    paths = {p for p in args.health_paths.split(",") if p}

    def dispatch(environ, start_response):
        if health is not None and environ.get("PATH_INFO", "") in paths:
            return health(environ, start_response)
        return app(environ, start_response)

    print(f"wsgilaunch: {args.app} on {args.host}:{args.port}", flush=True)
    make_server(args.host, args.port, dispatch).serve_forever()


if __name__ == "__main__":
    main()
