"""Per-app adapters (one module per catalog app).

Discovery: ``register_all`` imports every non-underscore module in this
package and calls its ``register(mcp)``. A broken adapter is reported on
stderr and skipped — it must never take down the core server.
"""
from __future__ import annotations

import importlib
import pkgutil
import sys


def register_all(mcp) -> list[str]:
    registered: list[str] = []
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f".{mod.name}", __package__)
            module.register(mcp)
            registered.append(mod.name)
        except Exception as exc:  # noqa: BLE001 — isolate broken adapters
            print(f"[desktop-mcp] app adapter '{mod.name}' failed to load: {exc}",
                  file=sys.stderr)
    return registered
