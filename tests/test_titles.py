"""Video titles from YouTube's oEmbed endpoint, and their use at ingest.

Offline: httpx.get is replaced by a fake, and the transcript fetch and the
database session are fakes too. A title is decoration, so every failure
here must leave the title NULL and must never stop ingestion.
"""

import httpx
import pytest

import app.youtube as youtube
from app.youtube import fetch_title, ingest_video

VIDEO_ID = "LXaFHI_2Hus"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, bad_json=False):
        self.status_code = status_code
        self._payload = payload
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


def _fake_get(response=None, error=None, calls=None):
    def get(url, params=None, timeout=None):
        if calls is not None:
            calls.append({"url": url, "params": params, "timeout": timeout})
        if error is not None:
            raise error
        return response

    return get


# --- fetch_title -----------------------------------------------------------


def test_returns_the_title_from_oembed(monkeypatch):
    calls = []
    response = _FakeResponse(payload={"title": "Everest base camp", "author_name": "x"})
    monkeypatch.setattr(youtube.httpx, "get", _fake_get(response, calls=calls))

    assert fetch_title(VIDEO_ID) == "Everest base camp"
    # Asks oEmbed about the watch page, as JSON, with a timeout.
    assert calls[0]["url"] == "https://www.youtube.com/oembed"
    assert calls[0]["params"] == {
        "url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
        "format": "json",
    }
    assert calls[0]["timeout"] == youtube.OEMBED_TIMEOUT_S


def test_strips_spaces_around_the_title(monkeypatch):
    monkeypatch.setattr(
        youtube.httpx, "get", _fake_get(_FakeResponse(payload={"title": "  Padded  "}))
    )
    assert fetch_title(VIDEO_ID) == "Padded"


@pytest.mark.parametrize("status", [401, 404, 500])
def test_non_200_gives_none(monkeypatch, capsys, status):
    monkeypatch.setattr(youtube.httpx, "get", _fake_get(_FakeResponse(status_code=status)))

    assert fetch_title(VIDEO_ID) is None
    assert f"HTTP {status}" in capsys.readouterr().out


def test_network_error_gives_none_and_says_why(monkeypatch, capsys):
    monkeypatch.setattr(
        youtube.httpx, "get", _fake_get(error=httpx.ConnectError("refused"))
    )

    assert fetch_title(VIDEO_ID) is None
    assert "ConnectError" in capsys.readouterr().out


def test_timeout_gives_none(monkeypatch):
    monkeypatch.setattr(
        youtube.httpx, "get", _fake_get(error=httpx.ReadTimeout("slow"))
    )
    assert fetch_title(VIDEO_ID) is None


def test_response_that_is_not_json_gives_none(monkeypatch):
    monkeypatch.setattr(
        youtube.httpx, "get", _fake_get(_FakeResponse(bad_json=True))
    )
    assert fetch_title(VIDEO_ID) is None


@pytest.mark.parametrize(
    "payload", [{}, {"title": None}, {"title": ""}, {"title": "   "}, {"title": 42}, ["x"]]
)
def test_missing_or_blank_title_gives_none(monkeypatch, payload):
    monkeypatch.setattr(youtube.httpx, "get", _fake_get(_FakeResponse(payload=payload)))
    assert fetch_title(VIDEO_ID) is None


# --- lookup_title: the status says why a lookup failed ----------------------


def test_lookup_reports_ok_and_the_title(monkeypatch):
    monkeypatch.setattr(
        youtube.httpx, "get", _fake_get(_FakeResponse(payload={"title": "A title"}))
    )
    assert youtube.lookup_title(VIDEO_ID) == youtube.TitleLookup("A title", "ok")


@pytest.mark.parametrize("status", [401, 404])
def test_lookup_reports_the_http_status(monkeypatch, status):
    monkeypatch.setattr(youtube.httpx, "get", _fake_get(_FakeResponse(status_code=status)))
    assert youtube.lookup_title(VIDEO_ID) == youtube.TitleLookup(None, f"HTTP {status}")


def test_lookup_reports_the_exception_name_when_there_is_no_http_status(monkeypatch):
    monkeypatch.setattr(
        youtube.httpx, "get", _fake_get(error=httpx.ConnectError("refused"))
    )
    assert youtube.lookup_title(VIDEO_ID) == youtube.TitleLookup(None, "ConnectError")


def test_lookup_reports_a_response_without_a_title(monkeypatch):
    monkeypatch.setattr(youtube.httpx, "get", _fake_get(_FakeResponse(payload={})))
    assert youtube.lookup_title(VIDEO_ID) == youtube.TitleLookup(
        None, "no title in response"
    )


# --- ingest_video ----------------------------------------------------------


class _FakeSession:
    """Just enough of a SQLAlchemy session for ingest_video."""

    def __init__(self):
        self.added = []
        self.commits = 0

    def get(self, model, key):
        return None  # the video is not stored yet

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1


SNIPPETS = [{"text": "hello", "start": 0.0, "duration": 4.0}]


@pytest.fixture
def fake_transcript(monkeypatch):
    monkeypatch.setattr(youtube, "fetch_transcript", lambda video_id: (SNIPPETS, "captions"))


def test_ingest_stores_the_oembed_title(monkeypatch, fake_transcript):
    monkeypatch.setattr(
        youtube.httpx, "get", _fake_get(_FakeResponse(payload={"title": "A title"}))
    )
    session = _FakeSession()

    video = ingest_video(session, VIDEO_ID)

    assert video.title == "A title"
    assert session.added == [video]
    assert session.commits == 1


def test_ingest_keeps_going_with_a_null_title_when_oembed_fails(monkeypatch, fake_transcript):
    monkeypatch.setattr(
        youtube.httpx, "get", _fake_get(error=httpx.ConnectError("refused"))
    )
    session = _FakeSession()

    video = ingest_video(session, VIDEO_ID)

    assert video.title is None
    assert video.video_id == VIDEO_ID
    assert video.raw_transcript == SNIPPETS
    assert session.commits == 1


def test_a_title_failure_never_stops_ingest(monkeypatch, fake_transcript):
    # Even an unexpected exception inside the lookup must not reach ingest.
    def boom(url, params=None, timeout=None):
        raise RuntimeError("anything at all")

    monkeypatch.setattr(youtube.httpx, "get", boom)
    session = _FakeSession()

    video = ingest_video(session, VIDEO_ID)

    assert video.title is None
    assert session.commits == 1
