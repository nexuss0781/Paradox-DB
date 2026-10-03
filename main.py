"""Single entrypoint for Wasmer and other Python web hosts.

The application implementation remains in ``gateway/app``. This thin root
module makes repository autodetection reliable while exposing a safe ASGI
failure app if an import-time dependency or configuration error prevents startup.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# The gateway package uses ``app`` as its top-level package name. Add the
# gateway directory before importing it so both ``python main.py`` and
# ``uvicorn main:app`` work from the repository root.
_GATEWAY_DIR = Path(__file__).resolve().parent / "gateway"
if str(_GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(_GATEWAY_DIR))

# When an ASGI runner imports ``app:app`` from the repository root, this file
# is itself the ``app`` module. Mark it as a package so ``app.main`` resolves.
if __name__ == "app":
    __path__ = [str(_GATEWAY_DIR / "app")]

from app.startup_diagnostics import (
    create_startup_failure_app,
    redact_diagnostic,
)

try:
    from app.main import app
except Exception as exc:  # noqa: BLE001
    # Convert dependency/config import failures into safe ASGI diagnostics.
    detail = redact_diagnostic(f"{type(exc).__name__}: {exc}")
    logging.getLogger("paradox.startup").error(
        "Gateway application import failed: %s", detail
    )
    app = create_startup_failure_app(exc)


if __name__ == "__main__":
    import uvicorn

    # Pass the already-imported application object so the startup diagnostic
    # fallback is used rather than re-importing the failing module string.
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        workers=1,
    )

__all__ = ["app"]
