"""evaluation/spot_check and spot_check_agreement: pure parts only."""

from evaluation.spot_check import candidates, pick
from evaluation.spot_check_agreement import compare, table_lines

LABELS = {"answers": [
    {"answer_file": "row3.json", "refused": False, "segments": [
        {"index": 0, "hash": "h30", "label": "supported"},
        {"index": 1, "hash": "h31", "label": "partly"}]},
    {"answer_file": "row10.json", "refused": True, "segments": [
        {"index": 0, "hash": "h100", "label": "unsupported"}]},
    {"answer_file": "row12.json", "refused": False, "segments": [
        {"index": 0, "hash": "h120", "label": "unsupported"}]},
]}


def test_candidates_skip_refused_answers_and_sort_by_row():
    assert candidates(LABELS) == [("row3.json", 0), ("row3.json", 1), ("row12.json", 0)]


def test_pick_is_repeatable_and_capped():
    c = [(f"row{i}.json", 0) for i in range(100)]
    assert pick(c, 20, 20261118) == pick(c, 20, 20261118)
    assert len(pick(c, 20)) == 20
    assert len(pick(c[:3], 20)) == 3


def test_compare_checks_hashes_and_counts_agreement():
    spot = {"segments": [
        {"item": 1, "answer_file": "row3.json", "index": 0, "hash": "h30", "label": "supported"},
        {"item": 2, "answer_file": "row3.json", "index": 1, "hash": "h31", "label": "unsupported"},
        {"item": 3, "answer_file": "row12.json", "index": 0, "hash": "h120", "label": None},
    ]}
    r = compare(spot, LABELS)
    assert r["problems"] == [] and r["blank"] == 1
    assert r["pairs"] == [("supported", "supported"), ("unsupported", "partly")]
    lines = table_lines(r["pairs"])
    assert lines[1] == "agreement: 50% (1/2)"


def test_compare_refuses_a_stale_hash():
    spot = {"segments": [{"item": 1, "answer_file": "row3.json", "index": 0,
                          "hash": "other", "label": "supported"}]}
    assert "stale" in compare(spot, LABELS)["problems"][0]
