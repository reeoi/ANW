"""Static assets for the ANW dashboard.

Assets are stored in separate files under templates/ and static/ directories.
By default, files are **re-read from disk on every access** so you can edit
CSS/HTML/JS and see changes immediately — just refresh the browser.

Set ``ANW_HOT_RELOAD=0`` to disable and cache at import time (production mode).
"""

from __future__ import annotations

from pathlib import Path

from config_loader import get_env

_HERE = Path(__file__).resolve().parent
_HOT_RELOAD = get_env("ANW_HOT_RELOAD", "1").strip().lower() not in ("0", "false", "no")


def _read_asset(rel_path: str) -> str:
    path = _HERE / rel_path
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


if _HOT_RELOAD:
    # Hot reload: files are re-read from disk every time the module-level
    # names are accessed.  Python's __getattr__ (PEP 562) fires when a
    # module attribute is not found in the normal namespace, so we just
    # never define the constants at all.
    _ASSET_MAP = {
        "DASHBOARD_CSS": "static/dashboard.css",
        "DASHBOARD_BODY_TEMPLATE": "templates/dashboard.html",
        "DASHBOARD_JS": "static/dashboard.js",
    }

    def __getattr__(name: str) -> str:
        if name in _ASSET_MAP:
            return _read_asset(_ASSET_MAP[name])
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

else:
    # Production mode: read once at import time
    DASHBOARD_CSS = _read_asset("static/dashboard.css")
    DASHBOARD_BODY_TEMPLATE = _read_asset("templates/dashboard.html")
    DASHBOARD_JS = _read_asset("static/dashboard.js")
