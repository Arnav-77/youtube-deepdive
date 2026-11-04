"""Saved answers for grounding labels: where they live, and how they are read.

One saved answer is one JSON file in evaluation/answers/ (gitignored, because
the excerpt texts are transcript text). It is written by save_answer.py from
one query_logs row, and read by label_answer.py and score_grounding.py.

The segment hash is how a label is tied to the text it labels. If an answer
file's text ever changes, its segments get new hashes, and the scorer refuses
the old labels instead of silently scoring them against different text.
"""

import hashlib
import json
from pathlib import Path

ANSWERS_DIR = Path(__file__).resolve().parent / "answers"
LABELS_PATH = Path(__file__).resolve().parent / "grounding_labels.yaml"


def segment_hash(text: str) -> str:
    """First 12 hex digits of SHA-256 over the segment's stripped text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def load_answer(path: Path) -> dict:
    """Read one saved answer. Raises if the file is not a saved answer."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("answer", "question", "excerpts", "model", "chunk_ids"):
        if key not in data:
            raise ValueError(f"{path.name} is not a saved answer: no {key!r}")
    return data


def excerpts_by_rank(answer_file: dict) -> dict[int, dict]:
    """Excerpt number (retrieval rank, as the model saw it) to its record."""
    return {int(e["rank"]): e for e in answer_file["excerpts"]}
