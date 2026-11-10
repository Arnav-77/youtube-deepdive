"""DeepDive API — Phase 02 skeleton."""

import hmac
import json
import os
import threading

from fastapi import Depends, FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel , Field
from app.chunking import store_chunks
from app.youtube import ingest_video
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
import time

from app.chunking import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunk_transcript, chunking_run_label
from app.limits import load_daily_cap, load_hidden_ids, public_paste_refusal, visitor_chunks_last_24h
from app.generation import PROMPT_VERSION, cited_numbers, generate
from app.embeddings import EmbeddingError, embed_missing_chunks
from app.jobs import derive_status, is_running, progress, start_embedding
from app.models import QueryLog, Video
from app.paste import MAX_PASTE_BYTES, PasteError, PasteTooLarge, ParsedPaste, parse_paste, read_limited
from app.retrieval import (
    DEFAULT_TOP_K,
    MissingEmbeddings,
    dense_search,
    search,
    to_log_payload,
)
from app.youtube import extract_video_id

from app.db import get_session

SERVICE_NAME = "deepdive-api"

# Render injects RENDER_GIT_COMMIT with the SHA of the commit it built.
# Not set locally, so fall back to something recognisable.
GIT_COMMIT = os.getenv("RENDER_GIT_COMMIT", "local")

# Which search /ask uses: "dense" (meaning search, measured better on 14 Oct:
# Recall@5 14/14 vs 12/14 on the reported set) or "lexical" (word search, the
# Phase 02 baseline). A setting rather than code, so it can be switched back
# from Render's dashboard without a commit. Checked at startup: a typo here
# should stop the app, not surface on the first real question (lesson of the
# "emini/" fallback typo, 14 Oct).
RETRIEVAL_METHOD = os.getenv("RETRIEVAL_METHOD", "dense").strip().lower()
if RETRIEVAL_METHOD not in {"dense", "lexical"}:
    raise RuntimeError(
        f"RETRIEVAL_METHOD must be 'dense' or 'lexical', got {RETRIEVAL_METHOD!r}"
    )

# Pasting is public. PASTE_TOKEN is an ADMIN override: a request that sends the
# right X-Paste-Token skips the public limits (see app/limits.py). Without a
# token configured, everyone is a visitor and the limits apply. The token value
# is never printed.
PASTE_TOKEN = os.getenv("PASTE_TOKEN", "").strip()
# Daily cap on chunks stored for visitor pastes. Bad value stops the app.
PUBLIC_DAILY_CHUNKS = load_daily_cap(os.getenv("PUBLIC_PASTE_DAILY_CHUNKS"))
# Videos the owner hides without deleting: removed from listings, /ask returns
# 404 for them. Bad value stops the app. Chunks are never changed.
HIDDEN_IDS = load_hidden_ids(os.getenv("HIDDEN_VIDEO_IDS"))
if PASTE_TOKEN:
    print("paste: public, with an admin token that skips the public limits")
else:
    print(f"paste: public, limits apply (no admin token set; {PUBLIC_DAILY_CHUNKS} chunks/day)")
print(f"hidden videos: {len(HIDDEN_IDS)}")

# On the request path the question's embedding gets one try with a short
# timeout. The batch script can afford five patient retries; a person waiting
# on /ask cannot, and word search is a working answer in the meantime.
LIVE_EMBED_ATTEMPTS = 1
LIVE_EMBED_TIMEOUT_S = 10.0

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
    # Chunks embedded by THIS call; 0 on a repeat call for a complete video.
    embedded_count: int


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

    # Make the chunks searchable by meaning. Without this, a video added after
    # the switch to dense retrieval would have chunks but no embeddings, and
    # every question about it would fall back to word search.
    run = chunking_run_label(DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP)
    try:
        embedded = embed_missing_chunks(session, video.video_id, run)
    except EmbeddingError as exc:
        # Transcript and chunks are already saved, and embedding only sends
        # what is missing, so repeating the same request finishes the job.
        print(f"embedding failed for {video.video_id}: {exc!r}")
        raise HTTPException(
            status_code=502,
            detail=f"Transcript saved, but embedding failed ({type(exc).__name__}). "
                   "Send the same request again to finish.",
        )

    return IngestResponse(
        video_id=video.video_id,
        source=video.source,
        duration_seconds=video.duration_seconds,
        snippet_count=len(video.raw_transcript),
        chunk_count=len(chunks),
        chunking_run=chunks[0].chunking_run if chunks else "",
        embedded_count=embedded,
    )


