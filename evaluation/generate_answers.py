"""Generate answers through the real /ask code path, on the dev branch only.

    python evaluation/generate_answers.py --golden evaluation/golden_set.yaml --label human-r1
    python evaluation/generate_answers.py --golden evaluation/golden_set_agent.yaml --label agent-r1

Each question goes to POST /ask through FastAPI's TestClient: the same route,
live prompt (answer_v1), dense retrieval and query_logs row as production, in
this process. Gemini is never called any other way.

After each request the new query_logs row is copied to
evaluation/answers/rowN.json with save_answer.build_answer_file (gitignored:
excerpts are transcript text), and one line is appended to
evaluation/answers/finish_runs.jsonl: label, question, status, row id,
server latency, wall time, model, retrieval method.

BUDGET (finish plan, hard rule 6), kept in evaluation/answers/finish_budget.json
so a restart cannot overspend:
  - at most 120 answer-generation calls in total. One /ask is charged 1 call,
    or 2 when the logged model differs from the requested one or generation
    failed (FALLBACK_MODELS may have made a second call inside the request);
  - one request at a time, at least 7 s apart (PAUSE_S = 8, end to start);
  - on a 429 or 503: wait 60 s, retry once, then skip and record;
  - at most 200 new texts sent for embedding (query-embedding cache misses).
When a budget runs out the script stops and says how much was used.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.answer_files import ANSWERS_DIR

BUDGET_PATH = ANSWERS_DIR / "finish_budget.json"
RUNS_PATH = ANSWERS_DIR / "finish_runs.jsonl"
MAX_CALLS = 120
MAX_EMBEDS = 200
PAUSE_S = 8
RETRY_WAIT_S = 60
OVERLOAD_MARKERS = ("429", "503", "RateLimit", "ServiceUnavailable", "overloaded", "high demand")


def load_budget(path: Path = BUDGET_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"calls": 0, "embeds": 0, "requests": 0}


def save_budget(budget: dict, path: Path = BUDGET_PATH) -> None:
    path.write_text(json.dumps(budget, indent=2) + "\n", encoding="utf-8")


def calls_charged(status: int, model: str | None, model_requested: str | None,
                  error: str | None) -> int:
    """Gemini calls to charge for one /ask. Pure.

    0 when generation never ran (no chunks, video missing). 2 when a fallback
    may have fired: the answering model differs from the requested one, or
    generation failed (the fallback is tried before failure is reported).
    """
    if status == 404 or (error or "").startswith("no_chunks_retrieved"):
        return 0
    if status == 502 or (error and not error.startswith("invalid_citations")):
        return 2
    strip = lambda m: (m or "").partition("/")[2] or (m or "")
    if model and model_requested and strip(model) != strip(model_requested):
        return 2
    return 1


def is_overload(status: int, detail: str) -> bool:
    """A 429 or 503 from upstream, as /ask reports it (502 with the cause). Pure."""
    return status in (429, 503) or (status == 502 and any(m in detail for m in OVERLOAD_MARKERS))


def questions_from(golden: dict, set_filter: str) -> list[dict]:
    out = []
    for video in golden.get("videos") or []:
        if set_filter != "all" and video.get("set") != set_filter:
            continue
        for q in video.get("questions") or []:
            out.append({"video_id": video["video_id"], "set": video.get("set"),
                        "q": q["q"], "type": q.get("type", "human"),
                        "negative": not (q.get("spans") or [])})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--golden", required=True)
    parser.add_argument("--label", required=True, help="run label, e.g. human-r1")
    parser.add_argument("--set", default="all", choices=["dev", "reported", "all"])
    parser.add_argument("--limit", type=int, default=None, help="stop after N questions")
    args = parser.parse_args()

    from evaluation.dev_guard import require_dev
    require_dev()

    # Recorded in query_logs.commit_sha. "local-" keeps laptop rows apart from
    # Render rows, which carry the bare commit id.
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                         text=True, check=True).stdout.strip()
    os.environ["RENDER_GIT_COMMIT"] = f"local-{sha}"

    from fastapi.testclient import TestClient
    from sqlalchemy import text

    from app.db import SessionLocal
    from app.embeddings import CACHE_STATS
    from app.main import app
    from app.models import Chunk, QueryLog
    from evaluation.save_answer import build_answer_file

    golden = yaml.safe_load(Path(args.golden).read_text(encoding="utf-8"))
    todo = questions_from(golden, args.set)
    done = set()
    if RUNS_PATH.exists():
        for line in RUNS_PATH.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            if r["label"] == args.label and r.get("final"):
                done.add((r["video_id"], r["question"]))
    todo = [t for t in todo if (t["video_id"], t["q"]) not in done]
    if args.limit is not None:
        todo = todo[: args.limit]
    ANSWERS_DIR.mkdir(exist_ok=True)
    budget = load_budget()
    print(f"{len(todo)} questions to ask; budget used so far: {budget}")

    client = TestClient(app)
    for i, item in enumerate(todo):
        attempt = 0
        while True:
            if budget["calls"] + 2 > MAX_CALLS:
                print(f"STOP: answer budget reached ({budget['calls']} of {MAX_CALLS} calls used)")
                return
            if budget["embeds"] >= MAX_EMBEDS:
                print(f"STOP: embedding budget reached ({budget['embeds']} of {MAX_EMBEDS})")
                return
            misses_before = CACHE_STATS["misses"]
            started = time.perf_counter()
            r = client.post("/ask", json={"video": item["video_id"], "question": item["q"]})
            wall_ms = int((time.perf_counter() - started) * 1000)
            body = r.json()
            detail = str(body.get("detail", "")) if r.status_code != 200 else ""

            with SessionLocal() as s:
                log = s.execute(
                    text("SELECT id FROM query_logs WHERE question = :q AND video_id = :v "
                         "ORDER BY id DESC LIMIT 1"),
                    {"q": item["q"], "v": item["video_id"]},
                ).scalar()
                row = s.get(QueryLog, log) if log else None
                fields = {c.name: getattr(row, c.name) for c in QueryLog.__table__.columns} if row else {}

            charged = calls_charged(r.status_code, fields.get("model"),
                                    fields.get("model_requested"), fields.get("error"))
            budget["calls"] += charged
            budget["embeds"] += CACHE_STATS["misses"] - misses_before
            budget["requests"] += 1
            save_budget(budget)

            overload = is_overload(r.status_code, detail)
            final = not (overload and attempt == 0)
            saved = None
            if row is not None and fields.get("answer"):
                ids = [c["chunk_id"] for c in (fields.get("retrieved_chunks") or [])]
                with SessionLocal() as s:
                    found = s.query(Chunk).filter(Chunk.id.in_(ids)).all() if ids else []
                    chunks = {c.id: {"text": c.text, "start_seconds": c.start_seconds,
                                     "end_seconds": c.end_seconds} for c in found}
                record = build_answer_file(fields, chunks)
                out = ANSWERS_DIR / f"row{row.id}.json"
                if not out.exists():
                    out.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                   encoding="utf-8")
                saved = out.name

            entry = {
                "label": args.label, "video_id": item["video_id"], "set": item["set"],
                "question": item["q"], "type": item["type"], "negative": item["negative"],
                "status": r.status_code, "row_id": fields.get("id"),
                "answer_file": saved, "latency_ms_server": fields.get("latency_ms"),
                "wall_ms": wall_ms, "model": fields.get("model"),
                "model_requested": fields.get("model_requested"),
                "retrieval_method": fields.get("retrieval_method"),
                "error": (fields.get("error") or "")[:120] or None,
                "calls_charged": charged, "attempt": attempt + 1, "final": final,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            with RUNS_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            print(f"[{i + 1}/{len(todo)}] {r.status_code} row {entry['row_id']} "
                  f"{entry['model']} {entry['retrieval_method']} "
                  f"{entry['latency_ms_server']} ms  calls {budget['calls']}/{MAX_CALLS}  "
                  f"{item['q'][:50]}")

            if final:
                break
            attempt += 1
            print(f"  upstream overloaded; waiting {RETRY_WAIT_S} s, then one retry")
            time.sleep(RETRY_WAIT_S)
        time.sleep(PAUSE_S)
    print(f"done. budget used: {budget}")


if __name__ == "__main__":
    main()
