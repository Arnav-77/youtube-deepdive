"""evaluation/backfill_titles.py: dry run by default, writes only with --write.

Offline: the session and the title lookup are fakes, and the pause is zero.
Nothing here reaches YouTube or a database.
"""

from app.models import Video
from evaluation.backfill_titles import backfill, parse_args, pending_videos


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, videos):
        self._videos = videos
        self.statements = []
        self.commits = 0

    def scalars(self, statement):
        self.statements.append(statement)
        return _FakeScalars(self._videos)

    def commit(self):
        self.commits += 1


def _video(video_id):
    return Video(video_id=video_id, title=None, source="captions", raw_transcript=[])


def _lookup(titles):
    """A fake oEmbed lookup: returns the title for each id, or None."""
    return lambda video_id: titles.get(video_id)


def test_dry_run_prints_ids_and_titles_and_writes_nothing():
    videos = [_video("AAAAAAAAAAA"), _video("BBBBBBBBBBB")]
    session = _FakeSession(videos)
    printed = []

    found, missing = backfill(
        session,
        write=False,
        fetch=_lookup({"AAAAAAAAAAA": "First", "BBBBBBBBBBB": "Second"}),
        pause=0,
        out=printed.append,
    )

    assert (found, missing) == (2, 0)
    assert printed == ["AAAAAAAAAAA  First", "BBBBBBBBBBB  Second"]
    assert [v.title for v in videos] == [None, None]
    assert session.commits == 0


def test_write_sets_each_title_and_commits_after_each():
    videos = [_video("AAAAAAAAAAA"), _video("BBBBBBBBBBB")]
    session = _FakeSession(videos)

    found, missing = backfill(
        session,
        write=True,
        fetch=_lookup({"AAAAAAAAAAA": "First", "BBBBBBBBBBB": "Second"}),
        pause=0,
        out=lambda line: None,
    )

    assert (found, missing) == (2, 0)
    assert [v.title for v in videos] == ["First", "Second"]
    assert session.commits == 2


def test_a_video_without_a_title_is_left_null_and_reported():
    videos = [_video("AAAAAAAAAAA"), _video("BBBBBBBBBBB")]
    session = _FakeSession(videos)
    printed = []

    found, missing = backfill(
        session,
        write=True,
        fetch=_lookup({"BBBBBBBBBBB": "Second"}),
        pause=0,
        out=printed.append,
    )

    assert (found, missing) == (1, 1)
    assert videos[0].title is None
    assert videos[1].title == "Second"
    assert "AAAAAAAAAAA  (no title; left NULL)" in printed
    assert session.commits == 1


def test_only_videos_with_a_null_title_are_selected():
    session = _FakeSession([])

    pending_videos(session)

    sql = str(session.statements[0])
    assert "videos.title IS NULL" in sql


def test_default_is_a_dry_run():
    assert parse_args([]).write is False


def test_write_flag_turns_writing_on():
    assert parse_args(["--write"]).write is True
