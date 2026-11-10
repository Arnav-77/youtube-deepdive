"""Tests for app/jobs.py and the resume behaviour of embed_missing_chunks.

No database, no network, no Gemini. The session is a small fake that records
commits, so a crash between batches can be simulated and the restart checked.
"""

import threading
from types import SimpleNamespace

import pytest

from app import embeddings, jobs
from app.embeddings import EmbeddingError, MODEL_KEY, embed_missing_chunks
from app.jobs import derive_status


# --- derive_status ------------------------------------------------------------

@pytest.mark.parametrize(
    "exists, total, embedded, running, expected",
    [
        (False, 0, 0, False, "not_found"),
        (False, 5, 5, True, "not_found"),
        (True, 5, 5, False, "ready"),
        (True, 5, 5, True, "ready"),          # finished before the thread cleared its flag
        (True, 5, 2, True, "embedding"),
        (True, 5, 2, False, "incomplete"),    # restart: nothing running, resend to resume
        (True, 5, 0, False, "incomplete"),
        (True, 0, 0, False, "incomplete"),    # a video with no chunks is never "ready"
    ],
)
def test_derive_status(exists, total, embedded, running, expected):
    assert derive_status(exists, total, embedded, running) == expected


# --- start_embedding: one job per video --------------------------------------

@pytest.fixture
def fake_db(monkeypatch):
    """Replace the database session and the embedding call inside jobs."""
    sessions = []

    class _Session:
        def __enter__(self):
            sessions.append(self)
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(jobs, "SessionLocal", _Session)
    return sessions


def test_second_start_for_a_running_video_is_refused(monkeypatch, fake_db):
    release = threading.Event()
    calls = []

    def slow_embed(session, video_id, run):
        calls.append(video_id)
        release.wait(timeout=5)
        return 0

    monkeypatch.setattr(jobs, "embed_missing_chunks", slow_embed)

    assert jobs.start_embedding("vid00000001", "fixed_1000_200") is True
    assert jobs.is_running("vid00000001")
    assert jobs.start_embedding("vid00000001", "fixed_1000_200") is False

    release.set()
    # Wait for the thread to finish and clear its flag.
    for _ in range(200):
        if not jobs.is_running("vid00000001"):
            break
        threading.Event().wait(0.01)
    assert not jobs.is_running("vid00000001")
    assert calls == ["vid00000001"]  # the refused second start never ran


def test_a_failing_job_clears_its_flag_and_prints_only_the_class_name(monkeypatch, fake_db, capsys):
    def failing_embed(session, video_id, run):
        raise EmbeddingError("secret detail that must not be printed")

    monkeypatch.setattr(jobs, "embed_missing_chunks", failing_embed)

    assert jobs.start_embedding("vid00000002", "fixed_1000_200") is True
    for _ in range(200):
        if not jobs.is_running("vid00000002"):
            break
        threading.Event().wait(0.01)

    out = capsys.readouterr().out
    assert not jobs.is_running("vid00000002")
    assert "EmbeddingError" in out
    assert "secret detail" not in out


# --- resume after a crash: embed_missing_chunks is the restart story -------

class _Row(SimpleNamespace):
    pass


class _FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def all(self):
        return self._rows


class _FakeSession:
    """Remembers committed embeddings, the way Postgres would after each commit."""

    def __init__(self, chunks):
        self.chunks = chunks            # list of (id, text), in chunk_index order
        self.committed = set()          # chunk ids with a stored embedding
        self.pending = set()
        self.commit_count = 0

    def _missing_rows(self):
        return [_Row(id=cid, text=text) for cid, text in self.chunks if cid not in self.committed]

    def execute(self, statement, params=None):
        if statement is embeddings._MISSING_SQL:
            assert params["model"] == MODEL_KEY
            return _FakeResult(self._missing_rows())
        if statement is embeddings._INSERT_SQL:
            for row in params:
                self.pending.add(row["chunk_id"])
            return _FakeResult()
        raise AssertionError("unexpected SQL in test")

    def commit(self):
        self.committed |= self.pending
        self.pending = set()
        self.commit_count += 1


def _chunks(n):
    return [(i + 1, f"chunk text {i}") for i in range(n)]


def test_restart_embeds_only_the_chunks_that_were_never_committed(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)

    sent = []
    fail_on_second_call = {"calls": 0}

    def fake_batch(texts, task_type, attempts=None, timeout=None):
        fail_on_second_call["calls"] += 1
        if fail_on_second_call["calls"] == 2:
            # The process dies here, mid-job.
            raise EmbeddingError("simulated crash")
        sent.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(embeddings, "_embed_batch", fake_batch)

    session = _FakeSession(_chunks(120))  # 3 batches of 50, 50, 20

    with pytest.raises(EmbeddingError):
        embed_missing_chunks(session, "vid00000003", "fixed_1000_200")

    # Batch 1 was committed; batch 2 died before its commit.
    assert session.committed == set(range(1, 51))

    # Restart: the same request runs again and must send only the 70 missing chunks.
    sent.clear()
    fail_on_second_call["calls"] = 0
    monkeypatch.setattr(
        embeddings,
        "_embed_batch",
        lambda texts, task_type, attempts=None, timeout=None: (
            sent.append(list(texts)) or [[1.0, 0.0] for _ in texts]
        ),
    )

    done = embed_missing_chunks(session, "vid00000003", "fixed_1000_200")

    assert done == 70
    sent_texts = [t for batch in sent for t in batch]
    assert sent_texts == [f"chunk text {i}" for i in range(50, 120)]
    assert session.committed == set(range(1, 121))


def test_a_finished_video_sends_nothing_on_resend(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)

    def must_not_be_called(texts, task_type, attempts=None, timeout=None):
        raise AssertionError("no chunk is missing, so no API call is allowed")

    monkeypatch.setattr(embeddings, "_embed_batch", must_not_be_called)

    session = _FakeSession(_chunks(10))
    session.committed = set(range(1, 11))

    assert embed_missing_chunks(session, "vid00000004", "fixed_1000_200") == 0


# --- questions during embedding: lexical fallback, no question embedding ----

def test_question_during_embedding_is_logged_as_lexical_fallback(monkeypatch):
    import app.main as main
    import app.retrieval as retrieval

    monkeypatch.setattr(main, "RETRIEVAL_METHOD", "dense")
    monkeypatch.setattr(main, "search", lambda *a, **k: [])

    def must_not_embed(*args, **kwargs):
        raise AssertionError("the question must not be embedded while chunks are missing")

    monkeypatch.setattr(retrieval, "embed_query", must_not_embed)

    class _Scalar:
        def scalar(self):
            return 3  # three chunks of this video have no embedding yet

    class _Session:
        def execute(self, statement, params=None):
            return _Scalar()

    hits, label = main._retrieve(_Session(), "vid00000005", "what is hashing?", "fixed_1000_200", 5)

    assert hits == []
    assert label == "lexical_fallback:MissingEmbeddings"
