"""Save one query_logs row as an answer file for grounding labels.

    python evaluation/save_answer.py --row 26

Makes two read-only SELECTs: the query_logs row, and the chunks it cites. It
writes evaluation/answers/row<N>.json, which is gitignored because the excerpt
texts are transcript text. It writes nothing to the database and makes no
Gemini call.

Run it from the main folder, whose .env points at the dev branch. This
worktree has no .env, so it stops at import instead of reaching a database.
"""

import argparse
import json
import sys

from app.db import SessionLocal
from app.models import Chunk, QueryLog
from evaluation.answer_files import ANSWERS_DIR


def build_answer_file(row: dict, chunks: dict[int, dict]) -> dict:
    """The saved-answer record for one query_logs row. Pure: no database.

    `row` holds the query_logs fields. `chunks` maps chunk id to its text and
    times. Excerpts are numbered by rank, the same numbers the model was
    shown, so a citation [n] in the answer means the excerpt with rank n.
    """
    if not row["answer"]:
        raise ValueError(f"row {row['id']} has no answer (error: {row['error']})")
    retrieved = sorted(row["retrieved_chunks"] or [], key=lambda r: r["rank"])
    if not retrieved:
        raise ValueError(f"row {row['id']} has no retrieved chunks")

    excerpts = []
    for r in retrieved:
        chunk = chunks.get(r["chunk_id"])
        if chunk is None:
            raise ValueError(f"chunk {r['chunk_id']} not found; its excerpt cannot be rebuilt")
        excerpts.append({
            "rank": r["rank"],
            "chunk_id": r["chunk_id"],
            "start_seconds": chunk["start_seconds"],
            "end_seconds": chunk["end_seconds"],
            "text": chunk["text"],
        })

    return {
        "row_id": row["id"],
        "created_at": row["created_at"].isoformat(),
        "video_id": row["video_id"],
        "question": row["question"],
        "answer": row["answer"],
        "model": row["model"],
        "model_requested": row["model_requested"],
        "prompt_version": row["prompt_version"],
        "commit_sha": row["commit_sha"],
        "retrieval_method": row["retrieval_method"],
        "chunking_run": row["chunking_run"],
        "chunk_ids": [e["chunk_id"] for e in excerpts],
        "excerpts": excerpts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--row", type=int, required=True, help="query_logs id")
    args = parser.parse_args()

    out = ANSWERS_DIR / f"row{args.row}.json"
    if out.exists():
        sys.exit(f"{out.name} already exists; not overwritten")

    # Read only. The session is never committed, and nothing is added to it.
    with SessionLocal() as session:
        log = session.get(QueryLog, args.row)
        if log is None:
            sys.exit(f"no query_logs row {args.row}")
        row = {c.name: getattr(log, c.name) for c in QueryLog.__table__.columns}
        ids = [r["chunk_id"] for r in (log.retrieved_chunks or [])]
        found = session.query(Chunk).filter(Chunk.id.in_(ids)).all() if ids else []
        chunks = {
            c.id: {"text": c.text, "start_seconds": c.start_seconds, "end_seconds": c.end_seconds}
            for c in found
        }

    try:
        record = build_answer_file(row, chunks)
    except ValueError as exc:
        sys.exit(str(exc))

    ANSWERS_DIR.mkdir(exist_ok=True)
    out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}: {len(record['excerpts'])} excerpts, model {record['model']}")
    print(f"question: {record['question']}")


if __name__ == "__main__":
    main()
