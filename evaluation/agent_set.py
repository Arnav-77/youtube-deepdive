"""Checks for evaluation/golden_set_agent.yaml, the AI-written question set.

    python evaluation/agent_set.py          # prints problems, exit 1 if any

Pure apart from reading the two YAML files: no database, no network.

The AI-written set is kept apart from Arnav's golden_set.yaml on purpose:
its questions and spans were written by Claude Code, not by a person, so
every number computed from it is reported as AI-labelled and never mixed
with the human-labelled numbers.

Rules checked:
  - every question has author: claude-code and a type of speaker_words,
    paraphrase or negative; each video has exactly one of each;
  - every video is in golden_set.yaml, with the same set and duration_s
    (a video never moves between dev and reported);
  - positive spans are [start, end] with 0 <= start < end <= duration_s;
    negative questions have spans: [];
  - no question repeats a human question, and no question is repeated.
"""

import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
AGENT_PATH = HERE / "golden_set_agent.yaml"
GOLDEN_PATH = HERE / "golden_set.yaml"
AUTHOR = "claude-code"
TYPES = ("speaker_words", "paraphrase", "negative")


def norm(q: str) -> str:
    return " ".join(str(q).split()).lower()


def validate(agent: dict, golden: dict) -> list[str]:
    """Every problem found, as text. Empty list means the file is usable. Pure."""
    problems = []
    human = {v["video_id"]: v for v in golden.get("videos") or []}
    human_qs = {norm(q["q"]) for v in human.values() for q in v.get("questions") or []}
    seen_q, seen_v = set(), set()

    for video in agent.get("videos") or []:
        vid = video.get("video_id")
        if vid in seen_v:
            problems.append(f"{vid}: listed twice")
        seen_v.add(vid)
        if vid not in human:
            problems.append(f"{vid}: not in golden_set.yaml")
            continue
        ref = human[vid]
        if video.get("set") != ref.get("set"):
            problems.append(f"{vid}: set {video.get('set')!r} differs from golden_set.yaml")
        duration = ref.get("duration_s")
        if video.get("duration_s") != duration:
            problems.append(f"{vid}: duration_s differs from golden_set.yaml")

        questions = video.get("questions") or []
        types = sorted(q.get("type") for q in questions)
        if types != sorted(TYPES):
            problems.append(f"{vid}: needs one question of each type, has {types}")

        for q in questions:
            text = q.get("q") or ""
            label = f"{vid} {text[:40]!r}"
            if q.get("author") != AUTHOR:
                problems.append(f"{label}: author must be {AUTHOR}")
            if q.get("type") not in TYPES:
                problems.append(f"{label}: type {q.get('type')!r} not allowed")
            if norm(text) in human_qs:
                problems.append(f"{label}: repeats a human question")
            if norm(text) in seen_q:
                problems.append(f"{label}: repeated")
            seen_q.add(norm(text))

            spans = q.get("spans")
            if not isinstance(spans, list):
                problems.append(f"{label}: spans must be a list")
                continue
            if q.get("type") == "negative":
                if spans:
                    problems.append(f"{label}: a negative question has spans: []")
                continue
            if not spans:
                problems.append(f"{label}: a positive question needs a span")
            for span in spans:
                ok = (isinstance(span, list) and len(span) == 2
                      and all(isinstance(x, (int, float)) for x in span))
                if not ok:
                    problems.append(f"{label}: span {span!r} is not [start, end]")
                    continue
                start, end = span
                if not (0 <= start < end) or (duration is not None and end > duration):
                    problems.append(f"{label}: span {span} outside 0..{duration} or empty")
    return problems


def main() -> None:
    agent = yaml.safe_load(AGENT_PATH.read_text(encoding="utf-8")) or {}
    golden = yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8")) or {}
    problems = validate(agent, golden)
    for p in problems:
        print(f"  - {p}")
    n = sum(len(v.get("questions") or []) for v in agent.get("videos") or [])
    print(f"{len(agent.get('videos') or [])} videos, {n} questions, {len(problems)} problems")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
