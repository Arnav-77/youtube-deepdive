"""Tests for POST /videos/paste and GET /videos/{id}.

Offline: the database session is a fake, and the store, progress and job
functions are patched in app.main. No Gemini, no network, no database.
The token below is a dummy value that exists only in this file.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.db import get_session
from app.models import Video
from app.paste import MAX_PASTE_BYTES

DUMMY_TOKEN = "dummy-token-for-tests-only"
VIDEO_ID = "LXaFHI_2Hus"
URL = f"https://youtu.be/{VIDEO_ID}"
# Made-up words only.
TRANSCRIPT = "0:00 made up words one\n0:04 made up words two\n"


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class FakeSession:
    """Just enough session for _store_paste and the progress route."""

    def __init__(self):
        self.videos = {}
        self.added = []

    def get(self, model, key):
        return self.videos.get(key)

    def add(self, obj):
        self.added.append(obj)
        self.videos[obj.video_id] = obj

    def commit(self):
        pass

    def rollback(self):
        pass

    def execute(self, statement, params=None):
        # The only raw SQL the progress route runs is the source lookup.
        assert "SELECT source" in str(statement)
        video = self.videos.get(params["v"])
        return _Result(video.source if video is not None else None)


@pytest.fixture
def env(monkeypatch):
    fake = FakeSession()
    state = {"total": 0, "embedded": 0, "started": []}

    monkeypatch.setattr(main, "PASTE_TOKEN", DUMMY_TOKEN)
    monkeypatch.setattr(main, "store_chunks", lambda session, video: [])
    monkeypatch.setattr(main, "progress", lambda session, vid, run: (state["total"], state["embedded"]))
    monkeypatch.setattr(main, "start_embedding", lambda vid, run: state["started"].append(vid) or True)
    monkeypatch.setattr(main, "is_running", lambda vid: vid in state["started"])

    main.app.dependency_overrides[get_session] = lambda: fake
    try:
        yield SimpleNamespace(client=TestClient(main.app), fake=fake, state=state)
    finally:
        main.app.dependency_overrides.clear()


def post(client, body=None, token=DUMMY_TOKEN, raw=None):
    headers = {} if token is None else {"X-Paste-Token": token}
    if raw is not None:
        return client.post(
            "/videos/paste", content=raw,
            headers={**headers, "content-type": "application/json"},
        )
    return client.post("/videos/paste", json=body, headers=headers)


def good_body(transcript=TRANSCRIPT):
    return {"url": URL, "transcript": transcript}


# --- the gate: disabled, missing, wrong ---------------------------------------

def test_paste_is_503_when_no_token_is_configured(env, monkeypatch):
    monkeypatch.setattr(main, "PASTE_TOKEN", "")
    r = post(env.client, good_body())
    assert r.status_code == 503
    assert r.json()["detail"] == "paste is disabled on this server"
    assert env.fake.added == []


def test_503_comes_before_the_token_check(env, monkeypatch):
    monkeypatch.setattr(main, "PASTE_TOKEN", "")
    assert post(env.client, good_body(), token=DUMMY_TOKEN).status_code == 503


def test_missing_token_is_401(env):
    r = post(env.client, good_body(), token=None)
    assert r.status_code == 401
    assert env.fake.added == []


def test_wrong_token_is_401_and_nothing_is_stored(env):
    r = post(env.client, good_body(), token="guess")
    assert r.status_code == 401
    assert env.fake.added == []


def test_the_token_never_appears_in_any_response(env):
    responses = [
        post(env.client, good_body()),                  # 202
        post(env.client, good_body()),                  # 202 again (resume)
        post(env.client, good_body("0:00 different\n")),  # 409
        post(env.client, good_body(), token="guess"),   # 401
        post(env.client, {"url": "nope", "transcript": "x"}),  # 400
        env.client.get(f"/videos/{VIDEO_ID}"),          # progress
        env.client.get("/videos/bad-id"),               # 400
    ]
    for r in responses:
        assert DUMMY_TOKEN not in r.text


# --- body and input rules -----------------------------------------------------

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
    r = post(env.client, good_body("0:00 " + "a" * MAX_PASTE_BYTES))
    assert r.status_code == 422


# --- storing and resuming -----------------------------------------------------

def test_a_new_paste_is_stored_word_for_word_and_returns_202(env):
    r = post(env.client, good_body())
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "embedding"
    assert body["progress_url"] == f"/videos/{VIDEO_ID}"
    assert body["source"] == "pasted"
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
    r = post(env.client, good_body())
    assert r.status_code == 409


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


# --- progress -----------------------------------------------------------------

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
