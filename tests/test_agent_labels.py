"""evaluation/agent_labels.build_block/render and run_all.paired. Pure; no files of the repo touched."""

import yaml

from app.citations import citation_segments
from evaluation.agent_labels import build_block, render
from evaluation.answer_files import segment_hash
from evaluation.run_all import paired

ANSWER = {"question": "Q?", "video_id": "V", "model": "m",
          "answer": "Fact one [1]. Fact two [2]. Uncited tail."}


def judged(labels, refused=False):
    return {"refused": refused, "segments": [
        {"index": i, "label": l, "reason": f"r{i}"} for i, l in enumerate(labels)]}


def test_build_block_fills_hashes_from_the_saved_answer():
    block, problems = build_block("row1.json", ANSWER, judged(["supported", "partly", "unsupported"]), "this")
    assert problems == []
    want = [segment_hash(s.text) for s in citation_segments(ANSWER["answer"])]
    assert [s["hash"] for s in block["segments"]] == want
    assert block["run"] == "this" and block["segments"][2]["reason"] == "r2"


def test_build_block_reports_a_missing_or_bad_label():
    _, problems = build_block("row1.json", ANSWER, judged(["supported", "partly"]), "this")
    assert any("labelled segments" in p for p in problems)
    _, problems = build_block("row1.json", ANSWER, judged(["supported", "maybe", "partly"]), "this")
    assert any("not allowed" in p for p in problems)
    _, problems = build_block("row1.json", ANSWER, {"refused": None}, "this")
    assert any("refused" in p for p in problems)


def test_render_round_trips_through_yaml():
    block, _ = build_block("row1.json", ANSWER, judged(["supported", "partly", "unsupported"]), "this")
    data = yaml.safe_load(render([block]))
    assert data["labeller"].startswith("claude-code")
    assert data["answers"][0]["segments"][1]["label"] == "partly"
    assert data["answers"][0]["refused"] is False


def rec(rank, negative=False):
    return {"rank": rank, "negative": negative}


def test_paired_compares_reciprocal_rank_and_skips_negatives():
    a = [rec(1), rec(2), rec(None), rec(3), rec(None, True)]
    b = [rec(2), rec(2), rec(1), rec(None), rec(1, True)]
    assert paired(a, b) == {"better": 2, "worse": 1, "tied": 1}
