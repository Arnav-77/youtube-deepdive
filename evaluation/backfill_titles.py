"""Fill in missing video titles: YouTube's oEmbed endpoint first, then a hand-written file.

Run from the laptop. Only videos whose title is still NULL are looked up, one
request at a time, with a pause between requests (YouTube rate limits, and
CLAUDE.md asks for one video at a time).

For each video, oEmbed is asked first. If it gives no title, the video is
looked up in evaluation/video_titles.yaml (id: title), written by hand. A
video that neither source can title is listed at the end with the reason
oEmbed gave, so you know what to add to that file.

Dry run by default: prints each id and title and writes nothing.

    python evaluation/backfill_titles.py          # dry run
    python evaluation/backfill_titles.py --write  # writes titles to the database .env points at

Titles are the only thing this script writes, and only to videos.title. It
never touches chunks, and it commits after each title, so an interrupted run
keeps what it found.
"""

import argparse
import sys
import time
from pathlib import Path

import yaml
from sqlalchemy import select

# Running this file puts evaluation/ on sys.path, not the repo root, so `app`
# is found only if the repo root is added here. Same line as the other scripts.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Video
from app.youtube import lookup_title

# Seconds between requests. Sequential, never parallel.
PAUSE_S = 1.0

OVERRIDE_FILE = Path(__file__).resolve().parent / "video_titles.yaml"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--write",
        action="store_true",
        help="write titles to the database (default: dry run, print only)",
    )
    return parser.parse_args(argv)


def load_overrides(path=OVERRIDE_FILE):
    """Hand-written titles, {id: title}. A missing or empty file means none.

    Raises ValueError on a bad entry, so a typo stops the run before anything
    is written.
    """
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected lines of 'video_id: title'")
    overrides = {}
    for video_id, title in data.items():
        if not isinstance(video_id, str):
            raise ValueError(f"{path.name}: {video_id!r} is not a video id (quote it)")
        if not isinstance(title, str) or not title.strip():
            raise ValueError(
                f"{path.name}: {video_id} needs a non-empty title "
                "(quote it if it is a number or yes/no/on/off)"
            )
        overrides[video_id] = title.strip()
    return overrides


def pending_videos(session):
    """Videos with no title yet, oldest first."""
    statement = (
        select(Video).where(Video.title.is_(None)).order_by(Video.fetched_at)
    )
    return session.scalars(statement).all()


def backfill(session, write, lookup=lookup_title, overrides=None, pause=PAUSE_S, out=print):
    """Find a title for each video that has none.

    oEmbed first. A hand-written override is used only when oEmbed gives no
    title. Returns a summary: how many came from oEmbed, how many from the
    override file, and which ids failed, each with its status.
    """
    overrides = overrides or {}
    summary = {"found": 0, "overridden": 0, "failed": []}
    for index, video in enumerate(pending_videos(session)):
        if index:
            time.sleep(pause)
        looked_up = lookup(video.video_id)
        if looked_up.title is not None:
            title, source = looked_up.title, "oEmbed"
            summary["found"] += 1
        elif video.video_id in overrides:
            title, source = overrides[video.video_id], "video_titles.yaml"
            summary["overridden"] += 1
        else:
            summary["failed"].append((video.video_id, looked_up.status))
            out(f"{video.video_id}  FAILED: {looked_up.status}")
            continue
        out(f"{video.video_id}  {title}  [{source}]")
        if write:
            video.title = title
            session.commit()
    return summary


def main(argv=None):
    args = parse_args(argv)
    overrides = load_overrides()  # a bad file stops the run before any lookup
    with SessionLocal() as session:
        summary = backfill(session, write=args.write, overrides=overrides)
    print(
        f"\n{summary['found']} from oEmbed, {summary['overridden']} from "
        f"{OVERRIDE_FILE.name}, {len(summary['failed'])} still without a title."
    )
    if summary["failed"]:
        print(f"Still without a title. Add these to evaluation/{OVERRIDE_FILE.name}:")
        for video_id, status in summary["failed"]:
            print(f"  {video_id}  {status}")
    if not args.write:
        print("Dry run: nothing written. Run again with --write.")


if __name__ == "__main__":
    main()
