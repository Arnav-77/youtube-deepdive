"""Add hand-written relevance labels for one video to evaluation/golden_set.yaml.

Edit VIDEO_ID and QUESTIONS, then run:

    python evaluation/add_labels.py

New questions are appended to the video's existing list. The script refuses if
any of them is already there (same text, ignoring case), so re-running it
cannot duplicate labels.

The file is edited as text rather than re-written through the YAML library,
because re-serialising would reformat every hand-written entry and drop the
comments. The edited text is parsed and checked in memory BEFORE anything is
written: if it does not parse, or if anything other than the new questions
would change, the file is left exactly as it was.
"""

import json
import sys
import textwrap
from pathlib import Path

import yaml

VIDEO_ID = "prWyZhcktn4"

# (question, gold spans in seconds, short quote for the labeller, note)
QUESTIONS = [
    (
        "what are the different categories of machine learning",
        [[80, 212]],
        "and then there is also",
        "Deliberately wide (2 min 12 s): the answer is a list of categories and "
        "the span covers the whole list.",
    ),
    (
        "why do we need a confusion matrix in the first place?",
        [[267, 342]],
        "with an accuracy of 80%",
        "Tests the topic-word pattern: 'confusion matrix' is this video's subject, "
        "likely in most chunks, so it may not discriminate; the other terms are "
        "generic.",
    ),
    (
        "what is sklearn?",
        [[880, 900]],
        "this is not answered very well",
        "PARTIAL-ANSWER CASE for Phase 04 grounding: the video mentions sklearn "
        "but barely explains it. The model knows what sklearn is, so a fluent "
        "answer from its own knowledge is a grounding failure even if true; the "
        "correct answer is limited to what these 20 s say. For retrieval, matches "
        "only if the captions write 'sklearn' as one token.",
    ),
]
PATH = Path(__file__).resolve().parent / "golden_set.yaml"


def find_video(data, video_id):
    for video in data.get("videos") or []:
        if video.get("video_id") == video_id:
            return video
    return None


def question_yaml(question, spans, quote, note):
    """Render one question as YAML lines at the indentation the file uses."""
    spans_yaml = "[" + ", ".join(f"[{a}, {b}]" for a, b in spans) + "]"
    out = f"      - q: {json.dumps(question)}\n"
    out += f"        spans: {spans_yaml}\n"
    if quote:
        out += f"        quote: {json.dumps(quote)}\n"
    out += "        note: >\n"
    for line in textwrap.wrap(note, 68):
        out += f"          {line}\n"
    return out


text = PATH.read_text()
if not text.endswith("\n"):
    text += "\n"

old_data = yaml.safe_load(text)
old_video = find_video(old_data, VIDEO_ID)
if old_video is None:
    sys.exit(f"{VIDEO_ID} is not in the golden set")

old_questions = old_video.get("questions") or []
existing = {str(q.get("q", "")).strip().lower() for q in old_questions}

# Validate every new question before touching the text.
duration = old_video.get("duration_s")
for question, spans, quote, note in QUESTIONS:
    if question.strip().lower() in existing:
        sys.exit(f"refusing: already labelled for {VIDEO_ID}: {question!r}")
    for start, end in spans:
        if not (0 <= start < end) or (duration and end > duration):
            sys.exit(f"bad span [{start}, {end}] for {question!r} (video is {duration}s)")

block = "".join(question_yaml(*q) for q in QUESTIONS)

# Locate this video's entry, and the questions key inside it, as lines of text.
lines = text.splitlines(keepends=True)
start = next(
    (i for i, line in enumerate(lines) if line.rstrip() == f"  - video_id: {VIDEO_ID}"),
    None,
)
if start is None:
    sys.exit(f"could not find the entry line for {VIDEO_ID}; is it indented differently?")

end = next(
    (i for i in range(start + 1, len(lines)) if lines[i].startswith("  - video_id:")),
    len(lines),
)

qline = next(
    (i for i in range(start + 1, end)
     if lines[i].rstrip() in ("    questions:", "    questions: []")),
    None,
)
if qline is None:
    sys.exit(f"{VIDEO_ID} has no 'questions:' key at the expected indentation")

if lines[qline].rstrip() == "    questions: []":
    lines[qline] = "    questions:\n" + block
else:
    # Insert after the last line of the existing list: every line inside
    # the questions list is indented by at least 6 spaces.
    insert_at = qline + 1
    while insert_at < end and lines[insert_at].startswith("      "):
        insert_at += 1
    lines.insert(insert_at, block)

new_text = "".join(lines)

# Prove, in memory, that only the intended change happened.
try:
    new_data = yaml.safe_load(new_text)
except yaml.YAMLError as exc:
    sys.exit(f"edited YAML does not parse; file NOT written:\n{exc}")

new_video = find_video(new_data, VIDEO_ID)
if new_video is None:
    sys.exit(f"{VIDEO_ID} disappeared from the parsed file; file NOT written")
new_questions = new_video.get("questions") or []

if new_questions[: len(old_questions)] != old_questions:
    sys.exit("existing questions would change; file NOT written")
if len(new_questions) != len(old_questions) + len(QUESTIONS):
    sys.exit(f"expected {len(old_questions) + len(QUESTIONS)} questions, "
             f"got {len(new_questions)}; file NOT written")

fields_old = {k: v for k, v in old_video.items() if k != "questions"}
fields_new = {k: v for k, v in new_video.items() if k != "questions"}
if fields_old != fields_new:
    sys.exit(f"{VIDEO_ID}'s other fields would change; file NOT written")

others_old = [v for v in old_data["videos"] if v.get("video_id") != VIDEO_ID]
others_new = [v for v in new_data["videos"] if v.get("video_id") != VIDEO_ID]
if others_old != others_new:
    sys.exit("another video's entry would change; file NOT written")

PATH.write_text(new_text)
print(f"added {len(QUESTIONS)} to {VIDEO_ID}; it now has {len(new_questions)}")
for q in new_questions:
    print(f"  {q['spans']}  {q['q']}")
print("total labelled:", sum(len(v.get("questions") or []) for v in new_data["videos"]))