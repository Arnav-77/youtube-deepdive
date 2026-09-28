"""Retrieval evaluation against the golden set.

Runs every labelled question through search() and scores what came back
against the hand-written gold spans. Retrieval only: no model is called, so
this costs nothing and is safe to run as often as you like.

WHAT IT MEASURES
  Recall@k   Did any relevant chunk appear in the top k? Averaged over
             questions. The headline number: if the right passage is never
             retrieved, no prompt can produce a grounded answer.
  MRR        Mean Reciprocal Rank. 1/rank of the FIRST relevant chunk,
             averaged. Rank 1 scores 1.0, rank 2 scores 0.5, rank 5 scores
             0.2, nothing relevant scores 0. Recall says "was it in there";
             MRR says "was it near the top", which is what the model sees
             first and what a user clicks.
  offset     Cited chunk start minus gold span start, in seconds, for the
             top-ranked relevant chunk. NEGATIVE = citation lands early (user
             waits through filler). POSITIVE = lands late (user misses the
             start of the answer and may not notice). Late is worse, so the
             two signs are reported separately rather than averaged.

WHAT COUNTS AS RELEVANT
  Any overlap between the chunk's time range and a gold span. Deliberately
  generous, with the offset reporting the quality of that overlap: one simple
  rule plus one honest number beats a rule with a threshold to defend.

QUESTIONS WITH NO GOLD SPAN
  spans: [] means the answer is not in the video and the system should refuse.
  Those are scored separately: retrieving nothing is correct, and they are
  excluded from Recall and MRR (there is nothing to recall).
"""

import argparse
import statistics
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.retrieval import search


def overlaps(chunk_start, chunk_end, spans):
    """True when the chunk's time range touches any gold span.

    Two ranges overlap when each starts before the other ends. Written this
    way rather than by enumerating cases because the four-case version is
    where off-by-one bugs live.
    """
    return any(chunk_start < end and start < chunk_end for start, end in spans)


def evaluate(session, data, chunking_run, k, set_filter):
    hits_at_k, reciprocal_ranks, offsets = [], [], []
    refusals_correct, refusals_total = 0, 0
    rows = []

    for video in data["videos"]:
        if set_filter != "all" and video["set"] != set_filter:
            continue

        for question in video["questions"]:
            spans = question.get("spans") or []
            results = search(session, video["video_id"], question["q"], chunking_run, k)

            # No gold span: the correct behaviour is to retrieve nothing.
            if not spans:
                refusals_total += 1
                ok = len(results) == 0
                refusals_correct += ok
                rows.append((video["video_id"], question["q"], "refuse",
                             "correct" if ok else f"returned {len(results)}"))
                continue

            # Rank of the first relevant chunk. 1-based: rank 1 is the top hit.
            first = None
            for i, r in enumerate(results, start=1):
                if overlaps(r.start_seconds, r.end_seconds, spans):
                    first = (i, r)
                    break

            if first is None:
                hits_at_k.append(0)
                reciprocal_ranks.append(0.0)
                rows.append((video["video_id"], question["q"], "MISS",
                             f"{len(results)} chunks, none relevant"))
            else:
                rank, chunk = first
                hits_at_k.append(1)
                reciprocal_ranks.append(1.0 / rank)
                # Offset against the EARLIEST gold span the chunk touches:
                # with several spans, the first one it overlaps is the one the
                # citation would send the user to.
                span = min((s for s in spans
                            if chunk.start_seconds < s[1] and s[0] < chunk.end_seconds),
                           key=lambda s: s[0])
                offset = chunk.start_seconds - span[0]
                offsets.append(offset)
                rows.append((video["video_id"], question["q"], f"rank {rank}",
                             f"offset {offset:+.0f}s"))

    return hits_at_k, reciprocal_ranks, offsets, refusals_correct, refusals_total, rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="fixed_1000_200",
                        help="chunking_run label to evaluate")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--set", default="dev", choices=["dev", "reported", "all"],
                        help="dev while tuning; reported only for final numbers")
    args = parser.parse_args()

    path = Path(__file__).resolve().parent / "golden_set.yaml"
    data = yaml.safe_load(path.read_text())

    session = SessionLocal()
    try:
        hits, rr, offsets, ref_ok, ref_n, rows = evaluate(
            session, data, args.run, args.k, args.set)
    finally:
        session.close()

    print(f"\nchunking_run={args.run}  k={args.k}  set={args.set}\n")
    for video_id, q, verdict, detail in rows:
        print(f"  {video_id}  {verdict:<8} {detail:<28} {q[:44]}")

    print()
    if hits:
        print(f"  Recall@{args.k}     {sum(hits) / len(hits):.3f}   ({sum(hits)}/{len(hits)})")
        print(f"  MRR           {sum(rr) / len(rr):.3f}")
    else:
        print("  no questions with gold spans in this set")

    if offsets:
        late = [o for o in offsets if o > 0]
        print(f"  offset median {statistics.median(offsets):+.0f}s"
              f"   late: {len(late)}/{len(offsets)}"
              f"   worst late {max(late, default=0):+.0f}s")

    if ref_n:
        print(f"  refusals      {ref_ok}/{ref_n} correct")
    print()


if __name__ == "__main__":
    main()