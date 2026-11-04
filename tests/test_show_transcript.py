"""Tests for evaluation/show_transcript.py. Pure functions; no database."""

import pytest

from evaluation.show_transcript import format_lines, golden_ids, mmss, parse_time

SNIPPETS = [
    {"text": "first line", "start": 0.0, "duration": 4.0},
    {"text": "second   line\nspans two", "start": 4.0, "duration": 4.0},
    {"text": "third", "start": 65.0, "duration": 5.0},
    {"text": "last", "start": 130.0, "duration": 3.0},
]


def test_each_caption_is_one_line_with_its_start_as_mmss():
    assert format_lines(SNIPPETS) == [
        "[00:00] first line",
        "[00:04] second line spans two",
        "[01:05] third",
        "[02:10] last",
    ]


def test_whitespace_inside_a_caption_is_collapsed_to_one_line():
    lines = format_lines([{"text": "a\n\nb   c", "start": 1.0, "duration": 1.0}])
    assert lines == ["[00:01] a b c"]


def test_window_keeps_captions_whose_time_range_overlaps_it():
    # Window 60..70. The caption at 4..65 runs past 60, so it overlaps the
    # window and is shown; the caption at 65..70 is shown too.
    lines = format_lines(SNIPPETS, start=60.0, end=70.0)
    assert lines == ["[00:04] second line spans two", "[01:05] third"]


def test_window_edge_that_only_touches_is_not_shown():
    # The caption at 0..4 ends exactly where the window starts at 4.
    lines = format_lines(SNIPPETS, start=4.0, end=5.0)
    assert lines == ["[00:04] second line spans two"]


def test_window_end_is_exclusive_for_a_caption_starting_on_it():
    lines = format_lines(SNIPPETS, start=0.0, end=4.0)
    assert lines == ["[00:00] first line"]


def test_last_caption_end_is_start_plus_duration():
    # Chunking's rule: the last caption ends at start + duration (133 here).
    assert format_lines(SNIPPETS, start=132.0) == ["[02:10] last"]


def test_no_window_shows_everything():
    assert len(format_lines(SNIPPETS)) == len(SNIPPETS)


@pytest.mark.parametrize("value, seconds", [
    ("480", 480.0),
    ("8:00", 480.0),
    ("1:30.5", 90.5),
])
def test_parse_time_accepts_seconds_and_minutes(value, seconds):
    assert parse_time(value) == seconds


def test_mmss_pads_minutes_and_seconds():
    assert mmss(5) == "00:05"
    assert mmss(3725) == "62:05"


def test_golden_ids_lists_every_video_in_the_file():
    golden = {"videos": [{"video_id": "LXaFHI_2Hus"}, {"video_id": "i3AkTO9HLXo"}]}
    assert golden_ids(golden) == {"LXaFHI_2Hus", "i3AkTO9HLXo"}


def test_golden_ids_on_an_empty_file_is_empty():
    assert golden_ids({}) == set()
