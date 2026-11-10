"""Embedding a video in the background, so a request can return before it ends.

Why this exists: 300 chunks take about three minutes to embed (batches of 50,
about 33 s apart under the free tier), and Render cuts a request off at about
100 s. So the paste endpoint stores the chunks, starts this job, and answers
202 straight away.

Why a thread and not FastAPI's BackgroundTasks: the job opens its OWN database
session. The request's session is closed when the response is sent, so the job
must not depend on it.

Restarts: the job's progress is in the database, not in memory. embed_missing_chunks
commits after every batch, so a restart loses at most the batch in flight. The
in-memory set below only stops a DUPLICATE run within one process. After a
restart the set is empty, the status reads "incomplete", and sending the same
request again starts a job that embeds only the missing chunks.

Assumes ONE worker process (Render sets WEB_CONCURRENCY=1 for 0.1 CPU). With two
workers, each would have its own set and could run the same video twice.
"""

import threading

from sqlalchemy import text

from app.db import SessionLocal
from app.embeddings import MODEL_KEY, embed_missing_chunks

_lock = threading.Lock()
_running: set[str] = set()

# One round trip: every chunk of this video and run, and how many have an
# embedding for the current model. count(e.chunk_id) skips the NULLs that the
# LEFT JOIN gives for missing embeddings.
_PROGRESS_SQL = text("""
    SELECT count(*) AS total, count(e.chunk_id) AS embedded
    FROM chunks c
    LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id AND e.model = :model
    WHERE c.video_id = :video_id AND c.chunking_run = :run
""")


def progress(session, video_id: str, chunking_run: str) -> tuple[int, int]:
    """(total chunks, chunks with an embedding). Counts only, never text."""
    row = session.execute(
        _PROGRESS_SQL,
        {"model": MODEL_KEY, "video_id": video_id, "run": chunking_run},
    ).one()
    return int(row.total), int(row.embedded)


def is_running(video_id: str) -> bool:
    with _lock:
        return video_id in _running


def start_embedding(video_id: str, chunking_run: str) -> bool:
    """Start embedding in a daemon thread. Returns False if one is already running.

    Returns at once. Never raises into the caller: a failure inside the job is
    printed (class name only) and the job simply stops, leaving the video
    "incomplete" until the same request is sent again.
    """
    with _lock:
        if video_id in _running:
            return False
        _running.add(video_id)

    try:
        thread = threading.Thread(
            target=_run,
            args=(video_id, chunking_run),
            name=f"embed-{video_id}",
            daemon=True,
        )
        thread.start()
    except Exception as exc:
        with _lock:
            _running.discard(video_id)
        print(f"could not start embedding thread for {video_id}: {type(exc).__name__}")
        return False
    return True


def _run(video_id: str, chunking_run: str) -> None:
    try:
        with SessionLocal() as session:
            done = embed_missing_chunks(session, video_id, chunking_run)
        print(f"background embedding finished for {video_id}: {done} chunks")
    except Exception as exc:
        # Class name only: EmbeddingError text can echo the API's response, and
        # database errors can echo the connection URL.
        print(f"background embedding stopped for {video_id}: {type(exc).__name__}")
    finally:
        with _lock:
            _running.discard(video_id)


def derive_status(video_exists: bool, total: int, embedded: int, running: bool) -> str:
    """One of: not_found, ready, embedding, incomplete.

    Pure, so the rules are testable with no database and no thread.
      - not_found: no such video.
      - ready: every chunk has an embedding (and there is at least one chunk).
      - embedding: some chunks are missing and a job is running right now.
      - incomplete: some chunks are missing and nothing is running. Send the
        same request again to resume.
    """
    if not video_exists:
        return "not_found"
    if total > 0 and embedded == total:
        return "ready"
    if running:
        return "embedding"
    return "incomplete"
