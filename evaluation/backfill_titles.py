"""Fill in missing video titles from YouTube's oEmbed endpoint.

Run from the laptop. Only videos whose title is still NULL are looked up, one
request at a time, with a pause between requests (YouTube rate limits, and
CLAUDE.md asks for one video at a time).

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

from sqlalchemy import select

# Running this file puts evaluation/ on sys.path, not the repo root, so `app`
# is found only if the repo root is added here. Same line as the other scripts.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Video
from app.youtube import fetch_title

# Seconds between requests. Sequential, never parallel.
PAUSE_S = 1.0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--write",
        action="store_true",
        help="write titles to the database (default: dry run, print only)",
    )
    return parser.parse_args(argv)


def pending_videos(session):
    """Videos with no title yet, oldest first."""
    statement = (
        select(Video).where(Video.title.is_(None)).order_by(Video.fetched_at)
    )
    return session.scalars(statement).all()


def backfill(session, write, fetch=fetch_title, pause=PAUSE_S, out=print):
    """Look up each pending title. Returns (found, missing)."""
    found = missing = 0
    for index, video in enumerate(pending_videos(session)):
        if index:
            time.sleep(pause)
        title = fetch(video.video_id)
        if title is None:
            missing += 1
            out(f"{video.video_id}  (no title; left NULL)")
            continue
        found += 1
        out(f"{video.video_id}  {title}")
        if write:
            video.title = title
            session.commit()
    return found, missing


def main(argv=None):
    args = parse_args(argv)
    with SessionLocal() as session:
        found, missing = backfill(session, write=args.write)
    print(
        f"\n{found} title(s) found, {missing} without a title."
        + ("" if args.write else " Dry run: nothing written. Run again with --write.")
    )


if __name__ == "__main__":
    main()
