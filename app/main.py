"""DeepDive API — Phase 02 skeleton."""

import os

from fastapi import Depends, FastAPI, Response ,HTTPException
from pydantic import BaseModel
from app.chunking import store_chunks
from app.youtube import ingest_video
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
class IngestRequest(BaseModel):
    """What the client sends to POST /videos.

    A Pydantic model rather than a raw dict: FastAPI validates the shape
    before the handler runs, so a request missing `url` gets a 422 with a
    readable message instead of reaching our code and raising KeyError.
    It also means the field appears in the auto-generated /docs page.
    """

    url: str


class IngestResponse(BaseModel):
    """What we send back.

    Declaring the response shape explicitly stops us accidentally leaking
    fields later -- if someone adds a column to Video, it does not silently
    appear in the public API.
    """

    video_id: str
    source: str
    duration_seconds: int | None
    snippet_count: int
    chunk_count: int
    chunking_run: str


@app.post("/videos", response_model=IngestResponse)
def ingest(payload: IngestRequest, session: Session = Depends(get_session)):
    """Fetch a video's transcript, store it, and chunk it.

    KNOWN LIMITATION -- THIS BLOCKS.
    Fetching a transcript takes seconds, and this handler holds a worker for
    that whole time. There is exactly one worker (Render sets
    WEB_CONCURRENCY=1 for 0.1 CPU), so a second concurrent ingestion queues
    behind the first, and Render's request timeout is around 100 seconds.

    The correct fix is a background job: return 202 Accepted immediately and
    have a worker do the fetch. That needs a queue, a second process, and
    state to track, and it is deferred deliberately rather than overlooked.
    Revisit when the frontend exists, or if a long video starts timing out.

    Both operations below are idempotent -- calling this twice on the same
    URL re-uses the stored transcript and the stored chunks rather than
    doing the work again. That matters because the 20 benchmark videos get
    hit repeatedly during evaluation runs and the transcript API is rate
    limited.
    """
    try:
        video = ingest_video(session, payload.url)
    except ValueError as exc:
        # extract_video_id raises ValueError on an unparseable URL. That is
        # the client's fault, so 400 -- not 500, which would claim our bug.
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        # Anything else is a fetch failure: video deleted, private, no
        # captions, or the API rate limiting us. 502 Bad Gateway says an
        # upstream service failed us, which is accurate and distinguishes
        # it from our own code breaking.
        print(f"ingest failed for {payload.url!r}: {exc!r}")
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch transcript: {type(exc).__name__}",
        )

    chunks = store_chunks(session, video)

    return IngestResponse(
        video_id=video.video_id,
        source=video.source,
        duration_seconds=video.duration_seconds,
        snippet_count=len(video.raw_transcript),
        chunk_count=len(chunks),
        chunking_run=chunks[0].chunking_run if chunks else "",
    )