"""Single entrypoint for Wasmer and other Python web hosts.

The application implementation remains in ``gateway/app``. This thin root
module makes repository autodetection reliable: Wasmer can start it with
``python app.py`` while the exported ``app`` object remains available for
ASGI runners.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


# The gateway package uses ``app`` as its top-level package name. Add the
# gateway directory before importing it so both ``python app.py`` and
# ``uvicorn app:app`` work from the repository root.
_GATEWAY_DIR = Path(__file__).resolve().parent / "gateway"
if str(_GATEWAY_DIR) not in sys.path:
    sys.path.insert(0, str(_GATEWAY_DIR))

# When an ASGI runner imports ``app:app`` from the repository root, this file
# is itself the ``app`` module. Mark it as a package so the existing
# ``app.main`` imports resolve to the gateway implementation below.
if __name__ == "app":
    __path__ = [str(_GATEWAY_DIR / "app")]

from app.main import app  # noqa: E402


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        workers=1,
    )

__all__ = ["app"]
