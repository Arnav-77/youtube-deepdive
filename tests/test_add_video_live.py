"""The caption-to-panel converter in evaluation/add_video_live.py. Pure; nothing is sent.

The only claim that matters: whatever the converter writes, the server's own
parser (app.paste.parse_paste) reads back as the same words at the same
whole second, in order. The script itself is never run from a test.
"""

import pytest

from app.paste import PasteError, parse_paste
from evaluation.add_video_live import clean, stamp, to_panel_text

SNIPS = [
    {"text": "welcome to the course", "start": 0.0, "duration": 4.2},
    {"text": "today we cover hashing", "start": 4.24, "duration": 3.0},
    {"text": "and then\ntwo lines", "start": 7.9, "duration": 2.0},
    {"text": "later on", "start": 75.5, "duration": 2.0},
]


def test_round_trip_through_the_servers_parser():
    parsed = parse_paste(to_panel_text(SNIPS)).snippets
    assert [s["text"] for s in parsed] == ["welcome to the course", "today we cover hashing",
                                           "and then two lines", "later on"]
    assert [s["start"] for s in parsed] == [0.0, 4.0, 7.0, 75.0]      # floored to whole seconds


@pytest.mark.parametrize("seconds, text", [(0, "0:00"), (59.9, "0:59"), (60, "1:00"),
                                           (3599, "59:59"), (4500.4, "75:00"), (36000, "600:00")])
def test_stamp_is_minutes_and_seconds(seconds, text):
    assert stamp(seconds) == text


def test_a_caption_that_looks_like_a_time_is_not_a_new_segment():
    snips = [{"text": "5:30 pm is when it starts", "start": 10.0, "duration": 3.0},
             {"text": "12:45 is lunch", "start": 20.0, "duration": 3.0}]
    parsed = parse_paste(to_panel_text(snips)).snippets
    assert [s["text"] for s in parsed] == ["5:30 pm is when it starts", "12:45 is lunch"]
    assert [s["start"] for s in parsed] == [10.0, 20.0]


def test_empty_captions_and_control_characters_are_dropped_or_blanked():
    snips = [{"text": "   ", "start": 1.0, "duration": 1.0},
             {"text": "bell\x07 rings", "start": 2.0, "duration": 1.0}]
    text = to_panel_text(snips)
    assert "\x07" not in text
    assert [s["text"] for s in parse_paste(text).snippets] == ["bell rings"]
    assert clean(" a \t b\n c ") == "a b c"


def test_text_with_no_words_is_refused_by_the_parser_not_sent_silently():
    with pytest.raises(PasteError):
        parse_paste(to_panel_text([{"text": "", "start": 0.0, "duration": 1.0}]))


def test_a_start_past_999_minutes_cannot_be_written():
    with pytest.raises(ValueError):
        stamp(60 * 1000)
