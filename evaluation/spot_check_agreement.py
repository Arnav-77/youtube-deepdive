"""Agreement between Arnav's spot-check labels and the AI judge's labels.

    python evaluation/spot_check_agreement.py

Reads evaluation/spot_check_labels.yaml (Arnav, by hand) and
evaluation/grounding_labels_agent.yaml (AI judge). For every spot-check item
Arnav has labelled, compares the two labels on the same segment, after
checking the segment hash still matches. Prints percent agreement and a 3x3
table (rows: Arnav, columns: AI), each with its sample size.

Reads files only: no database, no network.
"""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HERE = Path(__file__).resolve().parent
SPOT_LABELS = HERE / "spot_check_labels.yaml"
AGENT_LABELS = HERE / "grounding_labels_agent.yaml"
ORDER = ("supported", "partly", "unsupported")


def compare(spot: dict, agent: dict) -> dict:
    """Pairs of (human, AI) labels for filled items, plus problems. Pure."""
    ai = {}
    for block in agent.get("answers") or []:
        for seg in block.get("segments") or []:
            ai[(block["answer_file"], int(seg["index"]))] = (seg.get("hash"), seg.get("label"))
    pairs, problems, blank = [], [], 0
    for item in spot.get("segments") or []:
        human = item.get("label")
        if human is None:
            blank += 1
            continue
        if human not in ORDER:
            problems.append(f"item {item.get('item')}: label {human!r} is not one of {', '.join(ORDER)}")
            continue
        key = (item["answer_file"], int(item["index"]))
        if key not in ai:
            problems.append(f"item {item.get('item')}: no AI label for {key}")
            continue
        h, label = ai[key]
        if h != item.get("hash"):
            problems.append(f"item {item.get('item')}: segment hash differs (stale)")
            continue
        pairs.append((human, label))
    return {"pairs": pairs, "problems": problems, "blank": blank}


def table_lines(pairs: list) -> list[str]:
    n = len(pairs)
    agree = sum(1 for a, b in pairs if a == b)
    lines = [f"labelled by Arnav: {n}",
             f"agreement: {100 * agree / n:.0f}% ({agree}/{n})" if n else "agreement: n/a (0 of 0)",
             "rows: Arnav, columns: AI judge",
             " " * 13 + "".join(f"{c:>13}" for c in ORDER)]
    for r in ORDER:
        lines.append(f"{r:<13}" + "".join(f"{sum(1 for a, b in pairs if a == r and b == c):>13}" for c in ORDER))
    if 0 < n < 30:
        lines.append(f"With {n} segments this is a rough check of the AI judge, not a reliable agreement measure.")
    return lines


def main() -> None:
    spot = yaml.safe_load(SPOT_LABELS.read_text(encoding="utf-8")) or {}
    agent = yaml.safe_load(AGENT_LABELS.read_text(encoding="utf-8")) or {}
    result = compare(spot, agent)
    if result["problems"]:
        print("NOT SCORED:")
        for p in result["problems"]:
            print(f"  - {p}")
        sys.exit(1)
    print(f"not yet labelled: {result['blank']}")
    for line in table_lines(result["pairs"]):
        print(line)


if __name__ == "__main__":
    main()
