"""Tests for evaluation/score_grounding.py. Fake files in tmp_path; no database, no network."""

import json

from app.citations import citation_segments
from evaluation.answer_files import segment_hash
from evaluation.score_grounding import collect, model_name, refusal_table, summarise

# Excerpt 1 covers 10-40 s, excerpt 2 covers 100-130 s.
EXCERPTS = [
    {"rank": 1, "chunk_id": 1, "start_seconds": 10.0, "end_seconds": 40.0, "text": "one"},
    {"rank": 2, "chunk_id": 2, "start_seconds": 100.0, "end_seconds": 130.0, "text": "two"},
]

GOLDEN = {"videos": [{"video_id": "V", "questions": [
    {"q": "Answer near the start?", "spans": [[20, 35]]},
    {"q": "Not in the video?", "spans": []},
    {"q": "Answer far away?", "spans": [[500, 510]]},
]}]}


def answer(text, question="Answer near the start?", model="gemini/a"):
    return {"row_id": 1, "video_id": "V", "question": question, "answer": text,
            "model": model, "chunk_ids": [1, 2], "excerpts": EXCERPTS}


def write(dir_, name, data):
    (dir_ / name).write_text(json.dumps(data), encoding="utf-8")


def block(name, data, refused, labels=None, notes=None):
    """A label block exactly as label_answer writes it, with labels filled in."""
    segments = []
    for i, seg in enumerate(citation_segments(data["answer"])):
        segments.append({
            "index": seg.index,
            "cited": list(seg.cited),
            "hash": segment_hash(seg.text),
            "label": (labels[i] if labels and i < len(labels) else None),
            "note": (notes or {}).get(i),
        })
    return {"answer_file": name, "question": data["question"], "video_id": "V",
            "model": data["model"], "refused": refused, "segments": segments}


def run(tmp_path, files, blocks, golden=GOLDEN):
    answers = tmp_path / "answers"
    answers.mkdir(exist_ok=True)
    for name, data in files.items():
        write(answers, name, data)
    return collect({"answers": blocks}, golden, answers)


# --- grounding --------------------------------------------------------------

def test_segment_rate_and_answer_level_rate(tmp_path):
    files = {
        "a1.json": answer("Claim one [1]. Claim two [2]."),
        "a2.json": answer("Claim three [1]. Claim four [2]."),
    }
    blocks = [
        block("a1.json", files["a1.json"], False, ["supported", "supported"]),
        block("a2.json", files["a2.json"], False, ["supported", "unsupported"]),
    ]
    result = run(tmp_path, files, blocks)
    assert result.errors == []
    s = summarise(result.rows)
    assert (s["segments"], s["supported"]) == (4, 3)
    assert (s["every_segment_supported"], s["answered"]) == (1, 2)


def test_partly_supported_is_counted_separately_not_as_supported(tmp_path):
    files = {"a.json": answer("Mixed claim [1].")}
    result = run(tmp_path, files, [block("a.json", files["a.json"], False, ["partly"])])
    s = summarise(result.rows)
    assert (s["supported"], s["partly"]) == (0, 1)


def test_refused_answers_are_left_out_of_grounding(tmp_path):
    files = {"a.json": answer("I cannot tell from the excerpts.")}
    result = run(tmp_path, files, [block("a.json", files["a.json"], True)])
    s = summarise(result.rows)
    assert (s["answers"], s["refused"], s["answered"], s["segments"]) == (1, 1, 0, 0)


def test_wrong_link_note_is_counted(tmp_path):
    files = {"a.json": answer("This is because of that [2].")}
    result = run(tmp_path, files, [block("a.json", files["a.json"], False,
                                         ["partly"], {0: "support found in [1]"})])
    assert summarise(result.rows)["wrong_link_notes"] == 1


def test_citation_to_an_excerpt_never_sent_is_reported_not_an_error(tmp_path):
    files = {"a.json": answer("Claim [9].")}
    result = run(tmp_path, files, [block("a.json", files["a.json"], False, ["unsupported"])])
    assert result.errors == []
    assert summarise(result.rows)["answers_citing_unsent"] == 1