# --- pasted transcripts ------------------------------------------------------
#
# A person copies the transcript from YouTube's "Show transcript" panel and
# pastes it here, because YouTube blocks the server from fetching captions.
# Pasting is public. Visitors get the limits in app/limits.py; a request with
# the admin token skips them. See docs/decisions.md (10 Nov 2026).

# JSON escaping doubles some characters (quotes, newlines), so the raw request
# body can be a little larger than the transcript limit. The transcript itself
# is checked again by parse_paste.
PASTE_BODY_LIMIT = 2 * MAX_PASTE_BYTES + 4096
PASTE_RESUME_MESSAGE = (
    "Embedding runs in the background. Check progress at progress_url. "
    "If the server restarts, send this same request again: it embeds only "
    "the missing chunks."
)
# One paste at a time. The check against the daily cap and the write that
# uses it must be atomic, or two visitors could both pass the same headroom.
# A single worker process makes an in-memory lock enough.
_paste_lock = threading.Lock()


def _added_by_label(source: str, added_by: str | None) -> str:
    """'captions', 'admin' or 'visitor'. Unknown or missing counts as visitor."""
    if source == "captions":
        return "captions"
    if added_by == "admin":
        return "admin"
    return "visitor"


def _store_paste(
    session: Session, video_id: str, transcript: str, parsed: ParsedPaste, admin: bool
) -> tuple[int, dict]:
    """Store a pasted video, or resume one. Returns (status code, body).

    Runs in a worker thread (it does blocking database work), so the event
    loop is not held while it runs.
    """
    run = chunking_run_label(DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP)

    if video_id in HIDDEN_IDS:
        return 409, {
            "video_id": video_id,
            "status": "conflict",
            "message": "this video has been hidden and cannot be added again",
        }

    with _paste_lock:
        video = session.get(Video, video_id)
        if video is None:
            # Visitors: refuse before anything is written if the paste is too
            # big, or would take the daily total over the cap. Admin skips both.
            if not admin:
                chunk_count = len(chunk_transcript(parsed.snippets))
                used = visitor_chunks_last_24h(session)
                refusal = public_paste_refusal(chunk_count, used, PUBLIC_DAILY_CHUNKS)
                if refusal is not None:
                    code, message = refusal
                    return code, {"video_id": video_id, "status": "refused", "message": message}

            last = parsed.snippets[-1]
            video = Video(
                video_id=video_id,
                title=None,
                source="pasted",
                added_by="admin" if admin else "visitor",
                raw_transcript=parsed.snippets,
                raw_paste=transcript,
                duration_seconds=int(last["start"] + last["duration"]),
            )
            session.add(video)
            try:
                session.commit()
            except IntegrityError:
                # Another request added the same video between our read and
                # our write. Cannot happen under the lock today; kept as a guard.
                session.rollback()
                return 409, {
                    "video_id": video_id,
                    "status": "conflict",
                    "message": "this video is being added by another request; try again shortly",
                }
        elif video.raw_paste != transcript:
            # Covers a captions video too: its raw_paste is NULL. Never ignore
            # pasted text silently.
            return 409, {
                "video_id": video_id,
                "status": "conflict",
                "message": "a different transcript is already stored for this video",
            }

        # Chunks are append-only and store_chunks returns existing rows untouched,
        # so resending the same paste never re-chunks, and resuming never counts
        # against the daily cap again.
        store_chunks(session, video)

        total, embedded = progress(session, video_id, run)
        if not (total > 0 and embedded == total):
            start_embedding(video_id, run)
        status = derive_status(True, total, embedded, is_running(video_id))

    body = {
        "video_id": video_id,
        "status": status,
        "source": video.source,
        "added_by": _added_by_label(video.source, video.added_by),
        "chunk_count": total,
        "embedded_count": embedded,
        "progress_url": f"/videos/{video_id}",
        "skipped_chapter_lines": parsed.skipped_chapter_lines,
    }
    if status == "ready":
        body["message"] = "Every chunk is embedded. Questions use meaning search."
        return 200, body
    body["message"] = PASTE_RESUME_MESSAGE
    return 202, body


