"""Pure helpers shared by export_logs, log_stats, citation_rate and live_latency.

No database, no network. Rows are plain dicts, as read back from the CSV that
export_logs.py writes, so every number can be recomputed from that file alone.
"""

import csv
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.citations import cited_numbers

# Columns written by export_logs.py, in order. No question or answer text
# unless --with-text is given: production questions are typed by visitors.
COLUMNS = [
    "id", "created_at", "video_id", "chunking_run", "prompt_version", "commit_sha",
    "retrieval_method", "model", "model_requested", "prompt_tokens", "completion_tokens",
    "estimated_cost_usd", "latency_ms", "error", "n_retrieved", "has_answer",
    "cited", "valid_cited", "invalid_cited",
]
TEXT_COLUMNS = ["question", "answer"]


def percentile(values, p: float):
    """Linear-interpolation percentile, the same rule as Postgres percentile_cont.

    p is between 0 and 1. None for an empty list. With n values the P95 sits
    between the two slowest once n < 20, so callers print n next to it.
    """
    xs = sorted(float(v) for v in values)
    if not xs:
        return None
    pos = p * (len(xs) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def strip_provider(model: str | None) -> str:
    """'gemini/gemini-3.6-flash' -> 'gemini-3.6-flash'. Same rule as score_grounding."""
    model = model or ""
    return model.partition("/")[2] or model


def source_of(commit_sha: str | None) -> str:
    """Where a request ran, from query_logs.commit_sha.

    NULL: before commit ids were logged (21 Sep). 'local' or 'local-<sha>':
    the laptop (the finish run writes 'local-<sha>'). Anything else is the
    commit id Render built, so production.
    """
    if not commit_sha:
        return "unknown"
    return "laptop" if commit_sha.startswith("local") else "production"


def outcome_of(row: dict) -> str:
    """How one request ended. Same categories as the earlier SQL in log_stats."""
    error = row.get("error") or ""
    has_answer = str(row.get("has_answer")).lower() in ("true", "1")
    if error.startswith("bad_video_reference"):
        return "user error: bad link (400)"
    if error.startswith("video_not_ingested") or error.startswith("video_hidden"):
        return "user error: video not added (404)"
    if error == "no_chunks_retrieved":
        return "nothing retrieved"
    if error.startswith("invalid_citations"):
        return "answered, invalid citation number"
    if not error and has_answer:
        return "answered"
    return "failed"


def is_answered(row: dict) -> bool:
    return outcome_of(row).startswith("answered")


def error_type(error: str | None) -> str:
    """The class part of an error: 'ServiceUnavailableError: ...' -> 'ServiceUnavailableError'."""
    error = (error or "").strip()
    if not error:
        return ""
    return error.split(":", 1)[0].strip()


def citation_counts(answer: str | None, n_retrieved: int) -> tuple[list[int], int, int]:
    """(cited numbers, how many are valid, how many are invalid) for one answer.

    Valid means 1..n_retrieved: an excerpt that was actually sent.
    """
    cited = sorted(cited_numbers(answer or ""))
    valid = sum(1 for n in cited if 1 <= n <= n_retrieved)
    return cited, valid, len(cited) - valid


def export_row(log: dict, with_text: bool = False) -> dict:
    """One CSV row from one query_logs row (as a dict of columns). Pure."""
    retrieved = log.get("retrieved_chunks") or []
    cited, valid, invalid = citation_counts(log.get("answer"), len(retrieved))
    created = log.get("created_at")
    row = {
        "id": log.get("id"),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
        "video_id": log.get("video_id"),
        "chunking_run": log.get("chunking_run"),
        "prompt_version": log.get("prompt_version"),
        "commit_sha": log.get("commit_sha"),
        "retrieval_method": log.get("retrieval_method"),
        "model": log.get("model"),
        "model_requested": log.get("model_requested"),
        "prompt_tokens": log.get("prompt_tokens"),
        "completion_tokens": log.get("completion_tokens"),
        "estimated_cost_usd": log.get("estimated_cost_usd"),
        "latency_ms": log.get("latency_ms"),
        "error": log.get("error"),
        "n_retrieved": len(retrieved),
        "has_answer": bool(log.get("answer")),
        "cited": " ".join(str(n) for n in cited),
        "valid_cited": valid,
        "invalid_cited": invalid,
    }
    if with_text:
        row["question"] = log.get("question")
        row["answer"] = log.get("answer")
    return row


def read_csv(path: Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