def test_grouping_by_model_keeps_each_model_separate(tmp_path):
    files = {
        "a.json": answer("A [1].", model="gemini/a"),
        "b.json": answer("B [1].", model="gemini/b"),
    }
    result = run(tmp_path, files, [
        block("a.json", files["a.json"], False, ["supported"]),
        block("b.json", files["b.json"], False, ["unsupported"]),
    ])
    a = summarise([r for r in result.rows if r.model == "a"])
    b = summarise([r for r in result.rows if r.model == "b"])
    assert (a["supported"], a["segments"]) == (1, 1)
    assert (b["supported"], b["segments"]) == (0, 1)


def test_provider_prefix_and_bare_model_name_count_as_one_model(tmp_path):
    # Request logged with the prefix, response reported without it.
    files = {
        "req.json": answer("A [1].", model="gemini/gemini-3.6-flash"),
        "resp.json": answer("B [1].", model="gemini-3.6-flash"),
    }
    result = run(tmp_path, files, [
        block("req.json", files["req.json"], False, ["supported"]),
        block("resp.json", files["resp.json"], False, ["unsupported"]),
    ])
    assert {r.model for r in result.rows} == {"gemini-3.6-flash"}
    both = summarise(result.rows)
    assert (both["supported"], both["segments"]) == (1, 2)
    from evaluation.score_grounding import format_report
    text = format_report(result, GOLDEN)
    assert "model gemini-3.6-flash" in text
    assert "model gemini/" not in text


def test_model_name_strips_only_the_first_provider_prefix():
    assert model_name("gemini/gemini-3.6-flash") == "gemini-3.6-flash"
    assert model_name("gemini-3.6-flash") == "gemini-3.6-flash"
    assert model_name("openrouter/google/gemma") == "google/gemma"


# --- refusal ----------------------------------------------------------------

def test_refusal_table_judges_each_case_by_its_gold_span(tmp_path):
    files = {
        # gold []: refusing is right
        "empty_refused.json": answer("Not covered.", question="Not in the video?"),
        # gold [], answered: a missed refusal
        "empty_answered.json": answer("Something [1].", question="Not in the video?"),
        # gold [20,35]: excerpt 1 (10-40) overlaps it, so refusing is a generation miss
        "near_refused.json": answer("Not covered.", question="Answer near the start?"),
        # gold [500,510]: nothing retrieved overlaps it, so refusing is a retrieval miss
        "far_refused.json": answer("Not covered.", question="Answer far away?"),
        # gold [20,35], answered: right
        "near_answered.json": answer("Yes [1].", question="Answer near the start?"),
    }
    blocks = [
        block("empty_refused.json", files["empty_refused.json"], True),
        block("empty_answered.json", files["empty_answered.json"], False, ["supported"]),
        block("near_refused.json", files["near_refused.json"], True),
        block("far_refused.json", files["far_refused.json"], True),
        block("near_answered.json", files["near_answered.json"], False, ["supported"]),
    ]
    rt = refusal_table(run(tmp_path, files, blocks).rows)
    assert rt["judged"] == 5
    assert rt["correct_refusal"] == 1
    assert rt["missed_refusal"] == 1
    assert rt["false_refusal"] == 2
    assert rt["false_refusal_generation"] == 1
    assert rt["false_refusal_retrieval"] == 1
    assert rt["answered_with_gold"] == 1
    assert rt["accurate"] == 2


def test_answer_to_a_question_not_in_golden_set_is_not_judged(tmp_path):
    files = {"a.json": answer("Claim [1].", question="Something unlabelled?")}
    result = run(tmp_path, files, [block("a.json", files["a.json"], True)])
    assert result.rows == []
    assert result.examples == ["a.json"]
    assert refusal_table(result.rows)["judged"] == 0


# --- integrity: these stop the scorer --------------------------------------

def test_stale_label_after_the_answer_changed_is_an_error(tmp_path):
    files = {"a.json": answer("Claim [1].")}
    stale = block("a.json", files["a.json"], False, ["supported"])
    files["a.json"] = answer("Claim changed [1].")
    result = run(tmp_path, files, [stale])
    assert any("stale" in e for e in result.errors)
    assert result.rows == []


def test_labelled_answer_whose_file_is_missing_is_an_error(tmp_path):
    files = {"a.json": answer("Claim [1].")}
    result = run(tmp_path, {}, [block("a.json", files["a.json"], False, ["supported"])])
    assert any("file is missing" in e for e in result.errors)


