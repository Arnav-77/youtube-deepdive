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
# A real panel segment is a line or two of speech, a few hundred characters at
# most. Unreadable lines are joined to the segment before them, so a paste whose
# timestamps are missing would otherwise become one huge segment. Over this
# length, the paste is refused rather than stored with one giant snippet.
MAX_SEGMENT_CHARS = 1_000
MAX_VIDEO_SECONDS = 12 * 3600

# The panel gives no end time for the final segment. 4.0 s is a fixed estimate;
# chunking reads it only for the last snippet, and duration_seconds is
# approximate for pasted videos.
LAST_SNIPPET_SECONDS = 4.0

# A timestamp is h:mm:ss (minutes 00-59) or m:ss (minutes up to 3 digits: the
# panel shows "75:30" for a 75 minute video). Seconds are always two digits,
# 00-59, and must be followed by whitespace, by more digits, or by the end of
# the line. Everything after the seconds is kept in the "rest" group.
#
# The panel also writes the time again in words, GLUED to the timestamp with no
# space: "2:42" is followed by "2 minutes, 42 seconds" and so reads "2:422
# minutes, 42 ...". That spelled-out time is removed by _strip_spelled, and only
# when it matches the timestamp's own digits exactly. Digits are never read as a
# fraction of a second: every start is a whole second.
#
# Groups: 1 hours, 2 minutes after hours, 3 minutes (m:ss), 4 seconds, 5 rest.
_TIMESTAMP = re.compile(
    r"^\s*(?:(\d{1,2}):([0-5]\d)|(\d{1,3})):([0-5]\d)(?=\s|\d|$)(.*?)\s*$"
)


def _spelled_candidates(minutes: int, seconds: int) -> list[tuple[str, bool]]:
    """The spelled-out forms the panel may write, built from the timestamp's digits.

    Each is (text, ends_with_unit). Longest first, so "2 minutes, 42 seconds" is
    tried before "2 minutes, 42". "1" takes the singular unit. A form with no
    minutes is the under-a-minute form, which the panel writes as the seconds
    digits alone (with or without the unit word).
    """
    minute_word = "minute" if minutes == 1 else "minutes"
    second_word = "second" if seconds == 1 else "seconds"
    if minutes and seconds:
        return [
            (f"{minutes} {minute_word}, {seconds} {second_word}", True),
            (f"{minutes} {minute_word}, {seconds}", False),
        ]
    if minutes:
        return [(f"{minutes} {minute_word}", True)]
    return [(f"{seconds} {second_word}", True), (f"{seconds}", False)]


def _strip_spelled(minutes: int, seconds: int, rest: str) -> str:
    """Remove the spelled-out time glued to a timestamp, if it is exactly that.

    Only text that starts right after the timestamp, with no space, is a
    candidate. The match is case-insensitive. A form ending in a bare number
    must not be followed by a digit (that would make a longer number). After a
    unit word the speech may follow at once, letters or digits, because the
    panel sometimes writes it straight on with no space. Anything that does not match is returned unchanged: it is speech,
    and it is never read as a number.
    """
    if not rest or rest[0].isspace():
        return rest.lstrip()
    for candidate, ends_with_unit in _spelled_candidates(minutes, seconds):
        head = rest[: len(candidate)]
        if head.lower() == candidate.lower():
            after = rest[len(candidate):]
            # A bare number must not be followed by a digit: that would be a longer
            # number. After a unit word, speech may start at once, digits included.
            if ends_with_unit or not after[:1].isdigit():
                return after.lstrip()
    return rest


def _read_timestamp(match: re.Match) -> tuple[float, str]:
    """(start in whole seconds, the words after the timestamp with any glued spelled time removed)."""
    hours, minutes_in_hours, minutes_only, seconds_text, rest = match.groups()
    seconds = int(seconds_text)
    if hours is not None:
        # The panel's spelled form for h:mm:ss has no sample to check against,
        # so nothing is removed here. The rest stays as written, digits included.
        start = int(hours) * 3600 + int(minutes_in_hours) * 60 + seconds
        return float(start), rest.lstrip()
    minutes = int(minutes_only)
    start = minutes * 60 + seconds
    return float(start), _strip_spelled(minutes, seconds, rest)


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
            start, words = _read_timestamp(match)
            segments.append([start, [words]])
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
        if len(words) > MAX_SEGMENT_CHARS:
            raise PasteError(
                "the timestamps look missing: one stretch of text runs past "
                f"{MAX_SEGMENT_CHARS:,} characters without a new timestamp. "
                "Copy the transcript again, with its timestamps."
            )
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
