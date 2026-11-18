"""Pure parts of export_logs, log_stats and live_latency. No database, no network.

The scripts themselves are never run from a test (CLAUDE.md, evaluation rule 9).
"""

from datetime import datetime, timezone

import pytest

from evaluation.live_latency import pick_questions, summary
from evaluation.log_rows import (
    citation_counts,
    error_type,
    export_row,
    outcome_of,
    percentile,
    source_of,
    strip_provider,
)
from evaluation.log_stats import compute, select


def test_percentile_matches_linear_interpolation():
    assert percentile([], 0.5) is None
    assert percentile([5], 0.95) == 5
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile(range(1, 21), 0.95) == pytest.approx(19.05)


@pytest.mark.parametrize("sha, source", [
    (None, "unknown"), ("", "unknown"), ("local", "laptop"),
    ("local-abc1234", "laptop"), ("5cad71f0e1", "production"),
])
def test_source_of(sha, source):
    assert source_of(sha) == source


def test_strip_provider():
    assert strip_provider("gemini/gemini-3.6-flash") == "gemini-3.6-flash"
    assert strip_provider("gemini-3.6-flash") == "gemini-3.6-flash"
    assert strip_provider(None) == ""


@pytest.mark.parametrize("error, has_answer, outcome", [
    (None, True, "answered"),
    ("invalid_citations: [9]", True, "answered, invalid citation number"),
    ("no_chunks_retrieved", False, "nothing retrieved"),
    ("bad_video_reference: x", False, "user error: bad link (400)"),
    ("video_not_ingested: x", False, "user error: video not added (404)"),
    ("ServiceUnavailableError: busy", False, "failed"),
    (None, False, "failed"),
])
def test_outcome_of(error, has_answer, outcome):
    assert outcome_of({"error": error, "has_answer": has_answer}) == outcome


def test_error_type_keeps_the_class_only():
    assert error_type("ServiceUnavailableError: litellm says busy") == "ServiceUnavailableError"
    assert error_type(None) == ""


def test_citation_counts_split_valid_and_invalid():
    assert citation_counts("A [1]. B [2][7].", 5) == ([1, 2, 7], 2, 1)
    assert citation_counts(None, 5) == ([], 0, 0)


def test_export_row_has_no_text_unless_asked():
    log = {"id": 3, "created_at": datetime(2026, 11, 18, tzinfo=timezone.utc),
           "question": "q?", "answer": "A [1].", "retrieved_chunks": [{"chunk_id": 1}],
           "commit_sha": "local-x"}
    row = export_row(log)
    assert "question" not in row and "answer" not in row
    assert row["valid_cited"] == 1 and row["n_retrieved"] == 1 and row["has_answer"] is True
    assert export_row(log, with_text=True)["answer"] == "A [1]."


def rows():
    base = {"video_id": "v", "has_answer": "True", "cited": "1", "retrieval_method": "dense"}
    return [
        {**base, "id": "1", "created_at": "2026-11-17T10:00", "commit_sha": "abc",
         "model": "gemini-3.6-flash", "model_requested": "gemini/gemini-3.6-flash",
         "latency_ms": "4000", "estimated_cost_usd": "0.001", "error": ""},
        {**base, "id": "2", "created_at": "2026-11-18T10:00", "commit_sha": "abc",
         "model": "gemini-3.5-flash-lite", "model_requested": "gemini/gemini-3.6-flash",
         "latency_ms": "8000", "estimated_cost_usd": "0.002", "error": "", "cited": ""},
        {**base, "id": "3", "created_at": "2026-11-18T11:00", "commit_sha": "abc",
         "has_answer": "False", "error": "ServiceUnavailableError: busy",
         "latency_ms": "30000", "model": "", "model_requested": "gemini/gemini-3.6-flash"},
        {**base, "id": "4", "created_at": "2026-11-18T12:00", "commit_sha": "abc",
         "video_id": "", "has_answer": "False", "error": "bad_video_reference: x"},
    ]


def test_compute_counts_failures_fallbacks_cost_and_latency():
    s = compute(rows())
    assert s["per_day"] == {"2026-11-17": 1, "2026-11-18": 3}
    assert s["servable"] == 3 and s["failed"] == 1
    assert s["failed_by_type"] == {"ServiceUnavailableError": 1}
    assert s["latency_n"] == 2 and s["p50"] == 6000          # failed requests left out
    assert s["fallback"] == 1 and s["fallback_n"] == 2
    assert s["cost_per_day"] == {"2026-11-17": 0.001, "2026-11-18": 0.002}
    assert s["retrieval"] == {"dense": 3}                    # the bad link reached no video
    assert s["no_citation"] == 1


def test_select_by_source_day_and_id():
    data = rows() + [{"id": "9", "created_at": "2026-11-18", "commit_sha": "local-x"}]
    assert [r["id"] for r in select(data, "laptop", None, None)] == ["9"]
    assert [r["id"] for r in select(data, "production", "2026-11-18", 3)] == ["3", "4"]


def test_live_latency_picks_in_order_and_summarises():
    golden = {"videos": [{"video_id": "a", "questions": [{"q": "1"}, {"q": "2"}]},
                         {"video_id": "b", "questions": [{"q": "3"}]}]}
    assert pick_questions(golden, 2) == [("a", "1"), ("a", "2")]
    lines = summary([{"status": 200, "server_ms": 1000, "wall_ms": 1500},
                     {"status": 200, "server_ms": 3000, "wall_ms": 3500},
                     {"status": 502, "server_ms": None, "wall_ms": 9000}])
    assert lines[0] == "requests: 3, status 200: 2, failed: 1"
    assert "server latency (n=2): P50 2000 ms" in lines[1]
    assert any("status 502: 1" in line for line in lines)


# --- citation_rate ---------------------------------------------------------

from evaluation.citation_rate import rate, refusal_labels


def test_citation_rate_counts_valid_citations_and_separates_refusals():
    base = {"error": "", "has_answer": "True"}
    data = [
        {**base, "id": "1", "valid_cited": "2", "cited": "1 2", "invalid_cited": "0"},
        {**base, "id": "2", "valid_cited": "0", "cited": "", "invalid_cited": "0"},   # refused
        {**base, "id": "3", "valid_cited": "0", "cited": "9", "invalid_cited": "1",
         "error": "invalid_citations: [9]"},
        {"id": "4", "error": "ServiceUnavailableError: x", "has_answer": "False"},  # not a response
    ]
    s = rate(data, {1: (False, "f"), 2: (True, "f")})
    assert s["answered"] == 3 and s["cited"] == 1
    assert s["refused"] == 1 and s["not_refused"] == 2 and s["not_refused_cited"] == 1
    assert s["unlabelled"] == 1 and s["unlabelled_no_citation"] == 0
    assert s["invalid_any"] == 1


def test_refusal_labels_reads_row_ids_and_skips_blank_ones(tmp_path):
    f = tmp_path / "labels.yaml"
    f.write_text("answers:\n"
                 "  - {answer_file: row7.json, refused: true}\n"
                 "  - {answer_file: row8.json, refused: }\n", encoding="utf-8")
    assert refusal_labels([f]) == {7: (True, "labels.yaml")}


def test_citation_rate_source_filter_leaves_laptop_rows_out():
    from evaluation.log_rows import source_of
    rows = [{"commit_sha": "abc123"}, {"commit_sha": "local"}, {"commit_sha": "local-x"}, {"commit_sha": ""}]
    assert [r["commit_sha"] for r in rows if source_of(r["commit_sha"]) == "production"] == ["abc123"]
