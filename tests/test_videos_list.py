"""GET /videos: the list of added videos the page shows.

No database and no network. A fake session stands in for Postgres and returns
canned rows, so these tests check the route's shape and parameters, not the
SQL's filtering. The filter (only videos with chunks in the current run) runs
on Neon and is checked by hand on the dev branch, not here.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.chunking import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunking_run_label
from app.db import get_session
from app.main import app

FETCHED = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Records the last query and its parameters, returns canned rows."""

    def __init__(self, rows):
        self._rows = rows
        self.statement = None
        self.params = None

    def execute(self, statement, params=None):
        self.statement = statement
        self.params = params
        return _FakeResult(self._rows)


ROWS = [
    {
        "video_id": "LXaFHI_2Hus",
        "title": None,
        "duration_seconds": 754,
        "source": "captions",
        "fetched_at": FETCHED,
        "added_by": None,
    },
    {
        "video_id": "dQw4w9WgXcQ",
        "title": "Some title",
        "duration_seconds": None,
        "source": "captions",
        "fetched_at": FETCHED,
        "added_by": None,
    },
]


@pytest.fixture
def fake_session():
    """Install a fake session for GET /videos, and always remove it afterwards."""
    session = _FakeSession(ROWS)
    app.dependency_overrides[get_session] = lambda: session
    yield session
    app.dependency_overrides.pop(get_session, None)


@pytest.fixture
def client():
    return TestClient(app)


def test_lists_videos_with_the_summary_fields(client, fake_session):
    response = client.get("/videos")

    assert response.status_code == 200
    body = response.json()
    assert [row["video_id"] for row in body] == ["LXaFHI_2Hus", "dQw4w9WgXcQ"]
    assert body[0] == {
        "video_id": "LXaFHI_2Hus",
        "title": None,
        "duration_seconds": 754,
        "source": "captions",
        "fetched_at": "2026-11-01T12:00:00Z",
        "added_by": "captions",
        "checked": True,
    }


def test_never_returns_transcript_or_chunk_text(client, fake_session):
    body = client.get("/videos").json()

    for row in body:
        assert set(row) == {
            "video_id", "title", "duration_seconds", "source", "fetched_at", "added_by", "checked",
        }
        assert "raw_transcript" not in row
        assert "text" not in row


def test_filters_on_the_chunking_run_that_ask_searches(client, fake_session):
    client.get("/videos")

    expected_run = chunking_run_label(DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP)
    assert fake_session.params == {"run": expected_run, "n": 200}
    sql = str(fake_session.statement)
    assert "EXISTS" in sql
    assert "chunking_run = :run" in sql


def test_empty_database_gives_an_empty_list(client):
    session = _FakeSession([])
    app.dependency_overrides[get_session] = lambda: session
    try:
        response = client.get("/videos")
    finally:
        app.dependency_overrides.pop(get_session, None)

    assert response.status_code == 200
    assert response.json() == []


def test_post_videos_is_still_registered_beside_the_get():
    methods_for_videos = {
        method
        for route in app.routes
        if getattr(route, "path", None) == "/videos"
        for method in getattr(route, "methods", set())
    }
    assert {"GET", "POST"} <= methods_for_videos
