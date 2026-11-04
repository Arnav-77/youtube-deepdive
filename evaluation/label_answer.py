"""Prepare one saved answer for hand labelling.

    python evaluation/label_answer.py evaluation/answers/row26.json

Prints each segment with its index and hash, next to the text of the excerpts
it cites. Then appends a skeleton block to evaluation/grounding_labels.yaml,
with the index, citations and hash filled in and `refused` and each `label`
left blank.

This script never fills a label or a refusal. Those are judgements, and
they are written by hand, by the person reading the answer.
"""

import json
import sys
from pathlib import Path

import yaml

from app.generation import citation_segments
from evaluation.answer_files import LABELS_PATH, excerpts_by_rank, load_answer, segment_hash


def mmss(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:02d}:{total % 60:02d}"


def render_segments(answer_file: dict) -> str:
    """Every segment, then the excerpts it cites, as plain text for reading."""
    excerpts = excerpts_by_rank(answer_file)
    lines = [
        f"row {answer_file.get('row_id')}  video {answer_file['video_id']}  "
        f"model {answer_file['model']}",
        f"question: {answer_file['question']}",
        "",
    ]
    segments = citation_segments(answer_file["answer"])
    if not segments:
        lines.append("(no segments: the answer text is empty after stripping)")
    for seg in segments:
        cites = ", ".join(f"[{n}]" for n in seg.cited) or "nothing"
        lines.append(f"[{seg.index}] hash {segment_hash(seg.text)}  cites {cites}")
        lines.append(f"    {seg.text}")
        for n in seg.cited:
            excerpt = excerpts.get(n)
            if excerpt is None:
                lines.append(f"    excerpt [{n}]: NOT SENT TO THE MODEL")
                continue
            span = f"{mmss(excerpt['start_seconds'])}-{mmss(excerpt['end_seconds'])}"
            lines.append(f"    excerpt [{n}] ({span}):")
            for text_line in excerpt["text"].splitlines() or [""]:
                lines.append(f"        {text_line}")
        lines.append("")
    return "\n".join(lines)


def skeleton_block(answer_file_name: str, answer_file: dict) -> str:
    """One YAML list item for this answer. Labels and refusal left null."""
    segments = citation_segments(answer_file["answer"])
    out = [
        f"  - answer_file: {json.dumps(answer_file_name)}",
        f"    question: {json.dumps(answer_file['question'])}",
        f"    video_id: {json.dumps(answer_file['video_id'])}",
        f"    model: {json.dumps(answer_file['model'])}",
        "    refused:   # true | false, by hand",
    ]
    if not segments:
        out.append("    segments: []")
        return "\n".join(out) + "\n"
    out.append("    segments:")
    for seg in segments:
        out += [
            f"      - index: {seg.index}",
            f"        cited: [{', '.join(str(n) for n in seg.cited)}]",
            f"        hash: {json.dumps(segment_hash(seg.text))}",
            "        label:   # supported | partly | unsupported, by hand",
        ]
    return "\n".join(out) + "\n"


def append_skeleton(labels_text: str, block: str, answer_file_name: str) -> str:
    """Return the labels file with `block` appended. Refuses if anything would change.

    Parses the result in memory first. The existing entries must come back
    unchanged, the new one must be last, and the other top-level keys must be
    the same. Only then is the text returned for writing.
    """
    try:
        old = yaml.safe_load(labels_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"existing labels file does not parse; NOT touched:\n{exc}") from exc
    old_answers = old.get("answers") or []
    if any(a.get("answer_file") == answer_file_name for a in old_answers):
        raise ValueError(f"{answer_file_name} already has a block; not added again")

    if not labels_text.endswith("\n"):
        labels_text += "\n"
    new_text = labels_text + block
    try:
        new = yaml.safe_load(new_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"edited YAML does not parse; file NOT written:\n{exc}") from exc

    new_answers = new.get("answers") or []
    if new_answers[: len(old_answers)] != old_answers:
        raise ValueError("existing answer blocks would change; file NOT written")
    if len(new_answers) != len(old_answers) + 1:
        raise ValueError("expected exactly one new block; file NOT written")
    if new_answers[-1].get("answer_file") != answer_file_name:
        raise ValueError("new block is not the one expected; file NOT written")
    if {k: v for k, v in old.items() if k != "answers"} != \
            {k: v for k, v in new.items() if k != "answers"}:
        raise ValueError("other top-level keys would change; file NOT written")
    return new_text


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python evaluation/label_answer.py evaluation/answers/rowN.json")
    path = Path(sys.argv[1])
    answer_file = load_answer(path)

    print(render_segments(answer_file))

    if not LABELS_PATH.exists():
        sys.exit(f"{LABELS_PATH.name} is missing; restore it from git")
    try:
        new_text = append_skeleton(
            LABELS_PATH.read_text(encoding="utf-8"),
            skeleton_block(path.name, answer_file),
            path.name,
        )
    except ValueError as exc:
        sys.exit(str(exc))
    LABELS_PATH.write_text(new_text, encoding="utf-8")
    print(f"added a blank block for {path.name} to {LABELS_PATH.name}.")
    print("Set `refused` and each `label` by hand before scoring.")


if __name__ == "__main__":
    main()
