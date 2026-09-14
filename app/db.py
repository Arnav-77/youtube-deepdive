"""Database connection and session management.

Everything that talks to Postgres goes through here. Nothing else in the
app should import sqlalchemy directly or read DATABASE_URL.
"""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Reads .env into environment variables. On Render there is no .env file --
# DATABASE_URL is set in the dashboard instead -- so this finds nothing and
# does nothing. Same code path works in both places.
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

# Fail loudly and early. Without this check DATABASE_URL is None, SQLAlchemy
# fails later with an obscure error about a NoneType URL, and you debug the
# wrong layer for twenty minutes.
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not set. Check your .env file.")

# The engine owns the connection pool. Create ONE per application, never one
# per request -- the whole point of a pool is that connections are reused.
engine = create_engine(
    DATABASE_URL,
    # Neon's free tier sleeps when idle and drops open connections, leaving
    # dead ones sitting in our pool. pool_pre_ping sends a cheap test query
    # before handing a connection out and silently replaces it if it's dead.
    # Without this, the first request after any quiet period fails.
    pool_pre_ping=True,
    # Cap concurrent connections. Serverless Postgres has a low connection
    # ceiling and we're already going through Neon's pooler.
    pool_size=5,
    # 0 means pool_size is a hard limit, not a soft one. An app that opens
    # connections without limit hits "too many connections" during its own
    # evaluation runs.
    max_overflow=0,
)

# Factory that produces Session objects. A Session is one unit of work:
# it tracks changes and writes them on commit.
SessionLocal = sessionmaker(
    bind=engine,
    # Don't auto-send pending changes before every query. We control when
    # writes happen, via commit().
    autoflush=False,
    # Keep object attributes readable after commit. By default SQLAlchemy
    # expires them, so touching an attribute triggers a fresh SELECT -- an
    # extra round trip we don't want on the logging path.
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class all ORM models inherit from.

    SQLAlchemy collects table definitions on Base.metadata, which is what
    Alembic reads to work out what migrations to generate.
    """


def get_session():
    """FastAPI dependency: yields a session and always closes it.

    The try/finally matters. If a request raises, the finally block still
    returns the connection to the pool. Without it a handful of errors
    exhausts the pool and the app stops serving.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()