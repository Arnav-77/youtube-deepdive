"""Text embeddings, behind one seam.

Every embedding in the project goes through this module: embed_query() for
questions, iter_document_embeddings() for transcript chunks. Swapping the
provider (another API, or a local model on a bigger machine) means changing
this file and nothing else, which is why it exists.

The provider is Google's Gemini embedding API, called over plain HTTPS rather
than through Google's SDK: nothing new is added to memory against Render's
512 MB ceiling, and every field sent is visible here.

Vectors leave this module as pgvector text, '[0.1,0.2,...]', because Postgres
does all the vector maths. Python never needs numpy.
"""

import hashlib
import math
import os
import random
import time

import httpx
from sqlalchemy import text

from app.db import SessionLocal

MODEL = "gemini-embedding-001"
DIMS = 768
# Stored with every vector. Vectors from different models, or the same model
# at a different size, are not comparable, so they must never be mixed.
MODEL_KEY = f"{MODEL}@{DIMS}"

_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:batchEmbedContents"

BATCH_SIZE = 50
# Free-tier limit from Google's rate-limit page, checked 14 Oct 2026.
TOKENS_PER_MINUTE = 30_000
# The free tier also allows 100 requests per minute, and every text inside a
# batch counts as one request: on 18 Oct three backfill runs in a row managed
# exactly two batches of 50 per minute before HTTP 429. So a batch of 50 uses
# half a minute's allowance, and for bulk embedding THIS is the binding limit
# (an earlier comment here said the opposite, and was wrong).
REQUESTS_PER_MINUTE = 100
# Pace to 90% of each limit, so timing jitter cannot push us over the edge.
SAFETY = 0.9
MAX_ATTEMPTS = 5
REQUEST_TIMEOUT_S = 60.0
_RETRYABLE = {429, 500, 502, 503, 504}
# How many embed_query() calls were served from the cache versus paid for
# with an API request, since this process started. Lets an eval run report
# the quota it actually spent instead of anyone guessing.
CACHE_STATS = {"hits": 0, "misses": 0}


class EmbeddingError(RuntimeError):
    """The embedding API failed in a way that retrying did not fix."""


def _unit(values):
    # gemini-embedding-001 does not normalise vectors below 3072 dimensions
    # (measured 14 Oct: raw norms ~0.58), so scale to length 1 here, once,
    # and no caller ever has to remember to.
    n = math.sqrt(sum(x * x for x in values))
    return [x / n for x in values]


def to_pgvector(values):
    # 7 significant digits: pgvector stores 4-byte floats, so more is noise.
    return "[" + ",".join(f"{x:.7g}" for x in values) + "]"


def _embed_batch(texts, task_type, attempts=MAX_ATTEMPTS, timeout=REQUEST_TIMEOUT_S):
    """One HTTPS request in, one unit-length vector per text out, in order.

    `attempts` and `timeout` exist for the live /ask path, which cannot afford
    the batch script's patience: one try, a short timeout, then the caller
    falls back to word search.
    """
    body = {"requests": [
        {"model": f"models/{MODEL}",
         "content": {"parts": [{"text": t}]},
         "taskType": task_type,
         "outputDimensionality": DIMS}
        for t in texts
    ]}
    # The key goes in a header, never as ?key= in the URL: URLs end up in
    # exception messages and logs, headers do not.
    headers = {"x-goog-api-key": os.environ["GEMINI_API_KEY"]}

    last = "no attempt made"
    for attempt in range(attempts):
        # Exponential backoff with jitter: 1, 2, 4, 8 s plus up to 1 s of
        # randomness, so retries spread out instead of arriving in step.
        wait = 2 ** attempt
        try:
            r = httpx.post(_URL, json=body, headers=headers, timeout=timeout)
        except httpx.TransportError as exc:
            last = type(exc).__name__
        else:
            if r.status_code == 200:
                vectors = [e["values"] for e in r.json()["embeddings"]]
                if len(vectors) != len(texts):
                    raise EmbeddingError(f"sent {len(texts)} texts, got {len(vectors)} vectors")
                return [_unit(v) for v in vectors]
            if r.status_code not in _RETRYABLE:
                # 400, 403 and friends: retrying cannot help, so fail loudly now.
                raise EmbeddingError(f"HTTP {r.status_code}: {r.text[:300]}")
            last = f"HTTP {r.status_code}: {r.text[:200]}"
            if r.status_code == 429:
                # A per-minute quota only clears when the minute has passed.
                # The short backoff above gave up after ~15 s on 18 Oct, three
                # runs in a row; waiting out the whole window does not.
                wait = 60
        if attempt < attempts - 1:
            time.sleep(wait + random.uniform(0, 1))
    raise EmbeddingError(f"gave up after {attempts} attempts; last: {last}")


