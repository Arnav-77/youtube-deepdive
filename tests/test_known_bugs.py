"""Known bugs in app/, shown as strict xfail tests (finish plan: app code is frozen).

Each test states the correct behaviour and fails today. When the bug is fixed
the test passes, strict=True turns that into a failure, and the xfail marker
must be removed: the fix cannot go unnoticed. Details in report_out/REVIEW.md.
"""

from types import SimpleNamespace

import pytest

import app.generation as generation
from app.retrieval import Retrieved


@pytest.mark.xfail(strict=True, reason=(
    "generate() passes no timeout to litellm.completion, so LiteLLM's default "
    "(6000 s) applies: one hung Gemini call held a request for about 5 minutes "
    "on 18 Nov 2026, and could hold it for up to 100 minutes."))
def test_generate_bounds_the_model_call_with_a_timeout(monkeypatch):
    seen = {}

    def fake_completion(**kwargs):
        seen.update(kwargs)
        message = SimpleNamespace(content="An answer [1].")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                               model="gemini-3.6-flash", usage=None)

    monkeypatch.setattr(generation.litellm, "completion", fake_completion)
    monkeypatch.setattr(generation.litellm, "completion_cost", lambda **k: 0.0)
    hit = Retrieved(chunk_id=1, text="text", start_seconds=0.0, end_seconds=10.0, score=1.0, rank=1)
    result = generation.generate("q?", [hit])
    assert result.answer == "An answer [1]."
    assert seen.get("timeout") is not None
