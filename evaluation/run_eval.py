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

import app.embeddings
from app.db import SessionLocal
from app.embeddings import CACHE_STATS
from app.retrieval import dense_search, hybrid_search, search
from evaluation.relevance import overlaps

METHODS = {"lexical": search, "dense": dense_search, "hybrid": hybrid_search}
GOLDEN_PATH = Path(__file__).resolve().parent / "golden_set.yaml"


class CacheMiss(RuntimeError):
    """--cache-only was set and a question has no cached embedding."""


def forbid_embedding_calls():
    """Make any embedding API call raise CacheMiss instead of being sent.

    embed_query() looks up _embed_batch in app.embeddings at call time, so
    replacing it there catches every path: dense and hybrid alike. Cached
    questions never reach it.
    """
    def refuse(texts, *args, **kwargs):
        raise CacheMiss(f"no cached embedding for: {texts[0][:60]!r}")
    app.embeddings._embed_batch = refuse


def evaluate(session, data, chunking_run, k, set_filter, retrieve=search, records=None):
    """Score every question in the set. If `records` is a list, one dict per
    question is appended to it (video, question, type, rank, offset), which
    is what summarise() reads to break results down by question type."""
    hits_at_k, reciprocal_ranks, offsets = [], [], []
    refusals_correct, refusals_total = 0, 0
    rows = []
    if records is None:
        records = []

    for video in data["videos"]:
        if set_filter != "all" and video["set"] != set_filter:
            continue

        for question in video["questions"]:
            spans = question.get("spans") or []
            results = retrieve(session, video["video_id"], question["q"], chunking_run, k)
            record = {"video_id": video["video_id"], "q": question["q"],
                      "type": question.get("type", "-"), "negative": not spans,
                      "rank": None, "offset": None, "returned": len(results)}
            records.append(record)

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
                record["rank"] = rank
                hits_at_k.append(1)
                reciprocal_ranks.append(1.0 / rank)
                # Offset against the EARLIEST gold span the chunk touches:
                # with several spans, the first one it overlaps is the one the
                # citation would send the user to.
                span = min((s for s in spans
                            if chunk.start_seconds < s[1] and s[0] < chunk.end_seconds),
                           key=lambda s: s[0])
                offset = chunk.start_seconds - span[0]
                record["offset"] = offset
                offsets.append(offset)
                rows.append((video["video_id"], question["q"], f"rank {rank}",
                             f"offset {offset:+.0f}s"))

    return hits_at_k, reciprocal_ranks, offsets, refusals_correct, refusals_total, rows


def summarise(records, k):
    """Recall@k, MRR, offsets and negatives for a list of records. Pure.

    Questions with gold spans give Recall and MRR. Negative questions
    (spans: []) are counted apart: a retriever that returns nothing for them
    is "correct". Dense and hybrid always return k chunks, so for them this
    count is 0 by construction, and refusing is generation's job.
    """
    positive = [r for r in records if not r["negative"]]
    negative = [r for r in records if r["negative"]]
    found = [r for r in positive if r["rank"] is not None]
    offsets = [r["offset"] for r in found]
    late = [o for o in offsets if o > 0]
    return {
        "k": k,
        "n": len(positive),
        "hits": len(found),
        "recall": len(found) / len(positive) if positive else None,
        "mrr": sum(1.0 / r["rank"] for r in found) / len(positive) if positive else None,
        "rank1": sum(1 for r in found if r["rank"] == 1),
        "offset_median": statistics.median(offsets) if offsets else None,
        "abs_offset_median": statistics.median(abs(o) for o in offsets) if offsets else None,
        "late": len(late),
        "worst_late": max(late, default=0),
        "negatives": len(negative),
        "negatives_empty": sum(1 for r in negative if r["returned"] == 0),
    }


def summary_lines(s, label=""):
    """The printed summary for one summarise() result."""
    out = []
    head = f"  [{label}] " if label else "  "
    if s["n"]:
        out.append(f"{head}Recall@{s['k']}  {s['recall']:.3f} ({s['hits']}/{s['n']})"
                   f"   MRR {s['mrr']:.3f}   Recall@1 {s['rank1']}/{s['n']}")
    else:
        out.append(f"{head}no questions with gold spans")
    if s["offset_median"] is not None:
        out.append(f"{head}offset median {s['offset_median']:+.0f}s"
                   f"   |offset| median {s['abs_offset_median']:.0f}s"
                   f"   late: {s['late']}/{s['hits']}"
                   f"   worst late {s['worst_late']:+.0f}s")
    if s["negatives"]:
        out.append(f"{head}negative questions: {s['negatives']}, retrieval returned "
                   f"nothing for {s['negatives_empty']}")
    return out


def load_golden(path):
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", default="fixed_1000_200",
                        help="chunking_run label to evaluate")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--set", default="dev", choices=["dev", "reported", "all"],
                        help="dev while tuning; reported only for final numbers")
    parser.add_argument("--method", default="lexical", choices=sorted(METHODS))
    parser.add_argument("--golden", default=str(GOLDEN_PATH),
                        help="golden file to score against (default: golden_set.yaml)")
    parser.add_argument("--cache-only", action="store_true",
                        help="stop with a message instead of calling the embedding API")
    args = parser.parse_args()

    # Retrieval writes newly paid-for query embeddings to query_embeddings,
    # so this script only ever runs against the dev branch.
    from evaluation.dev_guard import require_dev
    require_dev()
    if args.cache_only:
        forbid_embedding_calls()

    data = load_golden(args.golden)

    records = []
    session = SessionLocal()
    try:
        hits, rr, offsets, ref_ok, ref_n, rows = evaluate(
            session, data, args.run, args.k, args.set, METHODS[args.method], records)
    except CacheMiss as exc:
        sys.exit(f"STOPPED (--cache-only): {exc}. Nothing was sent to the embedding API.")
    finally:
        session.close()

    print(f"\nmethod={args.method}  chunking_run={args.run}  k={args.k}  set={args.set}"
          f"  golden={Path(args.golden).name}\n")
    for video_id, q, verdict, detail in rows:
        print(f"  {video_id}  {verdict:<8} {detail:<28} {q[:44]}")

    print()
    for line in summary_lines(summarise(records, args.k)):
        print(line)
    types = sorted({r["type"] for r in records} - {"-"})
    for t in types:
        for line in summary_lines(summarise([r for r in records if r["type"] == t], args.k), t):
            print(line)
    print()
    print(f"  query embeddings: {CACHE_STATS['hits']} cached, {CACHE_STATS['misses']} API requests")


if __name__ == "__main__":
    main()
