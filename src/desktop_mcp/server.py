"""desktop-mcp server entrypoint.

Run (Windows, PowerShell):
    .venv\\Scripts\\python -m desktop_mcp.server            # stdio (default)
    .venv\\Scripts\\python -m desktop_mcp.server --http     # streamable-http on 127.0.0.1:8765

Tools are grouped by namespace prefix:
    system_*   generic OS/window/input tools          (system_tools.py)
    autocad_*  AutoCAD via COM/pyautocad              (adapters/autocad.py)
    capcut_*   CapCut via draft_content.json editing  (adapters/capcut.py)
"""
from __future__ import annotations

import argparse

from mcp.server.fastmcp import FastMCP

from . import system_tools
from .adapters import autocad, capcut

HOST = "127.0.0.1"  # never bind beyond localhost: this server controls the machine
PORT = 8765


def build_server() -> FastMCP:
    mcp = FastMCP("desktop-mcp", host=HOST, port=PORT)
    system_tools.register(mcp)
    autocad.register(mcp)
    capcut.register(mcp)
    return mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="desktop-mcp")
    parser.add_argument("--http", action="store_true",
                        help="Serve Streamable HTTP on 127.0.0.1:8765 instead of stdio")
    args = parser.parse_args()
    server = build_server()
    if args.http:
        server.run(transport="streamable-http")
    else:
        server.run(transport="stdio")


if __name__ == "__main__":
    main()
