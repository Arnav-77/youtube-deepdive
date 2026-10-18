"""Operational numbers from query_logs: outcomes, failure rate, backup-model
use, latency (median and P95) and list-price cost, split by where each
request ran.

    python evaluation/log_stats.py

Read-only. Every figure is printed with its sample size: with a few dozen
rows, a P95 is close to "the second-slowest request" and has to be reported
as exactly that, not as a stable property of the system.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.db import SessionLocal  # noqa: E402

# One row per request, labelled by where it ran and how it ended. starts_with
# rather than LIKE '...%' keeps percent signs out of the SQL entirely.
BASE = r"""
WITH logs AS (
    SELECT *,
        CASE WHEN commit_sha IS NULL THEN 'before 21 Sep (unknown)'
             WHEN commit_sha = 'local' THEN 'laptop'
             ELSE 'production' END AS source,
        CASE WHEN starts_with(error, 'bad_video_reference') THEN 'user error: bad link (400)'
             WHEN starts_with(error, 'video_not_ingested') THEN 'user error: video not added (404)'
             WHEN error = 'no_chunks_retrieved' THEN 'nothing retrieved'
             WHEN starts_with(error, 'invalid_citations') THEN 'answered, invalid citation number'
             WHEN error IS NULL AND answer IS NOT NULL THEN 'answered'
             ELSE 'failed' END AS outcome
    FROM query_logs
)
"""

ANSWERED = "outcome IN ('answered', 'answered, invalid citation number')"

OUTCOMES = BASE + """
SELECT source, outcome, count(*) AS n
FROM logs GROUP BY source, outcome ORDER BY source, n DESC
"""

FAILURES = BASE + """
SELECT id, source, left(error, 80) AS error
FROM logs WHERE outcome = 'failed' ORDER BY id
"""

# An answer with no [n] in it is almost always "the excerpts do not contain
# the answer": the log records it as answered, so count it separately.
NO_CITATION = BASE + r"""
SELECT count(*) FILTER (WHERE answer !~ '\[[0-9]+\]') AS no_citation, count(*) AS n
FROM logs WHERE """ + ANSWERED

# The corrected rule from app/models.py: strip the provider prefix from both
# names before comparing.
FALLBACK = BASE + """
SELECT count(*) FILTER (
           WHERE regexp_replace(model, '^[^/]+/', '')
              <> regexp_replace(model_requested, '^[^/]+/', '')) AS backup_answered,
       count(*) AS n
FROM logs WHERE model IS NOT NULL AND model_requested IS NOT NULL
"""

LATENCY = BASE + """
SELECT source, count(*) AS n,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms))::int AS median_ms,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms))::int AS p95_ms,
       max(latency_ms) AS max_ms
FROM logs WHERE """ + ANSWERED + """
GROUP BY source ORDER BY source
"""

RETRIEVAL = BASE + """
SELECT coalesce(retrieval_method, 'not recorded (before 18 Oct)') AS retrieval_method,
       count(*) AS n
FROM logs GROUP BY 1 ORDER BY n DESC
"""

COST = BASE + """
SELECT regexp_replace(model, '^[^/]+/', '') AS model, count(*) AS n,
       count(estimated_cost_usd) AS priced,
       round(sum(estimated_cost_usd)::numeric, 4) AS total_usd,
       round(avg(estimated_cost_usd)::numeric, 5) AS avg_usd,
       round(avg(prompt_tokens)) AS avg_prompt_tokens,
       round(avg(completion_tokens)) AS avg_completion_tokens
FROM logs WHERE model IS NOT NULL
GROUP BY 1 ORDER BY n DESC
"""


def table(session, title, sql):
    """Run one query and print it as an aligned table. Returns the rows."""
    result = session.execute(text(sql))
    cols = list(result.keys())
    rows = result.all()
    widths = [max([len(str(c))] + [len(str(r[i])) for r in rows]) for i, c in enumerate(cols)]
    print(f"\n{title}")
    print("  " + "  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
    return rows


def main():
    with SessionLocal() as session:
        outcomes = table(session, "Outcomes, by where the request ran", OUTCOMES)
        total = sum(r.n for r in outcomes)
        user_errors = sum(r.n for r in outcomes if r.outcome.startswith("user error"))
        failed = sum(r.n for r in outcomes if r.outcome == "failed")
        servable = total - user_errors
        if servable:
            print(f"\nFailure rate: {failed} of {servable} requests that were not user "
                  f"errors ({failed / servable:.0%}), all sources and dates together")
        table(session, "Failed requests", FAILURES)

        nc = session.execute(text(NO_CITATION)).one()
        print(f"\nAnswered with no citation (most likely a refusal): {nc.no_citation} of {nc.n}")
        fb = session.execute(text(FALLBACK)).one()
        print(f"Backup model answered: {fb.backup_answered} of {fb.n} answered requests "
              f"that record which model was asked for")

        table(session, "Latency of answered requests, milliseconds", LATENCY)
        print("  With this few requests, P95 is close to the slowest one or two.")
        print("  Laptop times include ~280 ms per database round trip to Ohio; "
              "production does not.")

        table(session, "Retrieval method", RETRIEVAL)
        table(session, "Model, tokens and list-price cost (answered requests)", COST)
        print("  List price, not billed: the project runs on free tiers.")


if __name__ == "__main__":
    main()
