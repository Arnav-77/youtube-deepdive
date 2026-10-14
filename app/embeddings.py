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
# Free-tier limit from Google's rate-limit page, checked 14 Oct 2026. For bulk
# embedding this is the binding constraint; requests per minute is not.
TOKENS_PER_MINUTE = 30_000
MAX_ATTEMPTS = 5
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


def _embed_batch(texts, task_type):
    """One HTTPS request in, one unit-length vector per text out, in order."""
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
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = httpx.post(_URL, json=body, headers=headers, timeout=60)
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
        if attempt < MAX_ATTEMPTS - 1:
            # Exponential backoff with jitter: 1, 2, 4, 8 s plus up to 1 s of
            # randomness, so retries spread out instead of arriving in step.
            time.sleep(2 ** attempt + random.uniform(0, 1))
    raise EmbeddingError(f"gave up after {MAX_ATTEMPTS} attempts; last: {last}")


def iter_document_embeddings(texts):
    """Yield (start_index, pgvector_texts) one batch at a time, paced under the TPM limit.

    A generator so the caller can save each batch as it arrives: if the daily
    quota runs out halfway through, everything already embedded is kept.
    """
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        t0 = time.monotonic()
        vectors = _embed_batch(batch, "RETRIEVAL_DOCUMENT")
        yield start, [to_pgvector(v) for v in vectors]
        # ~4 characters per token for English. Deliberately an estimate:
        # counting exactly would cost another API call per batch.
        tokens = sum(len(t) for t in batch) / 4
        wait = 60 * tokens / TOKENS_PER_MINUTE - (time.monotonic() - t0)
        if start + BATCH_SIZE < len(texts) and wait > 0:
            time.sleep(wait)


def embed_query(question):
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
        vector = to_pgvector(_embed_batch([question], "QUESTION_ANSWERING")[0])
        session.execute(
            text("INSERT INTO query_embeddings (text_sha256, model, embedding) "
                 "VALUES (:h, :m, CAST(:v AS vector)) ON CONFLICT DO NOTHING"),
            {"h": digest, "m": MODEL_KEY, "v": vector},
        )
        session.commit()
        return vector