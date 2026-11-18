"""Measure /ask latency on a running server, one question at a time.

    python evaluation/live_latency.py --base-url https://youtube-deepdive.onrender.com --dry-run
    python evaluation/live_latency.py --base-url https://youtube-deepdive.onrender.com

Sends up to --max questions (default 25) from a golden-format YAML file
(default evaluation/golden_set.yaml) to POST {base-url}/ask, one at a time,
with --pause seconds (default 8) between requests. For each one it records
the HTTP status, the server-reported latency (latency_ms in the response,
the same number query_logs stores) and the wall time seen by this laptop,
which adds the network to Render. It writes one CSV row per request and
prints P50, P95, mean and failures, each with its sample size.

COST: every request is a real Gemini call on the server's free-tier quota and
writes one query_logs row there. That row is the point: it is the production
latency evidence. Run it once, by hand. Wake the server first (GET /health):
the first request after 15 idle minutes includes a cold start of about 40 s,
which this script reports separately instead of mixing into P50 and P95.

--dry-run prints the questions it would send and sends nothing.
"""

import argparse
import csv
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.log_rows import percentile

DEFAULT_QUESTIONS = Path(__file__).resolve().parent / "golden_set.yaml"
DEFAULT_OUT = Path(__file__).resolve().parent / "exports" / "live_latency.csv"


def pick_questions(golden: dict, limit: int) -> list[tuple[str, str]]:
    """(video_id, question) pairs in file order, at most `limit`. Pure."""
    out = []
    for video in golden.get("videos") or []:
        for q in video.get("questions") or []:
            out.append((video["video_id"], q["q"]))
    return out[:limit]


def summary(results: list[dict]) -> list[str]:
    """Printed summary of the recorded requests. Pure."""
    ok = [r for r in results if r["status"] == 200]
    failed = [r for r in results if r["status"] != 200]
    server = [r["server_ms"] for r in ok if r["server_ms"] is not None]
    wall = [r["wall_ms"] for r in ok]
    lines = [f"requests: {len(results)}, status 200: {len(ok)}, failed: {len(failed)}"]
    for name, xs in (("server latency", server), ("wall time", wall)):
        if xs:
            lines.append(f"{name} (n={len(xs)}): P50 {percentile(xs, 0.5):.0f} ms, "
                         f"P95 {percentile(xs, 0.95):.0f} ms, mean {statistics.fmean(xs):.0f} ms, "
                         f"max {max(xs):.0f} ms")
    if 0 < len(server) < 20:
        lines.append(f"CAUTION: n={len(server)}; P95 lies between the two slowest requests.")
    by_status = {}
    for r in failed:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    for status, n in sorted(by_status.items()):
        lines.append(f"  failed with status {status}: {n}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    parser.add_argument("--max", type=int, default=25)
    parser.add_argument("--pause", type=float, default=8.0)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    golden = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))
    todo = pick_questions(golden, args.max)
    base = args.base_url.rstrip("/")
    print(f"{len(todo)} questions to {base}/ask, {args.pause:.0f} s apart")
    if args.dry_run:
        for vid, q in todo:
            print(f"  {vid}  {q}")
        print("dry run: nothing sent")
        return

    import httpx

    # Wake-up request, reported apart: a cold start is not /ask latency.
    started = time.perf_counter()
    try:
        health = httpx.get(f"{base}/health", timeout=120).status_code
    except httpx.HTTPError as exc:
        sys.exit(f"/health failed: {type(exc).__name__}")
    print(f"/health {health} after {time.perf_counter() - started:.1f} s (includes any cold start)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for i, (vid, q) in enumerate(todo, start=1):
        started = time.perf_counter()
        try:
            r = httpx.post(f"{base}/ask", json={"video": vid, "question": q}, timeout=120)
            status = r.status_code
            server_ms = r.json().get("latency_ms") if status == 200 else None
        except httpx.HTTPError as exc:
            status, server_ms = f"error:{type(exc).__name__}", None
        wall_ms = int((time.perf_counter() - started) * 1000)
        results.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "video_id": vid, "status": status, "server_ms": server_ms,
                        "wall_ms": wall_ms})
        print(f"[{i}/{len(todo)}] {status} server {server_ms} ms, wall {wall_ms} ms")
        if i < len(todo):
            time.sleep(args.pause)

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(f"wrote {out}")
    for line in summary(results):
        print(line)


if __name__ == "__main__":
    main()
