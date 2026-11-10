"""Fetching YouTube transcripts and storing them.

Captions path only. The Whisper fallback for uncaptioned videos is a
separate branch, added after this one works -- it's slower, rate limited,
and debugging both transcription paths at once means you can't tell which
layer is broken.
"""

import re
from typing import NamedTuple

import httpx
from sqlalchemy.orm import Session
from youtube_transcript_api import YouTubeTranscriptApi

from app.models import Video

# YouTube's public oEmbed endpoint: returns a video's title as JSON, no key.
OEMBED_URL = "https://www.youtube.com/oembed"
OEMBED_TIMEOUT_S = 5.0

# Matches a YouTube video id: exactly 11 chars of letters, digits,
# underscore and hyphen. YouTube ids are opaque strings, so we match on
# shape rather than trying to validate meaning.
_VIDEO_ID = r"([0-9A-Za-z_-]{11})"

# Inside a URL the id must also END after 11 characters: without this, a
# 12-character typo matched its first 11 and returned a wrong id silently.
_URL_ID = _VIDEO_ID + r"(?![0-9A-Za-z_-])"

# Users paste whichever URL form they happened to copy, so all three are
# handled. Order matters only in that each pattern is anchored enough not
# to match the others by accident.
_URL_PATTERNS = [
    # https://www.youtube.com/watch?v=LXaFHI_2Hus&t=42
    re.compile(r"[?&]v=" + _URL_ID),
    # https://youtu.be/LXaFHI_2Hus?si=...   (the share-button form)
    re.compile(r"youtu\.be/" + _URL_ID),
    # https://www.youtube.com/embed/LXaFHI_2Hus
    re.compile(r"/embed/" + _URL_ID),
    # https://www.youtube.com/shorts/LXaFHI_2Hus
    re.compile(r"/shorts/" + _URL_ID),
]

# A bare id pasted on its own, e.g. "LXaFHI_2Hus". Anchored at both ends so
# it can't match a fragment of something longer.
_BARE_ID = re.compile(r"^" + _VIDEO_ID + r"$")


def extract_video_id(url_or_id: str) -> str:
    """Pull the 11-character video id out of whatever the user pasted.

    Raises ValueError rather than returning None. A None here would travel
    silently into a database lookup and fail three layers away from the
    actual problem.
    """
    value = url_or_id.strip()

    match = _BARE_ID.match(value)
    if match:
        return match.group(1)

    for pattern in _URL_PATTERNS:
        match = pattern.search(value)
        if match:
            return match.group(1)

    raise ValueError(f"Could not find a YouTube video id in: {url_or_id!r}")


def fetch_transcript(video_id: str) -> tuple[list[dict], str]:
    """Fetch captions for a video.

    Returns the raw snippet list and which path produced it ("captions").
    The second element exists so the Whisper branch can return "whisper"
    later and the caller doesn't need to care which ran.

    The library returns FetchedTranscriptSnippet objects, which are not
    JSON-serialisable. to_raw_data() converts to plain dicts -- used rather
    than hand-building them so that if the library adds a field, we get it
    without changing this code.
    """
    transcript = YouTubeTranscriptApi().fetch(video_id)
    return transcript.to_raw_data(), "captions"


class TitleLookup(NamedTuple):
    """The outcome of one oEmbed lookup.

    `status` says what happened, in a form a person can act on: "HTTP 404",
    "ConnectError", "no title in response". It is "ok" when `title` is set.
    """

    title: str | None
    status: str


def lookup_title(video_id: str) -> TitleLookup:
    """Ask YouTube's oEmbed endpoint for a video's title. Never raises.

    A title is decoration: any failure (network, a private or deleted video,
    an unexpected response) gives a None title and a status saying why. The
    reason also goes to stdout, where Render's logs show it.
    """
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        response = httpx.get(
            OEMBED_URL,
            params={"url": watch_url, "format": "json"},
            timeout=OEMBED_TIMEOUT_S,
        )
        if response.status_code != 200:
            status = f"HTTP {response.status_code}"
            print(f"title lookup for {video_id} returned {status}")
            return TitleLookup(None, status)
        title = response.json().get("title")
    except Exception as exc:
        status = type(exc).__name__
        print(f"title lookup for {video_id} failed: {status}")
        return TitleLookup(None, status)
    if isinstance(title, str) and title.strip():
        return TitleLookup(title.strip(), "ok")
    print(f"title lookup for {video_id}: response had no title")
    return TitleLookup(None, "no title in response")


def fetch_title(video_id: str) -> str | None:
    """The title alone, or None. Ingestion uses this; the backfill uses lookup_title."""
    return lookup_title(video_id).title


def ingest_video(session: Session, url_or_id: str) -> Video:
    """Fetch a video's transcript and store it. Returns the stored row.

    If we already have the video, returns the existing row without
    re-fetching. This is not an optimisation -- the 20 benchmark videos get
    queried hundreds of times during evaluation runs, and the transcript
    API is rate limited and will start refusing us.
    """
    video_id = extract_video_id(url_or_id)

    existing = session.get(Video, video_id)
    if existing:
        return existing

    raw_transcript, source = fetch_transcript(video_id)

    # Title from oEmbed. Never fails ingestion: fetch_title returns None on any error.
    title = fetch_title(video_id)

    # Duration = where the last snippet ends. Approximate, because caption
    # timings don't necessarily run to the end of the video, but good
    # enough for "roughly how long is this" and it costs no extra API call.
    duration = 0
    if raw_transcript:
        last = raw_transcript[-1]
        duration = int(last["start"] + last["duration"])

    video = Video(
        video_id=video_id,
        title=title,
        source=source,
        raw_transcript=raw_transcript,
        duration_seconds=duration,
    )
    session.add(video)
    session.commit()
    return video