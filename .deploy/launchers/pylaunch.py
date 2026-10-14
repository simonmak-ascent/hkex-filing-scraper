#!/usr/bin/env python3
"""pylaunch — serve a Vercel-style ``BaseHTTPRequestHandler`` module over a
standard-library threaded HTTP server.

The tool repos expose their hosted MCP/REST endpoint as a Vercel function, i.e.
a ``BaseHTTPRequestHandler`` subclass named ``handler`` (or given). Vercel's
runtime calls it directly; off-platform we wrap it in ``ThreadingHTTPServer`` —
stdlib only, no framework dependency.

    pylaunch --app api.index:handler --host 127.0.0.1 --port 8020
"""

from __future__ import annotations

import argparse
import importlib
from http.server import ThreadingHTTPServer


def load(spec: str):
    mod_name, _, cls_name = spec.partition(":")
    module = importlib.import_module(mod_name)
    return getattr(module, cls_name or "handler")


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve a BaseHTTPRequestHandler module.")
    ap.add_argument(
        "--app", required=True, help="module:ClassName (default class 'handler')"
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    args = ap.parse_args()
    handler = load(args.app)
    srv = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"pylaunch: serving {args.app} on {args.host}:{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
