"""DeepDive API — Phase 02 skeleton."""

import os

from fastapi import FastAPI

SERVICE_NAME = "deepdive-api"

# Render injects RENDER_GIT_COMMIT with the SHA of the commit it built.
# Not set locally, so fall back to something recognisable.
GIT_COMMIT = os.getenv("RENDER_GIT_COMMIT", "local")

app = FastAPI(title="YouTube Deep-Dive API", version="0.1.0")


@app.get("/")
def root():
    return {"service": SERVICE_NAME, "docs": "/docs"}


@app.get("/health")
def health():
    """Liveness check. Deliberately touches no dependencies."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "commit": GIT_COMMIT[:7],
    }