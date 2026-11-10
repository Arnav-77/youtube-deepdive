"""Tests for app/limits.py: the public limits, the daily-cap setting and hiding.

Pure functions only: no database, no network, no Gemini.
"""

import pytest

from app.limits import (
    DEFAULT_PUBLIC_DAILY_CHUNKS,
    PUBLIC_PASTE_CHUNK_LIMIT,
    load_daily_cap,
    load_hidden_ids,
    public_paste_refusal,
)


# --- PUBLIC_PASTE_DAILY_CHUNKS -----------------------------------------------

def test_the_daily_cap_defaults_to_600():
    assert DEFAULT_PUBLIC_DAILY_CHUNKS == 600
    assert load_daily_cap(None) == 600
    assert load_daily_cap("") == 600
    assert load_daily_cap("   ") == 600


@pytest.mark.parametrize("raw, expected", [("250", 250), (" 1200 ", 1200), ("0", 0)])
def test_a_whole_number_sets_the_daily_cap(raw, expected):
    assert load_daily_cap(raw) == expected


@pytest.mark.parametrize("raw", ["-5", "abc", "1.5", "600 chunks"])
def test_a_bad_daily_cap_stops_the_app(raw):
    with pytest.raises(RuntimeError, match="PUBLIC_PASTE_DAILY_CHUNKS"):
        load_daily_cap(raw)


# --- HIDDEN_VIDEO_IDS --------------------------------------------------------

def test_nothing_is_hidden_when_the_setting_is_blank():
    assert load_hidden_ids(None) == frozenset()
    assert load_hidden_ids("") == frozenset()


def test_hidden_ids_are_comma_separated_and_trimmed():
    ids = load_hidden_ids("AAAAAAAAAAA, BBBBBBBBBBB ,CCCCCCCCCCC")
    assert ids == frozenset({"AAAAAAAAAAA", "BBBBBBBBBBB", "CCCCCCCCCCC"})


def test_a_url_in_the_hidden_list_stops_the_app():
    with pytest.raises(RuntimeError, match="not URLs"):
        load_hidden_ids("https://youtu.be/AAAAAAAAAAA")


@pytest.mark.parametrize("raw", ["short", "AAAAAAAAAAAA", "AAAAAAAAAA!"])
def test_a_malformed_hidden_id_stops_the_app(raw):
    with pytest.raises(RuntimeError, match="HIDDEN_VIDEO_IDS"):
        load_hidden_ids(raw)


# --- the two visitor limits, in order ----------------------------------------

def test_a_paste_of_exactly_the_chunk_limit_is_allowed():
    assert public_paste_refusal(PUBLIC_PASTE_CHUNK_LIMIT, 0, 600) is None


def test_a_paste_over_the_chunk_limit_is_422_with_the_numbers():
    code, message = public_paste_refusal(151, 0, 600)
    assert code == 422
    assert "151 chunks" in message
    assert "150" in message


def test_the_chunk_limit_is_reported_before_the_daily_cap():
    # Both limits would fail; the paste itself is the problem, so 422 wins.
    code, _ = public_paste_refusal(151, 600, 600)
    assert code == 422


def test_a_paste_that_lands_exactly_on_the_daily_cap_is_allowed():
    assert public_paste_refusal(150, 450, 600) is None


def test_a_paste_that_would_pass_the_daily_cap_is_429_with_the_numbers():
    code, message = public_paste_refusal(150, 460, 600)
    assert code == 429
    assert "600" in message and "460" in message


def test_a_zero_daily_cap_refuses_every_visitor_paste():
    code, _ = public_paste_refusal(1, 0, 0)
    assert code == 429
