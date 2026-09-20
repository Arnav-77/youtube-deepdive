"""DeepDive API — Phase 02 skeleton."""

import os

from fastapi import Depends, FastAPI, Response ,HTTPException
from pydantic import BaseModel
from app.chunking import store_chunks
from app.youtube import ingest_video
from sqlalchemy import text
from sqlalchemy.orm import Session
import time

from app.chunking import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunking_run_label
from app.generation import PROMPT_VERSION, generate
from app.models import QueryLog
from app.retrieval import DEFAULT_TOP_K, search, to_log_payload
from app.youtube import extract_video_id

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
class AskRequest(BaseModel):
    """A question about one video.

    `video` accepts a URL in any of the forms extract_video_id handles, or a
    bare id. Forgiving on input because the caller is a browser and people
    paste whatever they copied.
    """

    video: str
    question: str
    top_k: int = DEFAULT_TOP_K


class Citation(BaseModel):
    """One [n] in the answer, resolved to a place in the video.

    The model produced only `number`. Everything else here comes from our
    own database, which is the whole point: the model cannot fabricate a
    timestamp because it never writes one.
    """

    number: int
    chunk_id: int
    start_seconds: float
    end_seconds: float
    url: str


class AskResponse(BaseModel):
    video_id: str
    question: str
    answer: str
    citations: list[Citation]
    status: str
    model: str | None
    latency_ms: int


NO_CONTEXT_ANSWER = "Nothing in this video's transcript matches that question."


def _write_log(session: Session, row: QueryLog) -> None:
    """Write the request log row. Never raises.

    The logging table is the one artifact in this project that cannot be
    reconstructed after the fact, so a failed write has to be loud in
    stdout. It must not, however, turn a request that was served correctly
    into an error for the user. Losing a log row is bad; losing the answer
    as well is worse.
    """
    try:
        session.add(row)
        session.commit()
    except Exception as exc:
        session.rollback()
        print(
            f"FAILED TO WRITE query_logs ROW: {exc!r} "
            f":: video={row.video_id} question={row.question!r}"
        )


@app.post("/ask", response_model=AskResponse)
def ask(payload: AskRequest, session: Session = Depends(get_session)):
    """Answer a question about a video, grounded in retrieved transcript.

    EVERY path through this function writes a query_logs row, including the
    ones that fail. That is enforced by the try/finally rather than by
    remembering to log before each return, because "remembering" is how
    logging tables end up with only the successful requests in them --
    which is the opposite of what the evaluation report needs.
    """
    started = time.perf_counter()
    run = chunking_run_label(DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP)

    # Built up as we go, written exactly once in the finally block.
    log = QueryLog(
        question=payload.question,
        chunking_run=run,
        prompt_version=PROMPT_VERSION,
    )

    try:
        try:
            video_id = extract_video_id(payload.video)
        except ValueError as exc:
            log.error = f"bad_video_reference: {exc}"
            raise HTTPException(status_code=400, detail=str(exc))

        log.video_id = video_id

        hits = search(session, video_id, payload.question, run, payload.top_k)
        log.retrieved_chunks = to_log_payload(hits)

        result = generate(payload.question, hits)

        log.answer = result.answer
        log.model = result.model
        log.prompt_tokens = result.prompt_tokens
        log.completion_tokens = result.completion_tokens
        log.estimated_cost_usd = result.estimated_cost_usd
        log.error = result.error

        # Retrieval found nothing. This is NOT an error for the caller: the
        # question was answered truthfully, with "no". The log row still
        # carries error="no_chunks_retrieved" so the distinction survives
        # for analysis, which is exactly the separation between what a user
        # sees and what the evaluation set records.
        if result.error == "no_chunks_retrieved":
            return AskResponse(
                video_id=video_id,
                question=payload.question,
                answer=NO_CONTEXT_ANSWER,
                citations=[],
                status="no_context",
                model=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        # Any other generation error is an upstream failure -- rate limit,
        # bad key, provider outage. 502 rather than 500, because our code
        # did its job and the distinction is what makes the failure-rate
        # column in the report mean anything.
        if result.error or not result.answer:
            raise HTTPException(
                status_code=502,
                detail=f"Generation failed: {(result.error or 'empty')[:120]}",
            )

        # Resolve the model's [n] citations against the chunks we actually
        # sent. by_rank is keyed on rank, which is the number the model saw.
        by_rank = {h.rank: h for h in hits}
        citations = [
            Citation(
                number=h.rank,
                chunk_id=h.chunk_id,
                start_seconds=h.start_seconds,
                end_seconds=h.end_seconds,
                # int() because YouTube's ?t= takes whole seconds. Rounding
                # down lands just before the cited moment rather than just
                # after it, which is the side to err on when the point is
                # to let someone check a claim.
                url=f"https://youtu.be/{video_id}?t={int(h.start_seconds)}",
            )
            for n, h in by_rank.items()
            if f"[{n}]" in result.answer
        ]

        # Citations the model invented. Already validated in generate(); we
        # record it here rather than failing, because an answer with one bad
        # reference among four good ones is still worth returning, and the
        # log row is what lets Phase 04 count how often it happens.
        if result.invalid_citations:
            log.error = f"invalid_citations: {result.invalid_citations}"

        return AskResponse(
            video_id=video_id,
            question=payload.question,
            answer=result.answer,
            citations=citations,
            status="answered",
            model=result.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    finally:
        # Total request time, not just generation. models.py notes that per
        # stage timings wait for tracing in Phase 06; one honest end-to-end
        # number now beats four invented ones.
        log.latency_ms = int((time.perf_counter() - started) * 1000)
        _write_log(session, log)