@app.post("/videos/paste")
async def paste_video(request: Request, session: Session = Depends(get_session)):
    """Add a video by pasting its transcript from YouTube's panel.

    Body: {"url": "...", "transcript": "..."}. Optional header: X-Paste-Token.

    Who is calling:
      No header, or no PASTE_TOKEN configured: a visitor. The limits apply.
      The right X-Paste-Token: the admin. The limits are skipped.
      A wrong token: 401. An empty header counts as no header.

    Order of checks, cheapest first:
      401 for a wrong token (nothing is read).
      422 for a body over the size limit, not JSON, or a transcript that breaks
          a rule. 400 for an unreadable URL.
      422 (visitor) if the transcript makes more than 150 chunks.
      429 (visitor) if the paste would take the daily total over the cap.
    The token is compared in constant time and is never echoed back.
    """
    provided = request.headers.get("x-paste-token", "")
    admin = False
    if PASTE_TOKEN and provided:
        if not hmac.compare_digest(provided.encode("utf-8"), PASTE_TOKEN.encode("utf-8")):
            raise HTTPException(status_code=401, detail="wrong X-Paste-Token header")
        admin = True

    try:
        raw = await read_limited(request.stream(), PASTE_BODY_LIMIT)
    except PasteTooLarge as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        body = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail="body must be JSON with url and transcript")
    if (
        not isinstance(body, dict)
        or not isinstance(body.get("url"), str)
        or not isinstance(body.get("transcript"), str)
        or len(body["url"]) > 200
    ):
        raise HTTPException(status_code=422, detail="body must be JSON with url and transcript")

    try:
        video_id = extract_video_id(body["url"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        parsed = parse_paste(body["transcript"])
    except PasteError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    code, payload = await run_in_threadpool(
        _store_paste, session, video_id, body["transcript"], parsed, admin
    )
    return JSONResponse(payload, status_code=code)


@app.get("/videos")
def list_videos(session: Session = Depends(get_session)):
    """Added videos, newest first, for the page. Hidden ids are left out.

    `checked` is false for visitor pastes, so the page can label them "not
    checked". Returns ids and labels only, never transcript text. Capped at
    200 videos.
    """
    rows = session.execute(
        text("SELECT video_id, source, added_by, fetched_at FROM videos "
             "ORDER BY fetched_at DESC LIMIT :n"),
        {"n": 200 + len(HIDDEN_IDS)},
    ).all()
    videos = []
    for row in rows:
        if row.video_id in HIDDEN_IDS:
            continue
        added_by = _added_by_label(row.source, row.added_by)
        videos.append({
            "video_id": row.video_id,
            "source": row.source,
            "added_by": added_by,
            "checked": added_by != "visitor",
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        })
        if len(videos) == 200:
            break
    return {"videos": videos}


@app.get("/videos/{video_id}")
def video_progress(video_id: str, session: Session = Depends(get_session)):
    """How far embedding has got for one video. Counts only: no transcript text.

    Public on purpose: it reveals nothing a person could not learn by asking
    a question, and it is what a person checks after pasting. A hidden video
    answers as not found.
    """
    try:
        vid = extract_video_id(video_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    run = chunking_run_label(DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP)
    # Select only the source column: loading the row would drag the whole
    # stored transcript out of the database on every progress check.
    source = None
    if vid not in HIDDEN_IDS:
        source = session.execute(
            text("SELECT source FROM videos WHERE video_id = :v"), {"v": vid}
        ).scalar()
    if source is None:
        return JSONResponse(
            {"video_id": vid, "status": "not_found"}, status_code=404
        )

    total, embedded = progress(session, vid, run)
    return {
        "video_id": vid,
        "status": derive_status(True, total, embedded, is_running(vid)),
        "source": source,
        "chunk_count": total,
        "embedded_count": embedded,
    }


class AskRequest(BaseModel):
    """A question about one video.

    `video` accepts a URL in any of the forms extract_video_id handles, or a
    bare id. Forgiving on input because the caller is a browser and people
    paste whatever they copied.
    """
    # Bounded because this endpoint is public: an unbounded top_k lets one
    # request put every chunk of a long video into a single prompt and spend
    # the free-tier quota, and a negative one crashes the SQL LIMIT. Pydantic
    # rejects out-of-range values with a 422 before the handler runs, so
    # those requests are deliberately not logged: they were never servable.
    video: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=500)
    top_k: int = Field(DEFAULT_TOP_K, ge=1, le=10)


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

# Does this video have chunks under the run /ask searches? EXISTS stops at the
# first match, so this is one cheap round trip, not a count.
_HAS_CHUNKS = text("""
    SELECT EXISTS (
        SELECT 1 FROM chunks WHERE video_id = :video_id AND chunking_run = :run
    )
""")


def _write_log(session: Session, row: QueryLog) -> None:
    """Write the request log row. Never raises.

    The logging table is the one artifact in this project that cannot be
    reconstructed after the fact, so a failed write has to be loud in
    stdout. It must not, however, turn a request that was served correctly
    into an error for the user. Losing a log row is bad; losing the answer
    as well is worse.
    """
    try:
        # A failed query earlier in this request leaves the session refusing
        # all work until rolled back, which would lose exactly the rows about
        # database failures. Safe only because /ask writes nothing else, so
        # there is nothing pending for the rollback to discard.
        session.rollback()
        session.add(row)
        session.commit()
        
    except Exception as exc:
        session.rollback()
        print(
            f"FAILED TO WRITE query_logs ROW: {exc!r} "
            f":: video={row.video_id} question={row.question!r}"
        )


def _retrieve(session: Session, video_id: str, question: str, run: str, top_k: int):
    """Run the configured search. Returns (hits, label for query_logs).

    If dense is configured but cannot run for this request -- chunks with no
    embedding, or the embedding API failing -- fall back to word search rather
    than fail, and say so in the label. A fallback is invisible to the user;
    the label is what keeps it visible in the log.
    """
    if RETRIEVAL_METHOD == "lexical":
        return search(session, video_id, question, run, top_k), "lexical"
    try:
        hits = dense_search(
            session, video_id, question, run, top_k,
            embed_attempts=LIVE_EMBED_ATTEMPTS,
            embed_timeout=LIVE_EMBED_TIMEOUT_S,
        )
        return hits, "dense"
    except (MissingEmbeddings, EmbeddingError) as exc:
        print(f"dense retrieval unavailable, using lexical: {exc!r}")
        hits = search(session, video_id, question, run, top_k)
        return hits, f"lexical_fallback:{type(exc).__name__}"


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
        commit_sha=GIT_COMMIT,
    )

    try:
        try:
            video_id = extract_video_id(payload.video)
        except ValueError as exc:
            log.error = f"bad_video_reference: {exc}"
            raise HTTPException(status_code=400, detail=str(exc))

        # A hidden video answers exactly like one that was never added, so a
        # hidden video cannot be told apart from absent from outside. The log
        # keeps the real reason. No chunk is touched: hiding is a setting.
        if video_id in HIDDEN_IDS:
            log.error = f"video_hidden: {video_id}"
            raise HTTPException(
                status_code=404,
                detail=f"Video {video_id} has not been added yet. "
                       "Add it with POST /videos first.",
            )

        # A video that was never added used to reach retrieval, find nothing,
        # and get "nothing in this video's transcript matches", which blames a
        # transcript we never had. Worse, the log row then failed to save:
        # query_logs.video_id is a foreign key to videos, and this video is not
        # in videos. So check first, answer 404, and record the requested id in
        # `error` with video_id left NULL, so the row saves.
        if not session.execute(_HAS_CHUNKS, {"video_id": video_id, "run": run}).scalar():
            log.error = f"video_not_ingested: {video_id}"
            raise HTTPException(
                status_code=404,
                detail=f"Video {video_id} has not been added yet. "
                       "Add it with POST /videos first.",
            )

        log.video_id = video_id

        hits, log.retrieval_method = _retrieve(
            session, video_id, payload.question, run, payload.top_k
        )
        log.retrieved_chunks = to_log_payload(hits)

        result = generate(payload.question, hits)

        log.answer = result.answer
        log.model = result.model
        log.model_requested = result.model_requested
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
        cited = cited_numbers(result.answer)
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
            if n in cited
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
    except HTTPException:
        # Our own deliberate 400/502. log.error was already set on that path.
        raise
    except Exception as exc:
        # Anything we did not anticipate: a database error inside search(),
        # or a plain bug. Without this the row is written with error=NULL and
        # answer=NULL, which reads as neither failed nor answered. Class name
        # only: SQLAlchemy messages can embed the connection URL, and the
        # full traceback already goes to stdout for Render's logs.
        if log.error is None:
            log.error = f"unhandled: {type(exc).__name__}"
        raise

    finally:
        # Total request time, not just generation. models.py notes that per
        # stage timings wait for tracing in Phase 06; one honest end-to-end
        # number now beats four invented ones.
        log.latency_ms = int((time.perf_counter() - started) * 1000)
        _write_log(session, log)