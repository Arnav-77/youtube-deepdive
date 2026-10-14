"""Embed transcript chunks into chunk_embeddings, resumably.

    python evaluation/embed_chunks.py                # videos that have labelled questions
    python evaluation/embed_chunks.py --videos all   # all benchmark videos

Only chunks without an embedding for this model are sent, so a run that stops
halfway (daily quota, network) simply continues where it left off next time.
Each batch is committed as soon as it arrives, so nothing paid for is lost.
"""

import argparse
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(".env")

from sqlalchemy import bindparam, text

from app.db import SessionLocal
from app.embeddings import BATCH_SIZE, MODEL_KEY, EmbeddingError, iter_document_embeddings

# Chunks of these videos, under this chunking run, with no embedding yet for
# this model. The LEFT JOIN keeps every chunk; "e.chunk_id IS NULL" keeps only
# the ones the join found nothing for.
MISSING = text("""
    SELECT c.id, c.text
    FROM chunks c
    LEFT JOIN chunk_embeddings e ON e.chunk_id = c.id AND e.model = :model
    WHERE c.chunking_run = :run
      AND c.video_id IN :videos
      AND e.chunk_id IS NULL
    ORDER BY c.video_id, c.chunk_index
""").bindparams(bindparam("videos", expanding=True))

INSERT = text("""
    INSERT INTO chunk_embeddings (chunk_id, model, embedding)
    VALUES (:chunk_id, :model, CAST(:embedding AS vector))
    ON CONFLICT DO NOTHING
""")

COUNT = text("""
    SELECT count(*) FROM chunk_embeddings e JOIN chunks c ON c.id = e.chunk_id
    WHERE e.model = :model AND c.chunking_run = :run AND c.video_id IN :videos
""").bindparams(bindparam("videos", expanding=True))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--videos", choices=["labelled", "all"], default="labelled")
    parser.add_argument("--run", default="fixed_1000_200", help="chunking_run label")
    args = parser.parse_args()

    golden = yaml.safe_load((Path(__file__).resolve().parent / "golden_set.yaml").read_text())
    videos = [v["video_id"] for v in golden["videos"]
              if args.videos == "all" or v.get("questions")]
    params = {"model": MODEL_KEY, "run": args.run, "videos": videos}

    with SessionLocal() as session:
        missing = session.execute(MISSING, params).all()
        requests_needed = -(-len(missing) // BATCH_SIZE)  # ceiling division
        print(f"{len(videos)} videos, {len(missing)} chunks to embed with {MODEL_KEY} "
              f"({requests_needed} requests)")

        if missing:
            ids = [row.id for row in missing]
            texts = [row.text for row in missing]
            t0 = time.monotonic()
            done = 0
            try:
                for start, vectors in iter_document_embeddings(texts):
                    session.execute(INSERT, [
                        {"chunk_id": ids[start + i], "model": MODEL_KEY, "embedding": v}
                        for i, v in enumerate(vectors)
                    ])
                    session.commit()
                    done += len(vectors)
                    print(f"  {done}/{len(texts)} embedded and saved ({time.monotonic() - t0:.0f} s)")
            except EmbeddingError as exc:
                print(f"stopped: {exc}\n{done} saved this run; rerun to continue.")

        total = session.execute(COUNT, params).scalar()
        print(f"chunks with an embedding, for these videos: {total}")


if __name__ == "__main__":
    main()