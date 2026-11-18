"""Operational numbers from a query_logs CSV export (evaluation/export_logs.py).

    python evaluation/log_stats.py evaluation/exports/prod_logs.csv
    python evaluation/log_stats.py evaluation/exports/dev_logs.csv --source laptop --since 2026-11-18

Reads the CSV only: no database, no network. Every figure is printed with its
sample size. Rows are split by where the request ran (source_of in
log_rows.py): production (a Render commit id), laptop ('local...'), unknown
(before commit ids were logged).

Prints, per source:
  - requests per day;
  - outcomes and the failure rate, by error type (user errors left out of
    the denominator: they were never servable);
  - latency of answered requests: P50, P95, mean, max (server-side,
    query_logs.latency_ms, the whole /ask request);
  - list-price cost per day (estimated_cost_usd from LiteLLM; not billed,
    the project runs on free tiers);
  - fallback rate: answered requests where the model that answered differs
    from the model requested, after stripping the provider prefix;
  - the share of each retrieval method.

Earlier versions of this script queried the database directly; reading an
export instead means production numbers come from one read-only SELECT
(export_logs.py), and the same file can be re-scored at any time.
"""

import argparse
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.log_rows import (
    error_type,
    is_answered,
    outcome_of,
    percentile,
    read_csv,
    source_of,
    strip_provider,
    to_float,
)


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}% ({part}/{whole})" if whole else "n/a (0 of 0)"


def compute(rows: list[dict]) -> dict:
    """Every number for one group of rows. Pure."""
    outcomes = Counter(outcome_of(r) for r in rows)
    user_errors = sum(n for o, n in outcomes.items() if o.startswith("user error"))
    failed = [r for r in rows if outcome_of(r) == "failed"]
    answered = [r for r in rows if is_answered(r)]
    latencies = [v for v in (to_float(r.get("latency_ms")) for r in answered) if v is not None]
    per_day = Counter(str(r.get("created_at") or "")[:10] for r in rows)
    cost_day = defaultdict(float)
    priced_day = Counter()
    for r in rows:
        c = to_float(r.get("estimated_cost_usd"))
        if c is not None:
            day = str(r.get("created_at") or "")[:10]
            cost_day[day] += c
            priced_day[day] += 1
    with_models = [r for r in answered if r.get("model") and r.get("model_requested")]
    fallback = [r for r in with_models
                if strip_provider(r["model"]) != strip_provider(r["model_requested"])]
    return {
        "n": len(rows),
        "per_day": dict(sorted(per_day.items())),
        "outcomes": dict(outcomes.most_common()),
        "servable": len(rows) - user_errors,
        "failed": len(failed),
        "failed_by_type": dict(Counter(error_type(r.get("error")) or "no error text"
                                       for r in failed).most_common()),
        "answered": len(answered),
        "latency_n": len(latencies),
        "p50": percentile(latencies, 0.5),
        "p95": percentile(latencies, 0.95),
        "mean": statistics.fmean(latencies) if latencies else None,
        "max": max(latencies) if latencies else None,
        "cost_per_day": {d: round(cost_day[d], 6) for d in sorted(cost_day)},
        "priced_per_day": dict(sorted(priced_day.items())),
        "fallback": len(fallback),
        "fallback_n": len(with_models),
        "retrieval": dict(Counter(r.get("retrieval_method") or "not recorded"
                                  for r in rows if r.get("video_id")).most_common()),
        "no_citation": sum(1 for r in answered if not (r.get("cited") or "").strip()),
    }


def ms(v) -> str:
    return "n/a" if v is None else f"{v:.0f} ms"


def report(title: str, s: dict) -> list[str]:
    out = [f"== {title}: {s['n']} requests =="]
    out.append("  requests per day: " + (", ".join(f"{d} {n}" for d, n in s["per_day"].items()) or "none"))
    out.append("  outcomes: " + ", ".join(f"{o} {n}" for o, n in s["outcomes"].items()))
    out.append(f"  failure rate: {pct(s['failed'], s['servable'])} of requests that were not user errors")
    for t, n in s["failed_by_type"].items():
        out.append(f"    {t}: {n}")
    out.append(f"  latency of answered requests (n={s['latency_n']}): P50 {ms(s['p50'])}, "
               f"P95 {ms(s['p95'])}, mean {ms(s['mean'])}, max {ms(s['max'])}")
    if 0 < s["latency_n"] < 20:
        out.append(f"    CAUTION: with n={s['latency_n']}, P95 lies between the two slowest requests.")
    out.append("  list-price cost per day (USD, not billed): "
               + (", ".join(f"{d} {c:.4f} ({s['priced_per_day'][d]} priced)"
                            for d, c in s["cost_per_day"].items()) or "none priced"))
    out.append(f"  fallback rate (backup model answered): {pct(s['fallback'], s['fallback_n'])}"
               f" of answered requests that record both models")
    total = sum(s["retrieval"].values())
    out.append("  retrieval method (requests that reached a video): "
               + (", ".join(f"{m} {pct(n, total)}" for m, n in s["retrieval"].items()) or "none"))
    out.append(f"  answered with no citation (often a refusal): {s['no_citation']} of {s['answered']}")
    return out


def select(rows: list[dict], source: str, since: str | None, min_id: int | None) -> list[dict]:
    """Filter rows by source, first day and first id. Pure."""
    out = []
    for r in rows:
        if source != "all" and source_of(r.get("commit_sha")) != source:
            continue
        if since and str(r.get("created_at") or "")[:10] < since:
            continue
        if min_id is not None and int(r.get("id") or 0) < min_id:
            continue
        out.append(r)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("csv", help="a CSV written by export_logs.py")
    parser.add_argument("--source", default="split",
                        choices=["split", "all", "production", "laptop", "unknown"],
                        help="split (default) prints each source separately")
    parser.add_argument("--since", default=None, help="first day, YYYY-MM-DD")
    parser.add_argument("--min-id", type=int, default=None, help="first query_logs id")
    parser.add_argument("--json", default=None, help="also write the numbers to this JSON file")
    args = parser.parse_args()

    rows = read_csv(Path(args.csv))
    print(f"file: {args.csv}   rows: {len(rows)}"
          + (f"   since {args.since}" if args.since else "")
          + (f"   id >= {args.min_id}" if args.min_id is not None else ""))
    sources = ["production", "laptop", "unknown"] if args.source == "split" else [args.source]
    data = {}
    for src in sources:
        chosen = select(rows, src, args.since, args.min_id)
        if not chosen:
            continue
        print()
        data[src] = compute(chosen)
        for line in report(f"source {src}", data[src]):
            print(line)
    if args.json:
        import json
        Path(args.json).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    print("\nLaptop latency includes about 280 ms per database round trip from Delhi to Ohio;"
          " production latency does not. Report them separately.")


if __name__ == "__main__":
    main()
