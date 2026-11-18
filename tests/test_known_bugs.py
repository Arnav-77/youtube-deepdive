"""generate() bounds every model attempt with a timeout.

History: on 18 Nov 2026 one gemini-3.6-flash call hung for about five minutes
during the final evaluation, because generate() passed no timeout and LiteLLM's
default is 6000 s. This file first held a strict xfail showing the bug. The fix
(MODEL_TIMEOUT_S = 45 in app/generation.py, applied by LiteLLM to each model
attempt, so primary plus one backup stay under 100 s) turned it into the
normal tests below.
"""

from types import SimpleNamespace

import app.generation as generation
from app.retrieval import Retrieved

HIT = Retrieved(chunk_id=1, text="text", start_seconds=0.0, end_seconds=10.0, score=1.0, rank=1)


def test_generate_bounds_the_model_call_with_a_timeout(monkeypatch):
    seen = {}

    def fake_completion(**kwargs):
        seen.update(kwargs)
        message = SimpleNamespace(content="An answer [1].")
        return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                               model="gemini-3.6-flash", usage=None)

    monkeypatch.setattr(generation.litellm, "completion", fake_completion)
    monkeypatch.setattr(generation.litellm, "completion_cost", lambda **k: 0.0)
    result = generation.generate("q?", [HIT])
    assert result.answer == "An answer [1]."
    assert seen["timeout"] == generation.MODEL_TIMEOUT_S == 45


def test_primary_plus_one_backup_stay_under_100_seconds():
    assert 2 * generation.MODEL_TIMEOUT_S < 100


def test_a_timeout_becomes_an_error_field_not_an_exception(monkeypatch):
    def fake_completion(**kwargs):
        raise TimeoutError("model did not answer in time")

    monkeypatch.setattr(generation.litellm, "completion", fake_completion)
    result = generation.generate("q?", [HIT])
    assert result.answer is None
    assert result.error.startswith("TimeoutError")
    assert result.model_requested == generation.PRIMARY_MODEL