def test_label_outside_the_three_values_is_an_error(tmp_path):
    files = {"a.json": answer("Claim [1].")}
    result = run(tmp_path, files, [block("a.json", files["a.json"], False, ["maybe"])])
    assert any("'maybe'" in e for e in result.errors)


def test_refused_must_be_true_or_false(tmp_path):
    files = {"a.json": answer("Claim [1].")}
    bad = block("a.json", files["a.json"], "yes", ["supported"])
    result = run(tmp_path, files, [bad])
    assert any("refused must be true or false" in e for e in result.errors)


def test_two_blocks_for_one_answer_is_an_error(tmp_path):
    files = {"a.json": answer("Claim [1].")}
    one = block("a.json", files["a.json"], False, ["supported"])
    result = run(tmp_path, files, [one, one])
    assert any("two blocks" in e for e in result.errors)


def test_saved_file_that_is_not_an_answer_is_an_error(tmp_path):
    answers = tmp_path / "answers"
    answers.mkdir()
    (answers / "junk.json").write_text(json.dumps({"hello": 1}), encoding="utf-8")
    result = collect({"answers": []}, GOLDEN, answers)
    assert any("junk.json" in e for e in result.errors)


# --- unlabelled: reported, never counted as failures ------------------------

def test_block_not_yet_refused_is_unlabelled_not_an_error(tmp_path):
    files = {"a.json": answer("Claim [1].")}
    result = run(tmp_path, files, [block("a.json", files["a.json"], None, ["supported"])])
    assert result.errors == []
    assert result.unlabelled == ["a.json"]
    assert result.rows == []


def test_answered_block_with_a_blank_segment_label_is_unlabelled(tmp_path):
    files = {"a.json": answer("Claim [1].")}
    result = run(tmp_path, files, [block("a.json", files["a.json"], False, [None])])
    assert result.errors == []
    assert result.unlabelled == ["a.json"]
    assert result.rows == []


def test_refused_block_needs_no_segment_labels(tmp_path):
    files = {"a.json": answer("Claim [1].")}
    result = run(tmp_path, files, [block("a.json", files["a.json"], True)])
    assert result.errors == []
    assert len(result.rows) == 1


# --- report -----------------------------------------------------------------

def test_report_prints_every_number_with_its_sample_size(tmp_path):
    from evaluation.score_grounding import format_report
    files = {"a.json": answer("Claim [1]."), "b.json": answer("Other [1].", model="gemini/b")}
    result = run(tmp_path, files, [
        block("a.json", files["a.json"], False, ["supported"]),
        block("b.json", files["b.json"], False, ["unsupported"]),
    ])
    text = format_report(result, GOLDEN)
    assert "grounding rate, segments: 50% (1/2)" in text
    assert "model a" in text and "model b" in text
    assert "saved answers not yet labelled: 0" in text


def test_leading_citation_segments_are_counted_in_the_report(tmp_path):
    from evaluation.score_grounding import format_report
    files = {"a.json": answer("[1] The climber said X. [2] Second claim.")}
    result = run(tmp_path, files, [block("a.json", files["a.json"], False, ["supported", "supported"])])
    assert result.leading_segments == 1
    assert "carried to the claim after it: 1 of 2 answered segments" in format_report(result, GOLDEN)


def test_answer_not_in_golden_set_is_an_example_left_out_of_every_number(tmp_path):
    from evaluation.score_grounding import format_report
    files = {
        "gold.json": answer("Claim [1].", question="Free question?"),
        "example.json": answer("Other [1].", question="Something unlabelled?"),
    }
    golden = {"videos": [{"video_id": "V", "questions": [
        {"q": "Free question?", "spans": [[20, 35]]}]}]}
    result = run(tmp_path, files, [
        block("gold.json", files["gold.json"], False, ["supported"]),
        block("example.json", files["example.json"], False, ["unsupported"]),
    ], golden=golden)
    assert [r.file for r in result.rows] == ["gold.json"]
    assert result.examples == ["example.json"]
    assert summarise(result.rows)["segments"] == 1
    assert refusal_table(result.rows)["judged"] == 1
    text = format_report(result, golden)
    assert "grounding rate, segments: 100% (1/1)" in text
    assert "left out of every number: 1" in text and "    example.json" in text
