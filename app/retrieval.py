"""Retrieval over stored chunks.

PHASE 02 STRATEGY: POSTGRES FULL-TEXT SEARCH, LEXICAL ONLY
==========================================================
No embeddings, no vector store, no reranking. This is the retrieval
equivalent of the fixed-size chunker: a deliberate baseline, recorded and
reproducible, that Phase 03 has to beat.

WHY POSTGRES FULL-TEXT SEARCH RATHER THAN SOMETHING CRUDER
-----------------------------------------------------------
Scoring by naive word overlap in Python would return near-noise, and noise
is expensive here: it makes a retrieval failure indistinguishable from a
generation failure, which is exactly the distinction Phase 03's gate asks
us to make. Postgres full-text search is real information retrieval --
it stems words, drops stopwords, and ranks by term frequency -- while
adding no dependency and no infrastructure, because the chunks are already
in Postgres.

WHY IT IS STILL ONLY A BASELINE
--------------------------------
It is lexical. A question about "getting started" will not match a chunk
that says "beginning" unless the stemmer happens to bridge them. There is
no notion of semantic similarity, which is the entire reason Phase 03 adds
embeddings.

WHY chunking_run IS A REQUIRED ARGUMENT
----------------------------------------
Chunks are append-only, so the same video will soon have two sets of chunks
in the table: fixed_1000_200 from Phase 02 and topicshift_v1 from Phase 03.
Retrieval that did not specify which set to search would silently mix them,
and the comparison the whole project rests on would be meaningless.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

DEFAULT_TOP_K = 5


@dataclass
class Retrieved:
    """One retrieved chunk, with everything the caller needs downstream.

    A dataclass rather than a raw tuple or dict so that the generation step
    and the logging step both read fields by name. A tuple index is a
    silent bug waiting for the day the query gains a column.
    """

    chunk_id: int
    text: str
    start_seconds: float
    end_seconds: float
    score: float
    rank: int


# The query is built once here rather than inline at the call site so the
# SQL is readable and reviewable as SQL.
#
# to_tsvector('english', text)
#     Converts chunk text into Postgres's searchable form: lowercased,
#     stopwords removed, words stemmed ("running" and "runs" both become
#     "run").
#
# plainto_tsquery('english', :q)
#     Converts the user's plain question into a query, doing the same
#     stemming and stopword removal. It joins the surviving terms with AND.
#
# replace(...::text, '&', '|')::tsquery
#     AND is wrong for a question. "What is the main point about habits"
#     would demand every one of those words in a single chunk and usually
#     return nothing. We take Postgres's own parsing -- which correctly
#     handles punctuation, stemming and stopwords, and which we do NOT want
#     to reimplement -- and swap the conjunction for a disjunction. Chunks
#     matching more terms still rank higher, because that is ts_rank's job.
#
# @@
#     The match operator: does this document satisfy this query.
#
# ts_rank
#     Relevance score, based on how often the query terms appear and where.
#     It is a bounded float, not a probability, and the absolute value means
#     nothing on its own -- only the ordering does. Worth remembering before
#     anyone is tempted to threshold on it.
_SEARCH_SQL = text("""
    SELECT
        id,
        text,
        start_seconds,
        end_seconds,
        ts_rank(
            to_tsvector('english', text),
            replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery
        ) AS score
    FROM chunks
    WHERE video_id = :video_id
      AND chunking_run = :run
      AND to_tsvector('english', text) @@
          replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery
    ORDER BY score DESC, chunk_index ASC
    LIMIT :k
""")


def search(
    session: Session,
    video_id: str,
    query: str,
    chunking_run: str,
    top_k: int = DEFAULT_TOP_K,
) -> list[Retrieved]:
    """Return the top_k chunks matching the query, best first.

    Returns an EMPTY LIST when nothing matches, rather than raising. That is
    a real and expected outcome -- a question whose words appear nowhere in
    the transcript -- and the caller must log it as a retrieval failure with
    no chunks rather than treat it as an error. Those rows are precisely
    what Phase 04 mines for the golden set.

    The tsvector is computed per query rather than stored in an indexed
    column. A production system would add that column and a GIN index, at
    the cost of another migration. At 20 chunks, or 400 across the whole
    benchmark set, a sequential scan costs nothing measurable and the
    network round trip dominates it completely. Store it when a measurement
    says to, not before.
    """
    rows = session.execute(
        _SEARCH_SQL,
        {"q": query, "video_id": video_id, "run": chunking_run, "k": top_k},
    ).all()

    return [
        Retrieved(
            chunk_id=row.id,
            text=row.text,
            start_seconds=float(row.start_seconds),
            end_seconds=float(row.end_seconds),
            score=float(row.score),
            rank=i + 1,
        )
        for i, row in enumerate(rows)
    ]


def to_log_payload(results: list[Retrieved]) -> list[dict]:
    """Shape the results for the query_logs.retrieved_chunks JSON column.

    IDs and scores only, never the chunk text. The text is already stored
    once in the chunks table, which is append-only, so the id stays
    resolvable forever. Copying the text into every log row would duplicate
    the same string on every retrieval and grow the table with every
    evaluation run, against Neon's 0.5 GB.
    """
    return [
        {"chunk_id": r.chunk_id, "score": round(r.score, 6), "rank": r.rank}
        for r in results
    ]