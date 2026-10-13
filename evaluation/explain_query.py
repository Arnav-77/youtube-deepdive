"""Explain one retrieval: rank every chunk of a video for one question.

The Phase 03 retrieval debug view in its simplest form. For a question it
prints the stemmed terms the question becomes, how many chunks contain each
term, and then the chunks ranked the way search() ranks them, with score,
time range, term counts, and whether each overlaps the gold span. It turns
"the answer was wrong" into "the right chunk ranked 7th, and this term is why".

    python evaluation/explain_query.py LXaFHI_2Hus "What is the second habit talked about in this video?" --span 282 402 --grep second

It re-implements app/retrieval.py's ranking in SQL instead of calling
search(), because search() returns only the top k and the point here is to
see the chunks it did NOT return. The cost: if retrieval.py's query changes,
this file must change with it, or it explains a different system. Check:
the top k shown here must agree with run_eval.py for the same question.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(".env")

from sqlalchemy import text

from app.db import SessionLocal

# Same construction as app/retrieval.py: Postgres parses and stems the
# question, then the ANDs are swapped for ORs so that any term can match.
TSQUERY = "replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery"

RANK_ALL = text(f"""
    SELECT chunk_index, start_seconds, end_seconds, text,
           to_tsvector('english', text)::text AS tsv,
           ts_rank(to_tsvector('english', text), {TSQUERY}) AS score,
           to_tsvector('english', text) @@ {TSQUERY} AS matches
    FROM chunks
    WHERE video_id = :v AND chunking_run = :run
    ORDER BY score DESC, chunk_index ASC
""")

QUERY_TERMS = text("SELECT to_tsvector('english', :q)::text")

# One entry in a tsvector's text form, e.g.  'habit':3,17  -> lexeme and positions.
TSV_ITEM = re.compile(r"'((?:[^']|'')+)':([0-9A-D,]+)")


def term_counts(tsv_text):
    """{lexeme: number of occurrences}, parsed from a tsvector's text form."""
    return {m.group(1).replace("''", "'"): len(m.group(2).split(","))
            for m in TSV_ITEM.finditer(tsv_text or "")}


def main():
    parser = argparse.ArgumentParser(description="Rank every chunk of a video for one question.")
    parser.add_argument("video_id")
    parser.add_argument("question")
    parser.add_argument("--span", nargs=2, type=float, metavar=("START", "END"),
                        help="gold span in seconds; overlapping chunks are marked GOLD")
    parser.add_argument("--run", default="fixed_1000_200", help="chunking_run label")
    parser.add_argument("--k", type=int, default=5, help="where search() cuts off")
    parser.add_argument("--show", type=int, default=10, help="rows to print (GOLD rows always print)")
    parser.add_argument("--grep", help="print the text around this word in each printed chunk")
    args = parser.parse_args()

    session = SessionLocal()
    try:
        terms = list(term_counts(session.execute(QUERY_TERMS, {"q": args.question}).scalar()))
        rows = session.execute(
            RANK_ALL, {"q": args.question, "v": args.video_id, "run": args.run}
        ).all()
    finally:
        session.close()

    if not rows:
        sys.exit(f"no chunks for {args.video_id} under {args.run}")

    counts = [term_counts(r.tsv) for r in rows]

    print(f"\nquestion: {args.question}")
    print("terms after stemming and stopword removal:", " ".join(terms) or "(none)")
    print("chunks containing each term:")
    for term in terms:
        df = sum(1 for c in counts if term in c)
        print(f"  {term:<14} {df}/{len(rows)}")

    print("\n  rank  chunk  time          score    term counts")
    for rank, (r, c) in enumerate(zip(rows, counts), start=1):
        relevant = (args.span is not None
                    and r.start_seconds < args.span[1] and args.span[0] < r.end_seconds)
        if rank <= args.show or relevant:
            found = " ".join(f"{t}x{c[t]}" for t in terms if t in c) or "-"
            flags = ("GOLD " if relevant else "") + ("" if r.matches else "(no term matches)")
            print(f"  {rank:>4}  {r.chunk_index:>5}  {r.start_seconds:>5.0f}-{r.end_seconds:<6.0f}"
                  f"  {r.score:.4f}   {found:<32} {flags}")
            if args.grep:
                low, word = r.text.lower(), args.grep.lower()
                i = low.find(word)
                while i != -1:
                    snippet = r.text[max(0, i - 40):i + 40].replace("\n", " ")
                    print(f"               ...{snippet}...")
                    i = low.find(word, i + 1)
        if rank == args.k:
            print(f"  ---------- search() returns only the rows above (k={args.k}) ----------")
    print()


if __name__ == "__main__":
    main()