"""Ingest the benchmark videos and report what came back.

Run once while the local server is up. Ingestion is idempotent: a video
already stored returns its existing rows rather than writing duplicates, so
re-running this is safe.

Prints, per video: duration, snippet count, chunk count, and the first words
of the transcript, so the language can be checked by eye rather than assumed.
Nothing is written to the golden set here -- that stays a deliberate step.
"""

import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(".env")

from app.db import SessionLocal
from app.models import Video

API = "http://localhost:8000/videos"

# (video_id, set, bucket, note) -- assignments decided before any results.
VIDEOS = [
    ("ukzFI9rgwfU", "reported", "short", "what is machine learning"),
    ("E0Hmnixke2g", "reported", "medium", "all ML algorithms explained"),
    ("9dFhZFUkzuQ", "reported", "medium", "ML vs DL vs AI"),
    ("NUXdtN1W1FE", "reported", "medium", "linear regression analysis"),
    ("prWyZhcktn4", "reported", "medium", "confusion matrix, tensorflow"),
    ("RmajweUFKvM", "reported", "medium", "decision trees"),
    ("4HKqjENq9OU", "reported", "medium", "kNN algorithm"),
]

session = SessionLocal()
print(f"{'video_id':<14}{'set':<10}{'bucket':<11}{'mins':>6}{'chunks':>8}  first words")
print("-" * 110)

for video_id, which_set, bucket, note in VIDEOS:
    try:
        r = requests.post(API, json={"url": f"https://youtu.be/{video_id}"}, timeout=180)
    except Exception as exc:
        print(f"{video_id:<14}{which_set:<10}{bucket:<11}  REQUEST FAILED: {type(exc).__name__}")
        continue

    if r.status_code != 200:
        print(f"{video_id:<14}{which_set:<10}{bucket:<11}  HTTP {r.status_code}: {r.text[:70]}")
        continue

    data = r.json()
    video = session.get(Video, video_id)
    session.expire(video)  # re-read, since rows may have just been written
    sample = " ".join(x["text"] for x in video.raw_transcript[:8])[:52].replace("\n", " ")

    print(f"{video_id:<14}{which_set:<10}{bucket:<11}"
          f"{data['duration_seconds'] / 60:>6.0f}{data['chunk_count']:>8}  {sample}")

    time.sleep(1)  # be polite to YouTube's API between fetches

session.close()