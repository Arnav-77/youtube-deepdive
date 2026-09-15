"""Fetching YouTube transcripts and storing them.

Captions path only. The Whisper fallback for uncaptioned videos is a
separate branch, added after this one works -- it's slower, rate limited,
and debugging both transcription paths at once means you can't tell which
layer is broken.
"""

import re

from sqlalchemy.orm import Session
from youtube_transcript_api import YouTubeTranscriptApi

from app.models import Video

# Matches a YouTube video id: exactly 11 chars of letters, digits,
# underscore and hyphen. YouTube ids are opaque strings, so we match on
# shape rather than trying to validate meaning.
_VIDEO_ID = r"([0-9A-Za-z_-]{11})"

# Users paste whichever URL form they happened to copy, so all three are
# handled. Order matters only in that each pattern is anchored enough not
# to match the others by accident.
_URL_PATTERNS = [
    # https://www.youtube.com/watch?v=LXaFHI_2Hus&t=42
    re.compile(r"[?&]v=" + _VIDEO_ID),
    # https://youtu.be/LXaFHI_2Hus?si=...   (the share-button form)
    re.compile(r"youtu\.be/" + _VIDEO_ID),
    # https://www.youtube.com/embed/LXaFHI_2Hus
    re.compile(r"/embed/" + _VIDEO_ID),
    # https://www.youtube.com/shorts/LXaFHI_2Hus
    re.compile(r"/shorts/" + _VIDEO_ID),
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

    # Duration = where the last snippet ends. Approximate, because caption
    # timings don't necessarily run to the end of the video, but good
    # enough for "roughly how long is this" and it costs no extra API call.
    duration = 0
    if raw_transcript:
        last = raw_transcript[-1]
        duration = int(last["start"] + last["duration"])

    video = Video(
        video_id=video_id,
        # Title is not available from the transcript API. Left null rather
        # than pulling in another dependency to scrape it -- revisit if the
        # frontend needs it.
        title=None,
        source=source,
        raw_transcript=raw_transcript,
        duration_seconds=duration,
    )
    session.add(video)
    session.commit()
    return video