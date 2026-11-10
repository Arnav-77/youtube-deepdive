"""Tests for the admin gate on POST /videos (the captions path).

Offline: the YouTube fetch is mocked (app.youtube.fetch_transcript), the
database session is a fake, and the embedding and chunk store are patched in
app.main. No network, no database, no Gemini. The token is the dummy value
used by the other route tests.

The refused cases assert the fetch was never called and nothing was stored:
the gate runs before either.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.youtube as youtube
from app.db import get_session
from app.models import Video

DUMMY_TOKEN = "dummy-token-for-tests-only"
VIDEO_ID = "LXaFHI_2Hus"
BODY = {"url": f"https://youtu.be/{VIDEO_ID}"}


class FakeSession:
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


@pytest.fixture
def env(monkeypatch):
    fake = FakeSession()
    fetches = []

    def fake_fetch(video_id):
        fetches.append(video_id)
        return [{"text": "made up words", "start": 0.0, "duration": 2.0}], "captions"

    monkeypatch.setattr(youtube, "fetch_transcript", fake_fetch)
    monkeypatch.setattr(main, "store_chunks", lambda session, video: [])
    monkeypatch.setattr(main, "embed_missing_chunks", lambda session, vid, run: 0)
    monkeypatch.setattr(main, "PASTE_TOKEN", DUMMY_TOKEN)

    main.app.dependency_overrides[get_session] = lambda: fake
    try:
        yield SimpleNamespace(client=TestClient(main.app), fake=fake, fetches=fetches)
    finally:
        main.app.dependency_overrides.clear()


def post(client, token=None):
    headers = {} if token is None else {"X-Paste-Token": token}
    return client.post("/videos", json=BODY, headers=headers)


def assert_refused_without_side_effects(env, response):
    assert response.status_code == 401
    assert env.fetches == []          # no YouTube fetch
    assert env.fake.added == []       # no database write


def test_no_header_is_401_and_nothing_happens(env):
    assert_refused_without_side_effects(env, post(env.client, token=None))


def test_a_wrong_token_is_401_and_nothing_happens(env):
    assert_refused_without_side_effects(env, post(env.client, token="guess"))


def test_an_empty_header_is_401_and_nothing_happens(env):
    assert_refused_without_side_effects(env, post(env.client, token=""))


def test_with_no_token_configured_even_a_header_is_401(env, monkeypatch):
    # Fails closed: unset means nobody is the admin, so the header is no help.
    monkeypatch.setattr(main, "PASTE_TOKEN", "")
    assert_refused_without_side_effects(env, post(env.client, token="anything"))


def test_with_no_token_configured_and_no_header_is_401(env, monkeypatch):
    monkeypatch.setattr(main, "PASTE_TOKEN", "")
    assert_refused_without_side_effects(env, post(env.client, token=None))


def test_the_token_never_appears_in_a_refusal(env):
    assert DUMMY_TOKEN not in post(env.client, token="guess").text


def test_the_right_token_reaches_ingest_and_stores_the_video_as_admin(env):
    response = post(env.client, token=DUMMY_TOKEN)
    assert response.status_code == 200
    assert env.fetches == [VIDEO_ID]          # the mocked YouTube fetch ran once
    stored = env.fake.videos[VIDEO_ID]
    assert isinstance(stored, Video)
    assert stored.added_by == "admin"
    assert stored.source == "captions"


def test_a_token_that_differs_only_in_case_is_refused(env):
    assert_refused_without_side_effects(env, post(env.client, token=DUMMY_TOKEN.upper()))
