"""Prepare Arnav's 15-minute spot check of the AI judge's grounding labels.

    python evaluation/spot_check.py

Picks 20 AI-labelled segments at random (seed 20261118) from
grounding_labels_agent.yaml, from answers that were not refused, and writes:

  evaluation/answers/spot_check.md   (gitignored: holds excerpt text)
      each segment's text and the text of the excerpts it cites, WITHOUT the
      AI label, so the person checking is not anchored by it;
  evaluation/spot_check_labels.yaml
      one blank block per segment (answer file, index, hash), for a label of
      supported, partly or unsupported, by hand. No transcript text.

It refuses to overwrite spot_check_labels.yaml once any label in it is filled.
Then: python evaluation/spot_check_agreement.py
Reads files only: no database, no network.
"""

import json
import random
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.citations import citation_segments
from evaluation.answer_files import ANSWERS_DIR, excerpts_by_rank, load_answer, segment_hash

HERE = Path(__file__).resolve().parent
AGENT_LABELS = HERE / "grounding_labels_agent.yaml"
SPOT_LABELS = HERE / "spot_check_labels.yaml"
SPOT_MD = ANSWERS_DIR / "spot_check.md"
SEED = 20261118
N = 20


def candidates(labels: dict) -> list[tuple[str, int]]:
    """(answer_file, segment index) for every labelled segment of a non-refused answer. Pure."""
    out = []
    for block in labels.get("answers") or []:
        if block.get("refused") is not False:
            continue
        for seg in block.get("segments") or []:
            if seg.get("label") in ("supported", "partly", "unsupported"):
                out.append((block["answer_file"], int(seg["index"])))
    return sorted(out, key=lambda x: (int(x[0][3:].split(".")[0]), x[1]))


def pick(cands: list, n: int = N, seed: int = SEED) -> list:
    """n items chosen with a fixed seed, so the pick can be repeated. Pure."""
    return sorted(random.Random(seed).sample(cands, min(n, len(cands))),
                  key=lambda x: (int(x[0][3:].split(".")[0]), x[1]))


def any_filled(path: Path) -> bool:
    if not path.exists():
        return False
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return any(item.get("label") for item in data.get("segments") or [])


def mmss(s: float) -> str:
    return f"{int(s) // 60:02d}:{int(s) % 60:02d}"


def main() -> None:
    if any_filled(SPOT_LABELS):
        sys.exit(f"{SPOT_LABELS.name} already has labels filled in; not overwritten")
    labels = yaml.safe_load(AGENT_LABELS.read_text(encoding="utf-8")) or {}
    chosen = pick(candidates(labels))

    md = ["# Spot check: 20 segments labelled by the AI judge",
          "",
          f"Chosen at random with seed {SEED} from {len(candidates(labels))} AI-labelled segments "
          "of answers that were not refused. The AI label is NOT shown.",
          "For each item, read the segment and only the excerpts listed under it, then write "
          "supported, partly or unsupported in evaluation/spot_check_labels.yaml (rules: docs/grounding.md).",
          ""]
    blocks = []
    for k, (name, index) in enumerate(chosen, start=1):
        answer = load_answer(ANSWERS_DIR / name)
        seg = next(s for s in citation_segments(answer["answer"]) if s.index == index)
        excerpts = excerpts_by_rank(answer)
        md += [f"## {k}. {name}, segment {index}", "",
               f"Question: {answer['question']}", "", "Segment:", "", f"> {seg.text}", ""]
        if not seg.cited:
            md += ["Cites nothing.", ""]
        for n in seg.cited:
            e = excerpts.get(n)
            if e is None:
                md += [f"Excerpt [{n}]: NOT SENT TO THE MODEL", ""]
                continue
            md += [f"Excerpt [{n}] ({mmss(e['start_seconds'])} to {mmss(e['end_seconds'])}):", "",
                   "```", e["text"], "```", ""]
        blocks.append({"item": k, "answer_file": name, "index": index,
                       "hash": segment_hash(seg.text), "label": None, "note": None})

    SPOT_MD.write_text("\n".join(md) + "\n", encoding="utf-8")
    head = ["# Arnav's spot check of 20 AI-labelled segments (see evaluation/answers/spot_check.md).",
            "# Fill each label by hand: supported | partly | unsupported. Then run",
            "#   python evaluation/spot_check_agreement.py",
            f"# Seed {SEED}. No transcript text in this file.", "segments:"]
    for b in blocks:
        head += [f"  - item: {b['item']}",
                 f"    answer_file: {json.dumps(b['answer_file'])}",
                 f"    index: {b['index']}",
                 f"    hash: {json.dumps(b['hash'])}",
                 "    label:    # supported | partly | unsupported",
                 "    note:"]
    SPOT_LABELS.write_text("\n".join(head) + "\n", encoding="utf-8")
    print(f"wrote {SPOT_MD} and {SPOT_LABELS.name}: {len(blocks)} segments")


if __name__ == "__main__":
    main()
