"""Tests for the paste, progress, list and ask routes, with the limits and hiding.

Offline: the database session is a fake, and the store, count, progress and
job functions are patched in app.main. No Gemini, no network, no database.
The token below is a dummy value that exists only in this file.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.chunking import chunk_transcript
from app.db import get_session
from app.models import Video
from app.paste import MAX_PASTE_BYTES, parse_paste

DUMMY_TOKEN = "dummy-token-for-tests-only"
VIDEO_ID = "LXaFHI_2Hus"
OTHER_ID = "AAAAAAAAAAA"
URL = f"https://youtu.be/{VIDEO_ID}"
# Made-up words only.
TRANSCRIPT = "0:00 made up words one\n0:04 made up words two\n"


def make_transcript(n_chunks):
    """n_chunks snippets of 1000 characters each: one chunk per snippet."""
    lines = [f"{i // 60}:{i % 60:02d} " + "a" * 1000 for i in range(n_chunks)]
    return "\n".join(lines) + "\n"


def test_make_transcript_gives_exactly_the_requested_chunk_count():
    # The limit tests depend on this, so check it directly.
    for n in (3, 151):
        assert len(chunk_transcript(parse_paste(make_transcript(n)).snippets)) == n


FETCHED_BASE = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class FakeSession:
    """Just enough session for the routes. Any other SQL fails the test.

    The list query is answered the way the real one is: only videos with chunks
    in the run it asks for, newest first, at most the LIMIT it is given.
    """

    def __init__(self):
        self.videos = {}
        self.added = []
        self.chunked = set()  # video ids with chunks in fixed_1000_200
        self._clock = 0

    def get(self, model, key):
        return self.videos.get(key)

    def add(self, obj):
        self.added.append(obj)
        self._clock += 1
        obj.fetched_at = FETCHED_BASE + timedelta(minutes=self._clock)
        self.videos[obj.video_id] = obj

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, statement, params=None):
        sql = str(statement)
        if "SELECT source FROM videos" in sql:
            video = self.videos.get(params["v"])
            return _Scalar(video.source if video is not None else None)
        if "FROM videos AS v" in sql:
            rows = [v for v in self.videos.values() if v.video_id in self.chunked]
            rows.sort(key=lambda v: v.fetched_at, reverse=True)
            rows = rows[: params["n"]]
            return _Rows([
                {
                    "video_id": v.video_id,
                    "title": v.title,
                    "duration_seconds": v.duration_seconds,
                    "source": v.source,
                    "fetched_at": v.fetched_at,
                    "added_by": v.added_by,
                }
                for v in rows
            ])
        raise AssertionError(f"unexpected SQL in a route test: {sql[:60]}")


@pytest.fixture
def env(monkeypatch):
    fake = FakeSession()
    state = {"total": 0, "embedded": 0, "started": [], "used": 0}

    monkeypatch.setattr(main, "PASTE_TOKEN", DUMMY_TOKEN)
    monkeypatch.setattr(main, "PUBLIC_DAILY_CHUNKS", 600)
    monkeypatch.setattr(main, "HIDDEN_IDS", frozenset())
    monkeypatch.setattr(main, "visitor_chunks_last_24h", lambda session: state["used"])
    monkeypatch.setattr(main, "store_chunks", lambda session, video: session.chunked.add(video.video_id) or [])
    monkeypatch.setattr(
        main, "progress", lambda session, vid, run: (state["total"], state["embedded"])
    )
    monkeypatch.setattr(main, "start_embedding", lambda vid, run: state["started"].append(vid) or True)
    monkeypatch.setattr(main, "is_running", lambda vid: vid in state["started"])

    main.app.dependency_overrides[get_session] = lambda: fake
    try:
        yield SimpleNamespace(client=TestClient(main.app), fake=fake, state=state)
    finally:
        main.app.dependency_overrides.clear()


def post(client, body=None, token=None, raw=None):
    """Visitor by default (no token). Pass token=DUMMY_TOKEN for the admin."""
    headers = {} if token is None else {"X-Paste-Token": token}
    if raw is not None:
        return client.post(
            "/videos/paste", content=raw,
            headers={**headers, "content-type": "application/json"},
        )
    return client.post("/videos/paste", json=body, headers=headers)


def good_body(transcript=TRANSCRIPT, url=URL):
    return {"url": url, "transcript": transcript}


# --- who is calling -----------------------------------------------------------

def test_pasting_is_public_when_no_token_is_configured(env, monkeypatch):
    monkeypatch.setattr(main, "PASTE_TOKEN", "")
    r = post(env.client, good_body())
    assert r.status_code == 202
    assert env.fake.videos[VIDEO_ID].added_by == "visitor"


def test_a_header_is_ignored_when_no_admin_token_is_configured(env, monkeypatch):
    monkeypatch.setattr(main, "PASTE_TOKEN", "")
    r = post(env.client, good_body(), token="anything")
    assert r.status_code == 202
    assert env.fake.videos[VIDEO_ID].added_by == "visitor"


def test_no_header_is_a_visitor_and_gets_the_limits(env):
    r = post(env.client, good_body(), token=None)
    assert r.status_code == 202
    assert env.fake.videos[VIDEO_ID].added_by == "visitor"


def test_an_empty_header_counts_as_no_header(env):
    r = post(env.client, good_body(), token="")
    assert r.status_code == 202
    assert env.fake.videos[VIDEO_ID].added_by == "visitor"


def test_a_wrong_token_is_401_and_nothing_is_stored(env):
    r = post(env.client, good_body(), token="guess")
    assert r.status_code == 401
    assert env.fake.added == []


def test_the_token_never_appears_in_any_response(env):
    responses = [
        post(env.client, good_body(), token=DUMMY_TOKEN),       # 202 admin
        post(env.client, good_body()),                          # 202 visitor, resume-free
        post(env.client, good_body("0:00 different\n")),        # 409
        post(env.client, good_body(), token="guess"),           # 401
        post(env.client, {"url": "nope", "transcript": "x"}),   # 400
        env.client.get(f"/videos/{VIDEO_ID}"),                  # progress
        env.client.get("/videos"),                              # list
    ]
    for r in responses:
        assert DUMMY_TOKEN not in r.text


# --- limit 1: 150 chunks per visitor paste -------------------------------------

def test_a_visitor_paste_of_150_chunks_is_allowed(env):
    r = post(env.client, good_body(make_transcript(150)))
    assert r.status_code == 202


def test_a_visitor_paste_of_151_chunks_is_422_and_stores_nothing(env):
    r = post(env.client, good_body(make_transcript(151)))
    assert r.status_code == 422
    assert "151 chunks" in r.json()["message"]
    assert "150" in r.json()["message"]
    assert env.fake.added == []


def test_the_admin_can_paste_more_than_150_chunks(env):
    r = post(env.client, good_body(make_transcript(151)), token=DUMMY_TOKEN)
    assert r.status_code == 202
    assert env.fake.videos[VIDEO_ID].added_by == "admin"


# --- limit 2: the daily cap across all visitors --------------------------------

def test_a_visitor_paste_that_would_pass_the_daily_cap_is_429_and_stores_nothing(env):
    env.state["used"] = 460  # 460 + 150 = 610 > 600
    r = post(env.client, good_body(make_transcript(150)))
    assert r.status_code == 429
    assert "600" in r.json()["message"] and "460" in r.json()["message"]
    assert env.fake.added == []


def test_a_visitor_paste_that_lands_exactly_on_the_cap_is_allowed(env):
    env.state["used"] = 450  # 450 + 150 = 600, not over
    assert post(env.client, good_body(make_transcript(150))).status_code == 202


def test_the_admin_skips_the_daily_cap(env):
    env.state["used"] = 600
    assert post(env.client, good_body(make_transcript(150)), token=DUMMY_TOKEN).status_code == 202


def test_resuming_a_paste_never_counts_against_the_daily_cap_again(env):
    # Added while there was room, then the cap fills up. Resending the same
    # paste resumes the job and must not be refused.
    assert post(env.client, good_body(make_transcript(150))).status_code == 202
    env.state["used"] = 600
    r = post(env.client, good_body(make_transcript(150)))
    assert r.status_code == 202


def test_the_daily_cap_is_read_from_the_database_count(env, monkeypatch):
    seen = []
    monkeypatch.setattr(main, "visitor_chunks_last_24h", lambda session: seen.append(session) or 0)
    post(env.client, good_body())
    assert len(seen) == 1


# --- limit 3: visitor pastes are marked, and listed as not checked ------------

def add_listed(fake, video_id, source="pasted", added_by="visitor"):
    """A video with chunks in the run, so the real list query would return it."""
    video = Video(video_id=video_id, title=None, source=source, added_by=added_by,
                  raw_transcript=[], raw_paste=None, duration_seconds=None)
    fake.add(video)
    fake.chunked.add(video_id)
    return video


def test_the_list_is_a_top_level_array_with_the_page_fields_and_the_new_ones(env):
    add_listed(env.fake, VIDEO_ID)
    body = env.client.get("/videos").json()
    assert isinstance(body, list)
    assert set(body[0]) == {
        "video_id", "title", "duration_seconds", "source", "fetched_at", "added_by", "checked",
    }


def test_a_pasted_video_appears_in_the_list(env):
    assert post(env.client, good_body()).status_code == 202
    ids = [v["video_id"] for v in env.client.get("/videos").json()]
    assert VIDEO_ID in ids


def test_a_visitor_paste_is_listed_first_and_marked_not_checked(env):
    post(env.client, good_body())
    first = env.client.get("/videos").json()[0]
    assert first["video_id"] == VIDEO_ID
    assert first["added_by"] == "visitor"
    assert first["checked"] is False


def test_admin_and_captions_videos_are_checked(env):
    add_listed(env.fake, "AAAAAAAAAAA", added_by="admin")
    add_listed(env.fake, "BBBBBBBBBBB", source="captions", added_by=None)
    checked = {v["video_id"]: v["checked"] for v in env.client.get("/videos").json()}
    assert checked == {"AAAAAAAAAAA": True, "BBBBBBBBBBB": True}


def test_a_pasted_row_with_no_added_by_is_treated_as_not_checked(env):
    add_listed(env.fake, VIDEO_ID, added_by=None)
    assert env.client.get("/videos").json()[0]["checked"] is False


def test_a_video_without_chunks_in_the_run_is_not_listed(env):
    env.fake.add(Video(video_id=VIDEO_ID, title=None, source="pasted", added_by="visitor",
                       raw_transcript=[], raw_paste=None, duration_seconds=None))
    assert env.client.get("/videos").json() == []


def test_the_list_never_contains_transcript_text(env):
    post(env.client, good_body())
    assert "made up words" not in env.client.get("/videos").text


def test_the_list_is_capped_at_200_newest_first(env):
    for i in range(205):
        add_listed(env.fake, f"V{i:010d}")
    body = env.client.get("/videos").json()
    assert len(body) == 200
    assert body[0]["video_id"] == "V0000000204"


def test_hidden_videos_are_removed_before_the_200_cap(env, monkeypatch):
    hidden_ids = [f"H{i:010d}" for i in range(3)]
    monkeypatch.setattr(main, "HIDDEN_IDS", frozenset(hidden_ids))
    for i in range(202):
        add_listed(env.fake, f"V{i:010d}")          # older
    for hid in hidden_ids:
        add_listed(env.fake, hid)                   # newest: they would take the first places
    body = env.client.get("/videos").json()
    ids = [v["video_id"] for v in body]
    assert len(ids) == 200
    assert not set(ids) & set(hidden_ids)
    assert ids[0] == "V0000000201"


@pytest.fixture
def hidden(env, monkeypatch):
    monkeypatch.setattr(main, "HIDDEN_IDS", frozenset({VIDEO_ID}))
    add_listed(env.fake, VIDEO_ID)
    add_listed(env.fake, OTHER_ID, added_by="admin")
    return env


def test_a_hidden_video_is_left_out_of_the_list(hidden):
    ids = [v["video_id"] for v in hidden.client.get("/videos").json()]
    assert VIDEO_ID not in ids
    assert OTHER_ID in ids


def test_progress_for_a_hidden_video_is_404_not_found(hidden):
    r = hidden.client.get(f"/videos/{VIDEO_ID}")
    assert r.status_code == 404
    assert r.json()["status"] == "not_found"


def test_ask_for_a_hidden_video_is_404_and_never_retrieves(hidden, monkeypatch):
    def must_not_run(*args, **kwargs):
        raise AssertionError("a hidden video must not reach retrieval or generation")

    monkeypatch.setattr(main, "_retrieve", must_not_run)
    monkeypatch.setattr(main, "generate", must_not_run)
    r = hidden.client.post("/ask", json={"video": VIDEO_ID, "question": "what is it?"})
    assert r.status_code == 404
    assert "has not been added yet" in r.json()["detail"]


def test_a_hidden_video_cannot_be_pasted_again(hidden):
    r = post(hidden.client, good_body(), token=DUMMY_TOKEN)
    assert r.status_code == 409
    assert "hidden" in r.json()["message"]


def test_hiding_changes_no_chunk_and_deletes_nothing(hidden):
    before = dict(hidden.fake.videos)
    added_before = len(hidden.fake.added)
    hidden.client.get(f"/videos/{VIDEO_ID}")
    hidden.client.get("/videos")
    assert dict(hidden.fake.videos) == before
    assert len(hidden.fake.added) == added_before


# --- storing and resuming (unchanged behaviour) ------------------------------

def test_a_new_paste_is_stored_word_for_word_and_returns_202(env):
    r = post(env.client, good_body())
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "embedding"
    assert body["progress_url"] == f"/videos/{VIDEO_ID}"
    assert body["source"] == "pasted"
    assert body["added_by"] == "visitor"
    assert "send this same request again" in body["message"]
    assert env.state["started"] == [VIDEO_ID]

    stored = env.fake.videos[VIDEO_ID]
    assert isinstance(stored, Video)
    assert stored.raw_paste == TRANSCRIPT          # exactly as pasted
    assert [s["text"] for s in stored.raw_transcript] == ["made up words one", "made up words two"]


def test_resending_the_same_paste_resumes_and_adds_no_second_video(env):
    post(env.client, good_body())
    r = post(env.client, good_body())
    assert r.status_code == 202
    assert len(env.fake.added) == 1
    assert env.state["started"] == [VIDEO_ID, VIDEO_ID]


def test_a_different_paste_for_the_same_video_is_409_and_nothing_changes(env):
    post(env.client, good_body())
    r = post(env.client, good_body("0:00 some other made up words\n"))
    assert r.status_code == 409
    assert env.fake.videos[VIDEO_ID].raw_paste == TRANSCRIPT


def test_a_video_from_captions_cannot_be_overwritten_by_a_paste(env):
    env.fake.videos[VIDEO_ID] = Video(
        video_id=VIDEO_ID, source="captions", raw_transcript=[], raw_paste=None
    )
    assert post(env.client, good_body()).status_code == 409


def test_a_fully_embedded_video_answers_200_ready_and_starts_nothing(env):
    post(env.client, good_body())
    env.state["started"].clear()
    env.state["total"], env.state["embedded"] = 2, 2
    r = post(env.client, good_body())
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    assert env.state["started"] == []


def test_the_response_reports_skipped_chapter_titles(env):
    r = post(env.client, good_body("0:00 one\nChapter 2: Made-up Title\n0:04 two\n"))
    assert r.json()["skipped_chapter_lines"] == 1


# --- input rules (unchanged) --------------------------------------------------

def test_a_body_over_the_limit_is_422_before_it_is_parsed(env):
    raw = b'{"url":"' + b"a" * (main.PASTE_BODY_LIMIT + 10) + b'"}'
    assert post(env.client, raw=raw).status_code == 422


def test_a_body_that_is_not_json_is_422(env):
    assert post(env.client, raw=b"not json").status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"url": URL},
        {"transcript": TRANSCRIPT},
        {"url": 5, "transcript": TRANSCRIPT},
        ["a", "list"],
    ],
)
def test_a_body_with_the_wrong_shape_is_422(env, body):
    assert post(env.client, body).status_code == 422


def test_an_unreadable_url_is_400(env):
    r = post(env.client, {"url": "https://example.com/nothing", "transcript": TRANSCRIPT})
    assert r.status_code == 400


def test_a_transcript_that_breaks_a_rule_is_422_with_the_rule_named(env):
    r = post(env.client, good_body("just prose, no timestamps\n"))
    assert r.status_code == 422
    assert "no timestamps" in r.json()["detail"]


def test_a_transcript_over_the_byte_limit_is_422(env):
    assert post(env.client, good_body("0:00 " + "a" * MAX_PASTE_BYTES)).status_code == 422


# --- progress (unchanged) -----------------------------------------------------

def test_progress_for_an_unknown_video_is_404_not_found(env):
    r = env.client.get(f"/videos/{VIDEO_ID}")
    assert r.status_code == 404
    assert r.json()["status"] == "not_found"


def test_progress_reports_counts_and_never_the_transcript(env):
    post(env.client, good_body())
    env.state["total"], env.state["embedded"] = 2, 1
    env.state["started"].clear()  # nothing running now: a restart happened
    r = env.client.get(f"/videos/{VIDEO_ID}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "incomplete"
    assert (body["chunk_count"], body["embedded_count"]) == (2, 1)
    assert "made up words" not in r.text


def test_progress_while_a_job_is_running_says_embedding(env):
    post(env.client, good_body())
    env.state["total"], env.state["embedded"] = 2, 1
    assert env.client.get(f"/videos/{VIDEO_ID}").json()["status"] == "embedding"


def test_progress_with_a_bad_video_reference_is_400(env):
    assert env.client.get("/videos/bad-id").status_code == 400


def test_a_paste_with_missing_timestamps_is_422_and_nothing_is_stored(env):
    text = "0:00 first words\n" + "".join(f"made up line {i} here\n" for i in range(300))
    r = post(env.client, good_body(text))
    assert r.status_code == 422
    assert "timestamps look missing" in r.json()["detail"]
    assert env.fake.added == []


def test_a_long_copy_whose_hour_labels_do_not_match_is_422_and_nothing_is_stored(env):
    # Made-up lines in a layout these rules do not cover: most h:mm:ss lines
    # keep a digit glued on after the timestamp.
    text = "".join(
        f"1:{i:02d}:151 hour 2 minutes 15 seconds made up words\n" for i in range(3)
    ) + "1:03:001 hour made up words\n"
    r = post(env.client, good_body(text))
    assert r.status_code == 422
    assert r.json()["detail"] == "this copy format isn't supported yet for videos over an hour"
    assert env.fake.added == []
