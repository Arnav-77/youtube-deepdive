"""Tests for evaluation/label_answer.py. Pure functions only, no files outside tmp_path."""

import pytest
import yaml

from evaluation.answer_files import segment_hash
from evaluation.label_answer import append_skeleton, render_segments, skeleton_block


def saved(answer, excerpts=None):
    if excerpts is None:
        excerpts = [
            {"rank": 1, "chunk_id": 11, "start_seconds": 80.0, "end_seconds": 125.0,
             "text": "the first excerpt says this"},
            {"rank": 2, "chunk_id": 12, "start_seconds": 125.0, "end_seconds": 200.0,
             "text": "the second excerpt says that"},
        ]
    return {
        "row_id": 26,
        "video_id": "LXaFHI_2Hus",
        "question": "What did the speaker learn?",
        "answer": answer,
        "model": "gemini/gemini-3.6-flash",
        "excerpts": excerpts,
    }


# --- render_segments --------------------------------------------------------

def test_render_shows_index_hash_and_cited_excerpt_text():
    text = render_segments(saved("He learned this [1]. Then that [2]."))
    assert "[0] hash " + segment_hash("He learned this [1].") in text
    assert "[1] hash " + segment_hash("Then that [2].") in text
    assert "the first excerpt says this" in text
    assert "the second excerpt says that" in text


def test_render_gives_excerpt_times_as_minutes_and_seconds():
    text = render_segments(saved("Claim [1]."))
    assert "excerpt [1] (01:20-02:05)" in text


def test_render_flags_a_citation_the_model_was_never_shown():
    text = render_segments(saved("Claim [9]."))
    assert "excerpt [9]: NOT SENT TO THE MODEL" in text


def test_render_marks_uncited_text():
    text = render_segments(saved("Claim [1]. A bare sentence with no citation."))
    assert "cites nothing" in text


def test_render_does_not_show_a_label_or_a_verdict():
    # The helper prints material for reading. It never judges it.
    text = render_segments(saved("Claim [1]."))
    for word in ("supported", "partly", "unsupported", "refused", "label"):
        assert word not in text.lower()


# --- skeleton_block ---------------------------------------------------------

def test_skeleton_fills_index_citations_and_hash():
    answer = saved("First [1]. Second [1][2].")
    block = skeleton_block("row26.json", answer)
    entry = yaml.safe_load(block)[0]
    assert entry["answer_file"] == "row26.json"
    assert [s["index"] for s in entry["segments"]] == [0, 1]
    assert [s["cited"] for s in entry["segments"]] == [[1], [1, 2]]
    assert entry["segments"][0]["hash"] == segment_hash("First [1].")


def test_skeleton_leaves_every_label_and_refusal_blank():
    entry = yaml.safe_load(skeleton_block("row26.json", saved("First [1]. Second [2].")))[0]
    assert entry["refused"] is None
    assert all(s["label"] is None for s in entry["segments"])


def test_skeleton_writes_no_label_value_anywhere():
    # The comment on the label line lists the choices; the VALUE must be empty.
    # Checked on the parsed YAML, so a comment cannot fail or hide a real value.
    entry = yaml.safe_load(skeleton_block("row26.json", saved("First [1]. Second [1][2].")))[0]
    assert entry["refused"] is None
    assert all(s["label"] is None for s in entry["segments"])


def test_skeleton_for_an_answer_with_no_segments():
    entry = yaml.safe_load(skeleton_block("row26.json", saved("")))[0]
    assert entry["segments"] == []


def test_skeleton_quotes_hashes_so_yaml_keeps_them_as_text():
    # A hex hash like 1e5... would otherwise parse as a number.
    entry = yaml.safe_load(skeleton_block("row26.json", saved("Claim [1].")))[0]
    assert isinstance(entry["segments"][0]["hash"], str)


# --- append_skeleton --------------------------------------------------------

START = "# header comment\nanswers:\n"


def test_append_to_an_empty_list_gives_one_block():
    block = skeleton_block("row26.json", saved("Claim [1]."))
    data = yaml.safe_load(append_skeleton(START, block, "row26.json"))
    assert [a["answer_file"] for a in data["answers"]] == ["row26.json"]


def test_second_answer_is_appended_after_the_first():
    first = append_skeleton(START, skeleton_block("row26.json", saved("A [1].")), "row26.json")
    second = append_skeleton(first, skeleton_block("row27.json", saved("B [1].")), "row27.json")
    names = [a["answer_file"] for a in yaml.safe_load(second)["answers"]]
    assert names == ["row26.json", "row27.json"]


def test_same_answer_is_refused_the_second_time():
    block = skeleton_block("row26.json", saved("A [1]."))
    once = append_skeleton(START, block, "row26.json")
    with pytest.raises(ValueError, match="already has a block"):
        append_skeleton(once, block, "row26.json")


def test_block_that_breaks_the_yaml_is_refused():
    with pytest.raises(ValueError, match="does not parse"):
        append_skeleton(START, "  - [unclosed\n", "row26.json")


def test_labels_file_that_already_does_not_parse_is_refused_untouched():
    with pytest.raises(ValueError, match="NOT touched"):
        append_skeleton("answers:\n  - [unclosed\n", "", "row26.json")


def test_other_top_level_keys_are_preserved():
    text = "answers:\nother: 5\n"
    # "other" is after "answers:" with no blank line; appending at the end
    # would put the new block under "other", so the check must catch it.
    block = skeleton_block("row26.json", saved("A [1]."))
    with pytest.raises(ValueError):
        append_skeleton(text, block, "row26.json")
