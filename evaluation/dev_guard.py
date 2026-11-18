"""Stop a script unless it is about to touch the Neon dev branch.

    from evaluation.dev_guard import require_dev
    require_dev()          # prints the host, exits unless it is the dev branch

Every evaluation script that writes to a database, or that should never run
against production, calls require_dev() before its first query. Only the host
name is printed: never the user, password or full URL, because the URL holds
the password.

Scripts meant for production on purpose (export_logs.py) call print_host()
instead, so the person running them sees which database they reached.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The Neon dev branch's endpoint name. Production has a different endpoint.
DEV_MARKER = "ep-jolly-credit"


def host_of(url) -> str:
    """The host part of a SQLAlchemy URL (object or string). Pure."""
    from sqlalchemy.engine import make_url

    return make_url(url).host or ""


def is_dev_host(host: str) -> bool:
    """True when the host is the dev branch. Pure."""
    return DEV_MARKER in (host or "")


def database_host() -> str:
    """The host the app's engine connects to. Loads .env the way the app does."""
    from app.db import engine

    return host_of(engine.url)


def print_host() -> str:
    host = database_host()
    print(f"database host: {host}")
    return host


def require_dev() -> str:
    """Print the host; exit unless it is the dev branch."""
    host = print_host()
    if not is_dev_host(host):
        sys.exit(f"STOPPED: this script only runs against the dev branch "
                 f"({DEV_MARKER}). Nothing was read or written.")
    return host


if __name__ == "__main__":
    require_dev()
    print("ok: dev branch")
