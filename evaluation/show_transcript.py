"""Print a video's stored transcript with timestamps, for marking answer spans by hand.

    python evaluation/show_transcript.py LXaFHI_2Hus --start 480 --end 620

Reads videos.raw_transcript (the caption list saved at ingestion) with one
read-only SELECT, and prints each caption as [mm:ss] text.

It shows what is there and nothing more. There is no question argument, no
highlighting, no keyword search, and no chunk ids or scores. It never suggests
a span: a helper that did would grade retrieval against its own output.

Only video ids listed in evaluation/golden_set.yaml are shown. Run it from the
main folder, whose .env points at the dev branch; this worktree has no .env.
"""

import argparse
import sys
from pathlib import Path

import yaml

from app.chunking import _snippet_end

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_set.yaml"
LONG_WINDOW_S = 600


def parse_time(value: str) -> float:
    """Accept seconds ("480") or minutes and seconds ("8:00")."""
    if ":" in value:
        minutes, seconds = value.split(":", 1)
        return float(minutes) * 60 + float(seconds)
    return float(value)


def mmss(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def golden_ids(golden: dict) -> set[str]:
    return {v["video_id"] for v in golden.get("videos") or []}


def format_lines(snippets: list[dict], start: float | None = None, end: float | None = None) -> list[str]:
    """One "[mm:ss] text" line per caption that overlaps the window.

    A caption's end is the next caption's start (or start + duration for the
    last), the same rule chunking uses. A caption is shown if its time range
    overlaps [start, end); with no window, every caption is shown.
    """
    lines = []
    for i, snippet in enumerate(snippets):
        caption_start = float(snippet["start"])
        caption_end = _snippet_end(snippets, i)
        if end is not None and caption_start >= end:
            continue
        if start is not None and caption_end <= start:
            continue
        text = " ".join(str(snippet["text"]).split())
        lines.append(f"[{mmss(caption_start)}] {text}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("video_id")
    parser.add_argument("--start", type=parse_time, default=None, help="seconds or m:ss")
    parser.add_argument("--end", type=parse_time, default=None, help="seconds or m:ss")
    args = parser.parse_args()

    if args.start is not None and args.end is not None and args.end <= args.start:
        sys.exit("--end must be after --start")

    golden = yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8")) or {}
    if args.video_id not in golden_ids(golden):
        sys.exit(f"{args.video_id} is not in golden_set.yaml; not shown")

    # Imported here, not at the top: app.db reads DATABASE_URL at import, and
    # the golden-set check above should run even where there is no database.
    from app.db import SessionLocal
    from app.models import Video

    # Read only. The session is never committed.
    with SessionLocal() as session:
        video = session.get(Video, args.video_id)
        if video is None:
            sys.exit(f"{args.video_id} is not in the database")
        snippets = list(video.raw_transcript or [])

    if not snippets:
        sys.exit(f"{args.video_id} has no stored transcript")

    # Long output is a warning, not a refusal: the person reading decides.
    if args.end is not None:
        span = args.end - (args.start or 0.0)
        if span > LONG_WINDOW_S:
            print(f"note: window is {int(span)} s; read it in parts of {LONG_WINDOW_S} s or less",
                  file=sys.stderr)
    elif args.start is None:
        total = float(snippets[-1]["start"]) + float(snippets[-1]["duration"])
        if total > LONG_WINDOW_S:
            print(f"note: whole transcript is {int(total)} s; use --start and --end to read in parts",
                  file=sys.stderr)

    for line in format_lines(snippets, args.start, args.end):
        print(line)


if __name__ == "__main__":
    main()
