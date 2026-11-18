"""run_eval.summarise, the cache-only guard, and the dev-branch host check.

All pure or in-process: no database, no network, and no evaluation script is
run (CLAUDE.md, evaluation rule 9).
"""

import pytest

import app.embeddings
from evaluation import run_eval
from evaluation.dev_guard import host_of, is_dev_host


def rec(rank=None, offset=None, negative=False, returned=5, qtype="-"):
    return {"video_id": "v", "q": "q", "type": qtype, "negative": negative,
            "rank": rank, "offset": offset, "returned": returned}


def test_summarise_recall_mrr_and_offsets():
    s = run_eval.summarise([rec(1, -10), rec(2, 30), rec(None)], k=5)
    assert s["n"] == 3 and s["hits"] == 2
    assert s["recall"] == pytest.approx(2 / 3)
    assert s["mrr"] == pytest.approx((1 + 0.5 + 0) / 3)
    assert s["rank1"] == 1
    assert s["late"] == 1 and s["worst_late"] == 30
    assert s["abs_offset_median"] == 20


def test_summarise_keeps_negatives_out_of_recall():
    s = run_eval.summarise([rec(1, 0), rec(negative=True, returned=0),
                            rec(negative=True, returned=5)], k=5)
    assert s["n"] == 1 and s["recall"] == 1.0
    assert s["negatives"] == 2 and s["negatives_empty"] == 1


def test_summarise_with_no_positive_questions():
    s = run_eval.summarise([rec(negative=True)], k=5)
    assert s["recall"] is None and s["mrr"] is None
    assert run_eval.summary_lines(s)[0].strip() == "no questions with gold spans"


def test_cache_only_raises_instead_of_calling_the_api(monkeypatch):
    monkeypatch.setattr(app.embeddings, "_embed_batch", app.embeddings._embed_batch)
    run_eval.forbid_embedding_calls()
    with pytest.raises(run_eval.CacheMiss):
        app.embeddings._embed_batch(["a question"], "QUESTION_ANSWERING")


@pytest.mark.parametrize("url, dev", [
    ("postgresql+psycopg://u:p@ep-jolly-credit-ax9-pooler.c-4.us-east-2.aws.neon.tech/db", True),
    ("postgresql+psycopg://u:p@ep-other-thing-pooler.c-4.us-east-2.aws.neon.tech/db", False),
    ("postgresql+psycopg://nobody@127.0.0.1:9/never", False),
])
def test_dev_host_check(url, dev):
    assert is_dev_host(host_of(url)) is dev


def test_host_never_includes_the_password():
    assert "secret" not in host_of("postgresql://user:secret@ep-jolly-credit-x.neon.tech/db")
