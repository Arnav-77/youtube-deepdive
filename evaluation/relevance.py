"""The relevance rule shared by the retrieval eval and the grounding scorer.

Pure: no database, no network. Kept in its own module so the grounding scorer
can use the same rule without importing run_eval, which needs a database
configured at import time.
"""


def overlaps(chunk_start, chunk_end, spans):
    """True when the chunk's time range shares some time with any gold span.

    Two ranges overlap when each starts before the other ends. Strictly
    before: a chunk ending at exactly the second a span starts only touches
    it, contains none of the answer, and does not count. Written this
    way rather than by enumerating cases because the four-case version is
    where off-by-one bugs live.
    """
    return any(chunk_start < end and start < chunk_end for start, end in spans)
