"""Citation rate (proposal Objective 2: at least 90% of responses cite timestamps).

    python evaluation/citation_rate.py evaluation/exports/dev_logs.csv \
        --labels evaluation/grounding_labels_agent.yaml --labels evaluation/grounding_labels.yaml

Reads a CSV from export_logs.py, and optionally labels files for refusals.
No database, no network.

Definitions:
  answered   the request returned an answer (outcome "answered" or
             "answered, invalid citation number"); failures, user errors and
             empty retrievals are not responses with content and are left out.
  cited      the answer holds at least one VALID citation: a number from 1
             to the number of excerpts sent. Code turns each into a ?t= link,
             so a valid citation is a timestamp link the user can click.
  refusal    a labelled refused: true in a labels file (human or AI). Only
             labelled rows can be counted as refusals; for unlabelled rows
             the number of answers with no citation at all is printed as an
             upper bound, since a refusal normally cites nothing.

Two groups, never mixed: this run's rows (commit_sha "local-<sha>", written by
generate_answers.py on 18 Nov) and all earlier rows.
"""

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.log_rows import is_answered, read_csv

TARGET = 0.90


def refusal_labels(paths: list[Path]) -> dict[int, tuple[bool, str]]:
    """query_logs id -> (refused, labels file name), from saved-answer names rowN.json."""
    out = {}
    for path in paths:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        for block in data.get("answers") or []:
            name = str(block.get("answer_file") or "")
            if block.get("refused") is None or not name.startswith("row"):
                continue
            row_id = int(name[3:].split(".")[0])
            out.setdefault(row_id, (bool(block["refused"]), Path(path).name))
    return out


def rate(rows: list[dict], refusals: dict) -> dict:
    """Citation numbers for one group of rows. Pure."""
    answered = [r for r in rows if is_answered(r)]
    cited = [r for r in answered if int(r.get("valid_cited") or 0) > 0]
    labelled = [r for r in answered if int(r["id"]) in refusals]
    refused = [r for r in labelled if refusals[int(r["id"])][0]]
    not_refused = [r for r in answered if not (int(r["id"]) in refusals and refusals[int(r["id"])][0])]
    unlabelled = [r for r in answered if int(r["id"]) not in refusals]
    return {
        "answered": len(answered),
        "cited": len(cited),
        "labelled": len(labelled),
        "refused": len(refused),
        "refused_cited": sum(1 for r in refused if int(r.get("valid_cited") or 0) > 0),
        "not_refused": len(not_refused),
        "not_refused_cited": sum(1 for r in not_refused if int(r.get("valid_cited") or 0) > 0),
        "unlabelled": len(unlabelled),
        "unlabelled_no_citation": sum(1 for r in unlabelled if not (r.get("cited") or "").strip()),
        "invalid_any": sum(1 for r in answered if int(r.get("invalid_cited") or 0) > 0),
    }


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}% ({part}/{whole})" if whole else "n/a (0 of 0)"


def report(title: str, s: dict) -> list[str]:
    out = [f"== {title} =="]
    out.append(f"  answered responses: {s['answered']}")
    out.append(f"  with at least one valid citation: {pct(s['cited'], s['answered'])}"
               f"   target {TARGET:.0%}: {'met' if s['answered'] and s['cited'] / s['answered'] >= TARGET else 'not met'}")
    out.append(f"  refusals (labelled refused: true): {s['refused']} of {s['labelled']} labelled answers"
               f"; refusals that still cite: {s['refused_cited']}")
    out.append(f"  excluding labelled refusals: {pct(s['not_refused_cited'], s['not_refused'])}")
    out.append(f"  answers with no refusal label: {s['unlabelled']}"
               f" (of these, {s['unlabelled_no_citation']} cite nothing: an upper bound on unlabelled refusals)")
    out.append(f"  answers citing an excerpt never sent: {s['invalid_any']} of {s['answered']}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("csv")
    parser.add_argument("--labels", action="append", default=[],
                        help="labels file with refused values; repeat for several (first wins)")
    parser.add_argument("--json", default=None, help="also write the numbers to this JSON file")
    parser.add_argument("--run-prefix", default="local-",
                        help="commit_sha prefix of this run's rows (default local-)")
    args = parser.parse_args()

    rows = read_csv(Path(args.csv))
    refusals = refusal_labels([Path(p) for p in args.labels])
    this_run = [r for r in rows if str(r.get("commit_sha") or "").startswith(args.run_prefix)]
    earlier = [r for r in rows if not str(r.get("commit_sha") or "").startswith(args.run_prefix)]
    print(f"file: {args.csv}   rows: {len(rows)}   labels: {', '.join(Path(p).name for p in args.labels) or 'none'}")
    print("A valid citation is a number between 1 and the number of excerpts sent; "
          "code turns it into a ?t= timestamp link.")
    data = {}
    for key, title, group in (("this_run", f"this run (commit_sha starting {args.run_prefix!r})", this_run),
                              ("earlier", "all earlier rows", earlier)):
        print()
        data[key] = rate(group, refusals)
        for line in report(title, data[key]):
            print(line)
    if args.json:
        import json
        Path(args.json).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
