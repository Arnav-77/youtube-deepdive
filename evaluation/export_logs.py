"""Export query_logs to CSV with one read-only SELECT.

    python evaluation/export_logs.py --out evaluation/exports/dev_logs.csv
    DATABASE_URL=<production URL> python evaluation/export_logs.py --out evaluation/exports/prod_logs.csv

Meant for production as well as dev: this is how the report's production
numbers are collected. It prints the database host first, so the person
running it can see which database it reached. It never writes to the
database: the transaction is set READ ONLY before the SELECT, so even a bug
here could not change a row.

By default the CSV holds no question or answer text, only ids, models,
timings, errors and citation counts (cited numbers and how many were valid).
Visitors type the questions on the live site, so --with-text is opt-in.
evaluation/exports/ is gitignored either way.

Then: python evaluation/log_stats.py evaluation/exports/prod_logs.csv
"""

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluation.log_rows import COLUMNS, TEXT_COLUMNS, export_row

DEFAULT_OUT = Path(__file__).resolve().parent / "exports" / "query_logs.csv"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--with-text", action="store_true",
                        help="also export question and answer text (keep the file private)")
    args = parser.parse_args()

    from sqlalchemy import text

    from app.db import SessionLocal
    from app.models import QueryLog
    from evaluation.dev_guard import print_host

    print_host()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    columns = COLUMNS + (TEXT_COLUMNS if args.with_text else [])

    with SessionLocal() as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        logs = session.query(QueryLog).order_by(QueryLog.id).all()
        rows = [export_row({c.name: getattr(log, c.name) for c in QueryLog.__table__.columns},
                           args.with_text) for log in logs]
        session.rollback()

    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
