"""Database tables.

Two tables, and the relationship between them is the whole design:

  chunks       -- pieces of transcript, APPEND ONLY. Never UPDATE, never
                  DELETE. Re-chunking a video writes a NEW set of rows
                  alongside the old ones, tagged with a different
                  chunking_run.
  query_logs   -- one row per request served, referencing chunk ids.

Why append-only: Phase 03 re-chunks the same videos with topic-shift
segmentation to compare against the Phase 02 fixed-size baseline. If
re-chunking overwrote rows, every chunk_id in the Phase 02 logs would point
at different text -- and Phase 04 mines those logs for real failure cases.
Overwriting a chunk silently corrupts the evaluation set.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Video(Base):
    """One YouTube video and its raw transcript.

    Stored so we never fetch the same video twice -- the 20 benchmark videos
    get queried hundreds of times during evaluation runs, and the transcript
    API is rate limited.
    """

    __tablename__ = "videos"

    # YouTube's own 11-character video id, used as the primary key rather
    # than an autoincrementing integer. It's already unique and stable, and
    # it means we can check "do we have this video" without a lookup table.
    video_id: Mapped[str] = mapped_column(String(20), primary_key=True)

    title: Mapped[str | None] = mapped_column(Text)

    # "captions" or "whisper" -- which path produced this transcript.
    # Needed because Whisper output is noisier, and if quality differs
    # between the two we need to be able to separate them in the results.
    source: Mapped[str] = mapped_column(String(20))

    # Full transcript with timestamps, as returned by the API. Kept raw so
    # we can re-chunk from it later without re-fetching.
    raw_transcript: Mapped[dict] = mapped_column(JSON)

    duration_seconds: Mapped[int | None] = mapped_column(Integer)

    # server_default=func.now() means Postgres fills this in, not Python.
    # One less thing that can be wrong if a clock drifts.
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Chunk(Base):
    """A piece of a transcript. APPEND ONLY -- see module docstring."""

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    video_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("videos.video_id"), index=True
    )

    # Identifies which chunking run produced this row, e.g.
    # "fixed_1000_200" or "topicshift_v1". This is what makes two chunkings
    # of the same video coexist instead of one destroying the other.
    chunking_run: Mapped[str] = mapped_column(String(64), index=True)

    # Position within this video FOR THIS RUN. Not globally unique.
    chunk_index: Mapped[int] = mapped_column(Integer)

    text: Mapped[str] = mapped_column(Text)

    # Timestamps are retrieval metadata, not display decoration. The
    # citation feature is only as reliable as these two columns.
    start_seconds: Mapped[float] = mapped_column(Float)
    end_seconds: Mapped[float] = mapped_column(Float)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Composite index: the common query is "give me chunks for video X from
    # run Y, in order". A single index over all three columns serves that
    # far better than three separate indexes.
    __table_args__ = (
        Index("ix_chunks_video_run_idx", "video_id", "chunking_run", "chunk_index"),
    )


class QueryLog(Base):
    """One row per request served. The evaluation report is built from this.

    This table cannot be reconstructed retroactively. Every field here is
    something that is either impossible or painful to recover later.
    """

    __tablename__ = "query_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    video_id: Mapped[str | None] = mapped_column(
        String(20), ForeignKey("videos.video_id"), index=True
    )

    # The user's question, verbatim. Not reconstructable.
    question: Mapped[str] = mapped_column(Text)

    # The answer we returned, verbatim. Also not reconstructable.
    answer: Mapped[str | None] = mapped_column(Text)

    # Which chunks were retrieved, in rank order, with their scores:
    #   [{"chunk_id": 412, "score": 0.83, "rank": 1}, ...]
    # IDs not text -- chunks are append-only, so these stay resolvable
    # forever, and one chunk is stored once rather than once per retrieval.
    retrieved_chunks: Mapped[list | None] = mapped_column(JSON)

    # Which chunking run was in play. Denormalised from the chunk rows on
    # purpose: it makes "compare fixed vs topic-shift" a WHERE clause
    # instead of a join.
    chunking_run: Mapped[str | None] = mapped_column(String(64), index=True)

    # Which prompt file version produced this answer. In week 12 the report
    # compares prompt iterations, and this column IS that analysis.
    prompt_version: Mapped[str | None] = mapped_column(String(32), index=True)

    # Which model actually answered -- matters because of the Gemini/Groq
    # fallback. Without this, a latency spike caused by failover is
    # indistinguishable from one caused by your code.
    model: Mapped[str | None] = mapped_column(String(64), index=True)
    
    # What we asked LiteLLM for, before any fallback could fire. NULL when no
    # model was called at all (retrieval returned nothing): a log column
    # records what happened, not what would have happened. Fallback fired =
    # model IS NOT NULL AND model <> model_requested.
    model_requested: Mapped[str | None] = mapped_column(String(64))

    # Which deployment wrote this row: the git SHA Render built from, or
    # "local" on a laptop. Laptop and production write to the same Neon
    # table, so without this, local test traffic is indistinguishable from
    # real traffic. NULL on every row written before this column existed.
    commit_sha: Mapped[str | None] = mapped_column(String(40))

    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float)

    # Total wall clock for the request. Separate stage timings come in
    # Phase 06 with tracing; one number is enough for now.
    latency_ms: Mapped[int | None] = mapped_column(Integer)

    # Grounding score from the critic loop, and whether it passed. Nullable
    # because the critic doesn't exist until Phase 04 -- these stay NULL
    # until then, and that's fine.
    grounding_score: Mapped[float | None] = mapped_column(Float)

    # Populated when the request failed. A row with an error and a null
    # answer is a failure case -- exactly what Phase 04 mines for the golden set
    error: Mapped[str | None] = mapped_column(Text)