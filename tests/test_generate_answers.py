"""Budget accounting in evaluation/generate_answers.py. Pure functions only.

The script itself is never run from a test (CLAUDE.md, evaluation rule 9):
it calls Gemini and writes query_logs rows.
"""

import pytest

from evaluation.generate_answers import calls_charged, is_overload, questions_from


@pytest.mark.parametrize("status, model, requested, error, expected", [
    (200, "gemini-3.6-flash", "gemini/gemini-3.6-flash", None, 1),          # normal answer
    (200, "gemini-3.5-flash-lite", "gemini/gemini-3.6-flash", None, 2),     # fallback fired
    (200, "gemini-3.6-flash", "gemini/gemini-3.6-flash", "invalid_citations: [7]", 1),
    (502, None, "gemini/gemini-3.6-flash", "ServiceUnavailableError: x", 2),  # both tried
    (200, None, None, "no_chunks_retrieved", 0),                             # no model call
    (404, None, None, "video_not_ingested: x", 0),
])
def test_calls_charged(status, model, requested, error, expected):
    assert calls_charged(status, model, requested, error) == expected


@pytest.mark.parametrize("status, detail, expected", [
    (502, "Generation failed: ServiceUnavailableError: litellm...", True),
    (502, "Generation failed: RateLimitError: 429", True),
    (502, "Generation failed: AuthenticationError", False),
    (429, "", True),
    (200, "", False),
])
def test_is_overload(status, detail, expected):
    assert is_overload(status, detail) is expected


def test_questions_from_filters_by_set_and_marks_negatives():
    golden = {"videos": [
        {"video_id": "a", "set": "dev", "questions": [{"q": "x", "spans": [[1, 2]]}]},
        {"video_id": "b", "set": "reported", "questions": [
            {"q": "y", "spans": [], "type": "negative"}]},
    ]}
    assert [q["q"] for q in questions_from(golden, "all")] == ["x", "y"]
    only = questions_from(golden, "reported")
    assert only == [{"video_id": "b", "set": "reported", "q": "y",
                     "type": "negative", "negative": True}]
