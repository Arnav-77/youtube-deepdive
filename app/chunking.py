"""Splitting transcripts into retrievable chunks.

PHASE 02 STRATEGY: FIXED-SIZE, DELIBERATELY CRUDE
=================================================
This splits every ~1000 characters with ~200 characters of overlap. It is
not a good chunking strategy and it is not meant to be. It is the CONTROL
GROUP.

Phase 03 implements topic-shift segmentation -- splitting where the subject
changes rather than at arbitrary character counts. The proposal asserts
that is better. An assertion is not a result. To turn it into one you need
a baseline measured on the same videos with the same questions, and this
file is that baseline.

That is also why the parameters are recorded in the chunking_run label and
not just in this source file: the comparison is only valid if you can say
exactly what the baseline was, months later, from the database alone.

WHY 1000 / 200
--------------
1000 characters is roughly 250 tokens, so five retrieved chunks costs about
1250 tokens of context -- comfortable against free-tier limits while still
giving the model enough surrounding text that a sentence is not stranded
without context. 200 characters of overlap means a sentence spanning a
boundary appears complete in at least one chunk. Both are conventional
starting points, not optimal ones. Optimal is what Phase 03 argues about.

THE TIMESTAMP PROBLEM
---------------------
YouTube caption snippets OVERLAP. Observed on this project's first video:

    start=  0.00  duration=4.84   ends at 4.84
    start=  2.24  duration=6.44   ends at 8.68
    start=  4.84  duration=6.48   ends at 11.32

Snippet 1 "ends" at 4.84, which is exactly when snippet 3 STARTS. That is
YouTube's rolling two-line caption display: each line stays on screen while
the next appears below it, so `duration` measures how long the text is
VISIBLE, not how long it is SPOKEN.

Consequence: `start + duration` is NOT when the speech ends. Using it would
put every citation roughly 4 seconds late -- click a citation, land after
the thing it cites. Silent, plausible-looking, and wrong.

The fix: a snippet's true end is the START of the next snippet. Only the
final snippet falls back to start + duration, because there is no next one.
"""

from sqlalchemy.orm import Session

from app.models import Chunk, Video

# Defaults. Changing these means a DIFFERENT chunking run -- see
# chunking_run_label() below.
DEFAULT_CHUNK_SIZE = 1000
DEFAULT_OVERLAP = 200


def chunking_run_label(chunk_size: int, overlap: int) -> str:
    """Build the label recorded on every chunk row, e.g. 'fixed_1000_200'.

    This string is what makes two chunkings of the same video coexist
    instead of one destroying the other. Phase 03 writes rows labelled
    'topicshift_v1' alongside these, and the comparison becomes a WHERE
    clause rather than an archaeology project.

    The parameters are IN the label on purpose. 'fixed' alone would be
    ambiguous the moment you try 1500/300.
    """
    return f"fixed_{chunk_size}_{overlap}"


def _snippet_end(snippets: list[dict], i: int) -> float:
    """True end time of snippet i -- see THE TIMESTAMP PROBLEM above.

    The next snippet's start, except for the last snippet where there is
    no next one and we fall back to start + duration.
    """
    if i + 1 < len(snippets):
        return float(snippets[i + 1]["start"])
    return float(snippets[i]["start"] + snippets[i]["duration"])


def chunk_transcript(
    snippets: list[dict],
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[dict]:
    """Split raw snippets into chunks of roughly chunk_size characters.

    Returns dicts with text, start_seconds, end_seconds -- not Chunk model
    objects. Keeping this function free of database concerns means it can
    be tested with a hand-written list of snippets and no database at all,
    which matters when Phase 03 starts comparing strategies.

    DECISION: chunks break on SNIPPET boundaries, never mid-snippet.
    Snippets average ~33 characters here, so a 1000-character target is hit
    within about 3% -- close enough that exactness buys nothing. The
    alternative, splitting mid-snippet, means a chunk's start time is
    somewhere inside a snippet's span and we would have to interpolate it.
    Interpolated timestamps are guesses, and this project cites timestamps
    to users. A chunk that is 970 or 1020 characters is fine; a citation
    pointing 2 seconds off is not.
    """
    if not snippets:
        return []

    chunks: list[dict] = []

    # Index of the snippet the current chunk starts at.
    start_i = 0

    while start_i < len(snippets):
        parts: list[str] = []
        char_count = 0
        i = start_i

        # Accumulate snippets until we reach the target size. The condition
        # is checked BEFORE appending so a chunk never ends up wildly over
        # target, but we always take at least one snippet -- otherwise a
        # single snippet longer than chunk_size would loop forever.
        while i < len(snippets):
            text = snippets[i]["text"]
            if char_count + len(text) > chunk_size and parts:
                break
            parts.append(text)
            char_count += len(text) + 1  # +1 for the space we join with
            i += 1

        end_i = i - 1  # last snippet actually included

        chunks.append(
            {
                "text": " ".join(parts),
                "start_seconds": float(snippets[start_i]["start"]),
                "end_seconds": _snippet_end(snippets, end_i),
            }
        )

        # If that chunk consumed the rest of the transcript, stop. Checked
        # here rather than relying on the while condition because the
        # overlap step below could otherwise walk backwards forever.
        if i >= len(snippets):
            break

        # OVERLAP: step back far enough that roughly `overlap` characters
        # of the chunk we just emitted are repeated at the start of the
        # next one. Walk backwards from the end, accumulating lengths,
        # until we have covered the overlap budget.
        back_chars = 0
        back_i = end_i
        while back_i > start_i and back_chars < overlap:
            back_chars += len(snippets[back_i]["text"]) + 1
            back_i -= 1

        # Guard against no forward progress. If the overlap calculation
        # lands us at or before where we started, the loop would repeat the
        # same chunk forever. Force at least one snippet of progress.
        start_i = max(back_i + 1, start_i + 1)

    return chunks


def store_chunks(
    session: Session,
    video: Video,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Chunk a stored video's transcript and write the rows.

    APPEND ONLY. This function never updates or deletes. If chunks already
    exist for this video under this exact run label, it returns them
    untouched rather than rewriting -- because every chunk id already
    recorded in query_logs.retrieved_chunks must keep resolving to the same
    text forever. Overwriting a chunk does not lose a table, it silently
    corrupts the evaluation set that Phase 04 mines for failure cases.

    Re-chunking with different parameters produces a different run label
    and writes a NEW set of rows alongside these.
    """
    run = chunking_run_label(chunk_size, overlap)

    existing = (
        session.query(Chunk)
        .filter(Chunk.video_id == video.video_id, Chunk.chunking_run == run)
        .order_by(Chunk.chunk_index)
        .all()
    )
    if existing:
        return existing

    rows = [
        Chunk(
            video_id=video.video_id,
            chunking_run=run,
            chunk_index=idx,
            text=c["text"],
            start_seconds=c["start_seconds"],
            end_seconds=c["end_seconds"],
        )
        for idx, c in enumerate(chunk_transcript(video.raw_transcript, chunk_size, overlap))
    ]

    session.add_all(rows)
    session.commit()
    return rows