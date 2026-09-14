"""DeepDive API — Phase 02 skeleton."""

import os

from fastapi import Depends, FastAPI, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

SERVICE_NAME = "deepdive-api"

# Render injects RENDER_GIT_COMMIT with the SHA of the commit it built.
# Not set locally, so fall back to something recognisable.
GIT_COMMIT = os.getenv("RENDER_GIT_COMMIT", "local")

app = FastAPI(title="YouTube Deep-Dive API", version="0.1.0")


# api_route rather than @app.get so this answers HEAD too. Render's platform
# probe sends HEAD (a GET that returns headers without a body, which is why
# it's used for liveness checks). With GET only, every probe logged a 405 --
# noise that trains you to ignore your own logs.
@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"service": SERVICE_NAME, "docs": "/docs"}


@app.get("/health")
def health():
    """Liveness check: is this process alive. Touches no dependencies."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "commit": GIT_COMMIT[:7],
    }


@app.get("/ready")
def ready(response: Response, session: Session = Depends(get_session)):
    """Readiness check: can we actually serve traffic right now.

    Separate from /health on purpose. /health answers "is this process
    alive" and must not fail when a dependency blips, because a platform
    that restarts on health-check failure would restart us during a
    transient database hiccup -- fixing nothing and dropping in-flight
    requests. /ready answers "are dependencies reachable", and is allowed
    to say no.
    """
    try:
        # Cheapest query that still proves the whole path: pool, network,
        # TLS, authentication, and Postgres actually responding. A count(*)
        # would also test the schema -- a different question, and it gets
        # slower as the table grows.
        session.execute(text("select 1"))
        return {"status": "ready", "database": "ok"}
    except Exception as exc:
        # 503 Service Unavailable: we are up but cannot serve. Distinct from
        # 500, which would mean the request itself broke.
        response.status_code = 503
        # str(exc) on a SQLAlchemy connection error routinely embeds the
        # connection URL, password included. On a public endpoint that is a
        # credential leak triggered by a database outage. Detail goes to
        # stdout where Render captures it; the caller gets a class name.
        print(f"readiness check failed: {exc!r}")
        return {"status": "not_ready", "database": type(exc).__name__}