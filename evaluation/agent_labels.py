"""Build evaluation/grounding_labels_agent.yaml from the AI judge's labels.

    python evaluation/agent_labels.py JUDGED.yaml [JUDGED.yaml ...]

The AI judge (Claude Code subagents, finish run 18 Nov 2026) reads each
saved answer with `label_answer.py --show` and returns, per answer file:
refused, and for each segment index a label and a one-line reason. This
script checks that every segment of every answer got exactly one label from
the allowed three, computes each segment's hash from the saved answer (the
same hash label_answer.py writes, so score_grounding.py can detect stale
labels), and writes the labels file.

It writes only grounding_labels_agent.yaml. Arnav's grounding_labels.yaml is
never read or written here: AI labels and human labels stay in separate files
and are never added together.
"""

import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.citations import citation_segments
from evaluation.answer_files import ANSWERS_DIR, load_answer, segment_hash

OUT_PATH = Path(__file__).resolve().parent / "grounding_labels_agent.yaml"
LABELLER = "claude-code (claude-opus-5-5)"
LABELS = ("supported", "partly", "unsupported")

HEADER = f"""# AI-LABELLED grounding labels. NOT Arnav's labels.
#
# labeller: {LABELLER}, as an AI judge, during the unattended finish run on
# 18 Nov 2026 (report_src/finish_plan.md, step 2d). Each saved answer was read
# with `python evaluation/label_answer.py --show` and judged with the rubric in
# docs/grounding.md: a segment is judged only against the excerpts cited in
# its own run; uncited text is unsupported; a true claim the excerpts do not
# state is unsupported. `reason` is the judge's one-line reason.
#
# run: this    answered in the finish run (commit_sha local-<sha>)
# run: earlier saved before the finish run (rows 24, 26, 33 to 50)
#
# Scored on its own (score_grounding.py --labels this file); never added to
# the human labels in grounding_labels.yaml. Rows 24 and 26 are labelled in
# both files so agreement can be shown; two answers are far too few to trust
# that agreement figure. No transcript text here.
"""


def build_block(name: str, answer: dict, judged: dict, run: str) -> tuple[dict, list[str]]:
    """One labels-file block and the problems found. Pure."""
    problems = []
    segments = citation_segments(answer["answer"])
    given = {int(s["index"]): s for s in judged.get("segments") or []}
    refused = judged.get("refused")
    if refused not in (True, False):
        problems.append(f"{name}: refused must be true or false")
    if not refused and set(given) != {s.index for s in segments}:
        problems.append(f"{name}: labelled segments {sorted(given)} != answer segments "
                        f"{[s.index for s in segments]}")
    out_segments = []
    for seg in segments:
        g = given.get(seg.index, {})
        label = g.get("label")
        if not refused and label not in LABELS:
            problems.append(f"{name} segment {seg.index}: label {label!r} not allowed")
        out_segments.append({
            "index": seg.index,
            "cited": list(seg.cited),
            "hash": segment_hash(seg.text),
            "label": label if label in LABELS else None,
            "note": g.get("note") or None,
            "reason": g.get("reason") or None,
        })
    block = {
        "answer_file": name,
        "question": answer["question"],
        "video_id": answer["video_id"],
        "model": answer["model"],
        "run": run,
        "refused": refused,
        "segments": out_segments,
    }
    return block, problems


def run_of(answer: dict) -> str:
    return "this" if str(answer.get("commit_sha") or "").startswith("local-") else "earlier"


def render(blocks: list[dict]) -> str:
    lines = [HEADER.rstrip("\n"), f"labeller: {json.dumps(LABELLER)}", "answers:"]
    for b in blocks:
        lines.append(f"  - answer_file: {json.dumps(b['answer_file'])}")
        for key in ("question", "video_id", "model", "run"):
            lines.append(f"    {key}: {json.dumps(b[key], ensure_ascii=False)}")
        lines.append(f"    refused: {'true' if b['refused'] else 'false'}")
        if not b["segments"]:
            lines.append("    segments: []")
            continue
        lines.append("    segments:")
        for s in b["segments"]:
            lines.append(f"      - index: {s['index']}")
            lines.append(f"        cited: [{', '.join(str(n) for n in s['cited'])}]")
            lines.append(f"        hash: {json.dumps(s['hash'])}")
            lines.append(f"        label: {s['label'] or ''}")
            lines.append(f"        note: {json.dumps(s['note']) if s['note'] else ''}")
            lines.append(f"        reason: {json.dumps(s['reason'], ensure_ascii=False) if s['reason'] else ''}")
    return "\n".join(lines) + "\n"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__.split("\n\n")[1])
    judged = {}
    for path in sys.argv[1:]:
        for item in yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []:
            if item["answer_file"] in judged:
                sys.exit(f"{item['answer_file']} judged twice")
            judged[item["answer_file"]] = item

    blocks, problems = [], []
    order = sorted(judged, key=lambda n: int(n[3:].split(".")[0]))
    for name in order:
        answer = load_answer(ANSWERS_DIR / name)
        block, p = build_block(name, answer, judged[name], run_of(answer))
        blocks.append(block)
        problems += p
    if problems:
        print("NOT WRITTEN:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    text = render(blocks)
    assert yaml.safe_load(text)["answers"][0]["answer_file"] == blocks[0]["answer_file"]
    OUT_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {OUT_PATH.name}: {len(blocks)} answers, "
          f"{sum(len(b['segments']) for b in blocks)} segments")


if __name__ == "__main__":
    main()
