"""Tests for build_answer_file in evaluation/save_answer.py. No database."""

from datetime import datetime, timezone

import pytest

from evaluation.save_answer import build_answer_file


def logged_row(**overrides):
    row = {
        "id": 26,
        "created_at": datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc),
        "video_id": "LXaFHI_2Hus",
        "question": "What did the speaker learn?",
        "answer": "He learned to ask questions [2].",
        "model": "gemini/gemini-3.6-flash",
        "model_requested": "gemini/gemini-3.6-flash",
        "prompt_version": "answer_v1",
        "commit_sha": "abc1234",
        "retrieval_method": "dense",
        "chunking_run": "fixed_1000_200",
        "error": None,
        # Stored in rank order by the app, but deliberately shuffled here:
        # the builder must sort by rank, not trust the stored order.
        "retrieved_chunks": [
            {"chunk_id": 12, "score": 0.7, "rank": 2},
            {"chunk_id": 11, "score": 0.9, "rank": 1},
        ],
    }
    row.update(overrides)
    return row


CHUNKS = {
    11: {"text": "first excerpt", "start_seconds": 50.0, "end_seconds": 90.0},
    12: {"text": "second excerpt", "start_seconds": 90.0, "end_seconds": 130.0},
}


def test_excerpts_are_numbered_by_rank_and_sorted():
    record = build_answer_file(logged_row(), CHUNKS)
    assert [e["rank"] for e in record["excerpts"]] == [1, 2]
    assert [e["text"] for e in record["excerpts"]] == ["first excerpt", "second excerpt"]


def test_chunk_ids_are_in_rank_order():
    assert build_answer_file(logged_row(), CHUNKS)["chunk_ids"] == [11, 12]


def test_record_carries_every_field_the_scorer_groups_by():
    record = build_answer_file(logged_row(), CHUNKS)
    for key in ("model", "model_requested", "prompt_version", "commit_sha",
                "retrieval_method", "chunking_run", "chunk_ids", "answer", "question"):
        assert key in record
    assert record["model"] == "gemini/gemini-3.6-flash"
    assert record["created_at"] == "2026-10-29T12:00:00+00:00"


def test_missing_chunk_is_refused_not_guessed():
    with pytest.raises(ValueError, match="chunk 12 not found"):
        build_answer_file(logged_row(), {11: CHUNKS[11]})


def test_row_without_answer_is_refused():
    with pytest.raises(ValueError, match="has no answer"):
        build_answer_file(logged_row(answer=None, error="no_chunks_retrieved"), CHUNKS)


def test_row_without_retrieved_chunks_is_refused():
    with pytest.raises(ValueError, match="has no retrieved chunks"):
        build_answer_file(logged_row(retrieved_chunks=[]), CHUNKS)
