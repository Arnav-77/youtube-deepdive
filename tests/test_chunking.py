"""Tests for chunk_transcript and _snippet_end in app/chunking.py."""

import copy

import pytest

from app.chunking import _snippet_end, chunk_transcript

# The three real snippets quoted in chunking.py's docstring. Each one stays
# on screen while the next appears, so start + duration overshoots.
OVERLAPPING = [
    {"text": "first", "start": 0.00, "duration": 4.84},
    {"text": "second", "start": 2.24, "duration": 6.44},
    {"text": "third", "start": 4.84, "duration": 6.48},
]


def snippets(texts, step=1.0, duration=3.0):
    """Hand-made transcript: one snippet every `step` seconds."""
    return [{"text": t, "start": i * step, "duration": duration} for i, t in enumerate(texts)]


# --- _snippet_end -----------------------------------------------------------

def test_snippet_end_uses_next_start_not_start_plus_duration():
    # 2.24, not 4.84. Using 4.84 would put citations about 4 s late.
    assert _snippet_end(OVERLAPPING, 0) == pytest.approx(2.24)


def test_snippet_end_of_last_snippet_falls_back_to_start_plus_duration():
    assert _snippet_end(OVERLAPPING, 2) == pytest.approx(4.84 + 6.48)


# --- chunk_transcript -------------------------------------------------------

def test_empty_transcript_gives_no_chunks():
    assert chunk_transcript([]) == []


def test_single_snippet_gives_one_chunk():
    chunks = chunk_transcript([{"text": "hello", "start": 5.0, "duration": 2.0}])
    assert chunks == [{"text": "hello", "start_seconds": 5.0, "end_seconds": 7.0}]


def test_chunk_end_is_next_snippets_start():
    # 4-character texts, chunk_size 10: "aaaa bbbb" fits, adding "cccc" does not.
    s = snippets(["aaaa", "bbbb", "cccc", "dddd"], duration=5.0)
    chunks = chunk_transcript(s, chunk_size=10, overlap=0)
    assert chunks[0]["text"] == "aaaa bbbb"
    assert chunks[0]["end_seconds"] == s[2]["start"]  # 2.0, not 1.0 + 5.0


def test_overlap_repeats_text_from_previous_chunk():
    s = snippets(["aaaa", "bbbb", "cccc", "dddd", "eeee"])
    chunks = chunk_transcript(s, chunk_size=10, overlap=4)
    # Chunk 0 is "aaaa bbbb"; chunk 1 steps back one snippet and starts at "bbbb".
    assert chunks[1]["text"].startswith("bbbb")
    assert chunks[1]["start_seconds"] < chunks[0]["end_seconds"]


def test_snippet_longer_than_chunk_size_becomes_its_own_chunk():
    s = snippets(["x" * 50, "aaaa"])
    chunks = chunk_transcript(s, chunk_size=10, overlap=0)
    assert [c["text"] for c in chunks] == ["x" * 50, "aaaa"]


def test_overlap_larger_than_chunk_size_still_finishes():
    # Without the forward-progress guard this would repeat the same chunk forever.
    s = snippets(["aaaa", "bbbb", "cccc", "dddd"])
    chunks = chunk_transcript(s, chunk_size=10, overlap=1000)
    assert chunks[-1]["text"].endswith("dddd")


def test_invariants_on_a_longer_transcript():
    texts = [f"snippet number {i:02d}" for i in range(30)]
    s = snippets(texts, step=2.5)
    chunks = chunk_transcript(s, chunk_size=100, overlap=30)

    # Nothing dropped: every snippet appears in some chunk.
    for t in texts:
        assert any(t in c["text"] for c in chunks), t

    starts = [c["start_seconds"] for c in chunks]
    assert starts == sorted(starts)
    assert all(c["start_seconds"] <= c["end_seconds"] for c in chunks)

    last = s[-1]
    assert chunks[-1]["end_seconds"] == pytest.approx(last["start"] + last["duration"])


def test_input_is_not_modified():
    # The input is the video's stored raw_transcript.
    s = snippets(["aaaa", "bbbb", "cccc"])
    before = copy.deepcopy(s)
    chunk_transcript(s, chunk_size=10, overlap=4)
    assert s == before
