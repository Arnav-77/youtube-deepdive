"""Write the confirmed benchmark videos into the golden set.

Reads each video's real duration and chunk count from the database rather
than from the chat, so the file records what was actually ingested. Any
video already in the YAML is left exactly as it is: the set assignment and
any hand-written labels are the things this script must never touch.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(".env")

from app.db import SessionLocal
from app.models import Video

# (video_id, set, bucket, category, caption_origin, note)
# Sets were assigned as each video was chosen, before any evaluation ran.
VIDEOS = [
    ("p4qPp_3_f2Y", "dev", "long", "rambling", "auto", "Robert Greene x Raj Shamani"),
    ("4Vz6L8B73i4", "dev", "very_long", "rambling", "auto", "Sahar Yousef x Raj Shamani"),
    ("Fa_V9fP2tpU", "dev", "medium", "educational", "auto", "ML concepts explained"),
    ("quyJBb1yWJ8", "dev", "short", "educational", "auto", "easy ML model explained"),
    ("i3AkTO9HLXo", "reported", "short", "jargon", "auto", "markov chains, Normalized Nerd"),
    ("o-jdJxXL_W4", "reported", "short", "jargon", "auto", "markov chains, third of the trio"),
    ("KZeIEiBrT_w", "reported", "medium", "jargon", "auto", "markov chains, longer treatment"),
    ("fNk_zzaMoSs", "reported", "short", "educational", "human", "vectors, 3Blue1Brown"),
    ("cmNIMjPYdgM", "reported", "long", "jargon", "auto", "Stanford CS229"),
    ("JuoVZkPBiKk", "reported", "long", "jargon", "auto", "LLM lecture 1"),
    ("lVynu4bo1rY", "reported", "long", "jargon", "auto", "LLM lecture 3"),
    ("DzpHeXVSC5I", "reported", "long", "jargon", "auto", "CS224N NLP lecture 1"),
    ("Ba6Fn1-Jsfw", "reported", "long", "jargon", "auto", "CS224N NLP lecture 6"),
    ("Ub3GoFaUcds", "reported", "long", "jargon", "auto", "CME295 transformers and LLMs"),
    ("PbXjK-F-5so", "reported", "very_long", "rambling", "auto", "bulletproof mind"),
]

path = Path(__file__).resolve().parent / "golden_set.yaml"
text = path.read_text()
session = SessionLocal()

added, skipped, missing = 0, 0, []

for video_id, which_set, bucket, category, captions, note in VIDEOS:
    if f"video_id: {video_id}" in text:
        skipped += 1
        continue

    video = session.get(Video, video_id)
    if video is None:
        missing.append(video_id)
        continue

    text += (
        f"\n  - video_id: {video_id}\n"
        f"    set: {which_set}\n"
        f"    bucket: {bucket}\n"
        f"    duration_s: {int(video.duration_seconds)}\n"
        f"    category: {category}\n"
        f"    captions: {captions}\n"
        f"    note: {note}\n"
        f"    questions: []\n"
    )
    added += 1

session.close()
path.write_text(text)
print(f"added {added}, already present {skipped}, not ingested {missing}")