"""Turning a pasted YouTube transcript into the caption list youtube.py makes.

YouTube's "Show transcript" panel can be copied as plain text. Each segment is
a timestamp on one line and its words on the next (or the same line):

    Show transcript
    0:00
    welcome to the course
    0:04
    today we cover hashing

The result is the same list of {text, start, duration} dicts that
fetch_transcript() returns, so chunk_transcript() runs on it unchanged.

Everything here is pure: no database, no network, no clock. The paste is
UNTRUSTED text from the public internet, so every rule below is checked in
code, and a paste that breaks one is refused with PasteError (422 at the
endpoint). Nothing is "cleaned up" silently except blank lines and the
whitespace around each segment.

Timestamps in the panel are whole seconds, so `start` values are integers.
Citations built from them land on the whole second. That is coarser than the
captions API, which gives fractional seconds, and it is why pasted videos are
never added to evaluation/golden_set.yaml.
"""

import re
import unicodedata
from dataclasses import dataclass

# Limits. Checked here, in code, never trusted to the caller.
MAX_PASTE_BYTES = 300_000  # UTF-8 bytes of the transcript text (~2 to 2.5 h)
MAX_SNIPPETS = 5_000
MAX_VIDEO_SECONDS = 12 * 3600

# The panel gives no end time for the final segment. 4.0 s is a fixed estimate;
# chunking reads it only for the last snippet, and duration_seconds is
# approximate for pasted videos.
LAST_SNIPPET_SECONDS = 4.0

# Two layouts are accepted, because the panel has shown both.
#
#   Own line:   "1:02:33" alone on a line, words on the next line(s).
#   Same line:  "0:04 words" or, in the copy checked on 10 Nov, "0:045 words".
#
# The timestamp is h:mm:ss (minutes 00-59) or m:ss (minutes up to 3 digits:
# the panel shows "75:30" for a 75 minute video). Seconds are 00-59. Digits
# glued directly onto the seconds are FRACTIONAL seconds: "0:045" is 4.5 s and
# "10:2345" is 23.45 s. Measured on the 10 Nov sample: the fraction has one or
# two digits, and with it the timestamps only ever go forward.
#
# Groups: 1 hours, 2 minutes after hours, 3 minutes, 4 seconds, 5 fraction
# digits, 6 same-line words.
_TIMESTAMP = re.compile(
    r"^\s*(?:(\d{1,2}):([0-5]\d)|(\d{1,3})):([0-5]\d)(\d*)(?:\s+(.*?))?\s*$"
)

# Chapter titles in the panel, e.g. "Chapter 2: Something". Not speech; skipped.
_CHAPTER = re.compile(r"^\s*Chapter\s+\d+\s*:")


class PasteError(ValueError):
    """The paste breaks a rule. The message is safe to show the caller."""


@dataclass(frozen=True)
class ParsedPaste:
    snippets: list[dict]  # {"text", "start", "duration"}, as to_raw_data() gives
    skipped_leading_lines: int  # panel headings before the first timestamp
    skipped_chapter_lines: int  # "Chapter N:" titles anywhere in the paste


def _check_characters(text: str) -> None:
    """Refuse control characters. NUL alone would make Postgres reject the row.

    Tabs and line breaks are fine: the panel copies with newlines.
    """
    for ch in text:
        if ch in "\n\r\t":
            continue
        if unicodedata.category(ch) == "Cc":
            raise PasteError("the transcript contains a control character")


def _timestamp_seconds(match: re.Match) -> float:
    hours_or_none, minutes_in_hours, minutes_only, seconds, fraction, _ = match.groups()
    if hours_or_none is not None:
        whole = int(hours_or_none) * 3600 + int(minutes_in_hours) * 60 + int(seconds)
    else:
        whole = int(minutes_only) * 60 + int(seconds)
    if not fraction:
        return float(whole)
    return whole + int(fraction) / 10 ** len(fraction)


def parse_paste(text: str) -> ParsedPaste:
    """Parse the copied panel text into caption-shaped snippets.

    Raises PasteError for anything that breaks a limit or a rule:
    too large, a control character, no timestamps at all, timestamps that go
    backwards, more than MAX_SNIPPETS segments, or a video longer than
    MAX_VIDEO_SECONDS.
    """
    if len(text.encode("utf-8")) > MAX_PASTE_BYTES:
        raise PasteError(f"the transcript is larger than {MAX_PASTE_BYTES} bytes")
    _check_characters(text)

    # Each kept segment: [start_seconds, [text parts]]
    segments: list[list] = []
    skipped_leading = 0
    skipped_chapters = 0

    for line in text.splitlines():
        if not line.strip():
            continue
        if _CHAPTER.match(line):
            skipped_chapters += 1
            continue
        match = _TIMESTAMP.match(line)
        if match:
            segments.append([_timestamp_seconds(match), [match.group(6) or ""]])
            continue
        if not segments:
            # Before any timestamp: panel headings such as "Show transcript".
            # Counted and dropped, never merged into a segment.
            skipped_leading += 1
            continue
        segments[-1][1].append(line.strip())

    if not segments:
        raise PasteError("no timestamps found; copy the transcript from the panel")

    # Join each segment's words and drop segments with no words. A timestamp
    # with nothing after it is a gap in the panel, not a snippet.
    kept: list[tuple[int, str]] = []
    previous_start = -1
    for start, parts in segments:
        if start < previous_start:
            raise PasteError("timestamps go backwards; the paste may be out of order")
        if start > MAX_VIDEO_SECONDS:
            raise PasteError("a timestamp is past the 12 hour limit")
        previous_start = start
        words = " ".join(p for p in parts if p).strip()
        if words:
            kept.append((start, words))

    if not kept:
        raise PasteError("the timestamps have no words after them")
    if len(kept) > MAX_SNIPPETS:
        raise PasteError(f"more than {MAX_SNIPPETS} segments")

    snippets = []
    for i, (start, words) in enumerate(kept):
        if i + 1 < len(kept):
            duration = float(kept[i + 1][0] - start)
        else:
            duration = LAST_SNIPPET_SECONDS
        snippets.append({"text": words, "start": float(start), "duration": duration})

    return ParsedPaste(
        snippets=snippets,
        skipped_leading_lines=skipped_leading,
        skipped_chapter_lines=skipped_chapters,
    )


class PasteTooLarge(PasteError):
    """The request body went past the byte limit while it was being read."""


async def read_limited(chunks, limit: int) -> bytes:
    """Collect an async byte stream, stopping as soon as it passes `limit`.

    Used on the request body so an oversized upload is refused while it is
    arriving, not after it has been held in memory. Pydantic's max_length runs
    only after the whole body is read, so it cannot do this job.
    """
    buffer = bytearray()
    async for chunk in chunks:
        buffer.extend(chunk)
        if len(buffer) > limit:
            raise PasteTooLarge(f"the request is larger than {limit} bytes")
    return bytes(buffer)
