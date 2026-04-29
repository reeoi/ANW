"""FastAPI human-review application skeleton."""

from __future__ import annotations

from fastapi import FastAPI

from config_loader import load_from_environment

app = FastAPI(title="ANP Human Review")


@app.get("/")
def index() -> dict[str, str | bool]:
    """Health-style landing endpoint for Sprint 1."""
    config = load_from_environment()
    return {"service": "ANP Human Review", "dry_run": config.is_dry_run}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("queue.human_review:app", host="127.0.0.1", port=8000, reload=False)
