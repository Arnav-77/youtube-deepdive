"""Agreement between Arnav's spot-check labels and the AI judge's labels.

    python evaluation/spot_check_agreement.py [--json evaluation/results/spot_check.json]

With --json it also writes the one-sentence result the report builder reads.
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


def summary_text(pairs: list) -> str:
    """One sentence for the report: agreement with its sample size. Pure."""
    n = len(pairs)
    if not n:
        return "no segments labelled yet"
    agree = sum(1 for a, b in pairs if a == b)
    return (f"I labelled {n} of the 20 segments by hand and agreed with the AI judge on "
            f"{agree} of {n} ({100 * agree / n:.0f}%). With {n} segments this is a rough check, "
            "not a reliable agreement measure.")


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
    if "--json" in sys.argv:
        import json
        out = Path(sys.argv[sys.argv.index("--json") + 1])
        out.write_text(json.dumps({"text": summary_text(result["pairs"]), "pairs": len(result["pairs"]),
                                   "blank": result["blank"]}, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