def iter_document_embeddings(texts):
    """Yield (start_index, pgvector_texts) one batch at a time, paced under both limits.

    A generator so the caller can save each batch as it arrives: if the daily
    quota runs out halfway through, everything already embedded is kept.
    """
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        vectors = _embed_batch(batch, "RETRIEVAL_DOCUMENT")
        yield start, [to_pgvector(v) for v in vectors]
        if start + BATCH_SIZE >= len(texts):
            break
        # Wait long enough for BOTH limits, measured from when the batch
        # succeeded. The old code measured from when it was first tried, so a
        # batch that spent 40 s retrying was followed by the next one almost
        # at once, and that one hit the limit. ~4 characters per token for
        # English: deliberately an estimate, since counting exactly would cost
        # another API call per batch.
        tokens = sum(len(t) for t in batch) / 4
        time.sleep(max(60 * tokens / (TOKENS_PER_MINUTE * SAFETY),
                       60 * len(batch) / (REQUESTS_PER_MINUTE * SAFETY)))


def embed_query(question, attempts=MAX_ATTEMPTS, timeout=REQUEST_TIMEOUT_S):
    """Embed one question as pgvector text, through the query_embeddings cache.

    Same text and same model give the same vector, so evaluation reruns spend
    no quota after the first run. Uses its own session, so it never commits
    the caller's transaction as a side effect.
    """
    digest = hashlib.sha256(question.encode("utf-8")).hexdigest()
    with SessionLocal() as session:
        cached = session.execute(
            text("SELECT embedding::text FROM query_embeddings "
                 "WHERE text_sha256 = :h AND model = :m"),
            {"h": digest, "m": MODEL_KEY},
        ).scalar()
        if cached is not None:
            CACHE_STATS["hits"] += 1
            return cached
        CACHE_STATS["misses"] += 1
        vector = to_pgvector(
            _embed_batch([question], "QUESTION_ANSWERING", attempts, timeout)[0]
        )
        session.execute(
            text("INSERT INTO query_embeddings (text_sha256, model, embedding) "
                 "VALUES (:h, :m, CAST(:v AS vector)) ON CONFLICT DO NOTHING"),
            {"h": digest, "m": MODEL_KEY, "v": vector},
        )
        session.commit()
        return vector


# Chunks of one video and chunking run with no embedding yet for this model.
_MISSING_SQL = text("""
    SELECT c.id, c.text
    FROM chunks c
    LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id AND e.model = :model
    WHERE c.video_id = :video_id
      AND c.chunking_run = :run
      AND e.chunk_id IS NULL
    ORDER BY c.chunk_index
""")

_INSERT_SQL = text("""
    INSERT INTO chunk_embeddings (chunk_id, model, embedding)
    VALUES (:chunk_id, :model, CAST(:embedding AS vector))
    ON CONFLICT DO NOTHING
""")


def embed_missing_chunks(session, video_id, chunking_run):
    """Embed and save every chunk of one video that has no embedding yet.

    Called by POST /videos, so a newly added video is searchable by meaning
    straight away. Returns how many chunks were embedded by this call: 0 when
    the video was already complete.

    Safe to call again after a failure: only missing chunks are sent, and each
    batch is committed as soon as it arrives, so a second call finishes the
    job without paying twice. This commits the caller's session.

    Slow for long videos: a batch of 50 every ~33 s under the free tier, so a
    300-chunk lecture takes about three minutes. Fine from a laptop; on a
    server with a request timeout this belongs in a background job.
    evaluation/embed_chunks.py does the same for many videos at once.
    """
    rows = session.execute(
        _MISSING_SQL, {"video_id": video_id, "run": chunking_run, "model": MODEL_KEY}
    ).all()
    ids = [row.id for row in rows]
    done = 0
    for start, vectors in iter_document_embeddings([row.text for row in rows]):
        session.execute(_INSERT_SQL, [
            {"chunk_id": ids[start + i], "model": MODEL_KEY, "embedding": v}
            for i, v in enumerate(vectors)
        ])
        session.commit()
        done += len(vectors)
    return done
