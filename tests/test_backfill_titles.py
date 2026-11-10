"""evaluation/backfill_titles.py: dry run by default, writes only with --write,
oEmbed before the override file, and every failure reported with its status.

Offline: the session and the lookup are fakes, the pause is zero, and override
files are written to pytest's temporary directory. Nothing here reaches
YouTube or a database.
"""

import pytest

from app.models import Video
from app.youtube import TitleLookup
from evaluation.backfill_titles import (
    backfill,
    load_overrides,
    parse_args,
    pending_videos,
)


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


def _lookup(results):
    """A fake oEmbed lookup. `results` maps id -> title, or id -> (title, status)."""

    def lookup(video_id):
        value = results.get(video_id)
        if isinstance(value, tuple):
            return TitleLookup(*value)
        if value is None:
            return TitleLookup(None, "HTTP 404")
        return TitleLookup(value, "ok")

    return lookup


def _run(videos, write, lookup, overrides=None):
    printed = []
    summary = backfill(
        _FakeSession(videos),
        write=write,
        lookup=lookup,
        overrides=overrides,
        pause=0,
        out=printed.append,
    )
    return summary, printed


# --- what gets written -----------------------------------------------------


def test_dry_run_prints_ids_and_titles_and_writes_nothing():
    videos = [_video("AAAAAAAAAAA"), _video("BBBBBBBBBBB")]
    session = _FakeSession(videos)
    printed = []

    backfill(
        session,
        write=False,
        lookup=_lookup({"AAAAAAAAAAA": "First", "BBBBBBBBBBB": "Second"}),
        pause=0,
        out=printed.append,
    )

    assert printed == [
        "AAAAAAAAAAA  First  [oEmbed]",
        "BBBBBBBBBBB  Second  [oEmbed]",
    ]
    assert [v.title for v in videos] == [None, None]
    assert session.commits == 0


def test_write_sets_each_title_and_commits_after_each():
    videos = [_video("AAAAAAAAAAA"), _video("BBBBBBBBBBB")]
    session = _FakeSession(videos)

    backfill(
        session,
        write=True,
        lookup=_lookup({"AAAAAAAAAAA": "First", "BBBBBBBBBBB": "Second"}),
        pause=0,
        out=lambda line: None,
    )

    assert [v.title for v in videos] == ["First", "Second"]
    assert session.commits == 2


def test_only_videos_with_a_null_title_are_selected():
    session = _FakeSession([])

    pending_videos(session)

    assert "videos.title IS NULL" in str(session.statements[0])


# --- oEmbed, then the override file ---------------------------------------


def test_an_override_fills_in_when_oembed_gives_no_title():
    videos = [_video("AAAAAAAAAAA")]

    summary, printed = _run(
        videos,
        write=True,
        lookup=_lookup({}),  # oEmbed: HTTP 404
        overrides={"AAAAAAAAAAA": "Hand-written"},
    )

    assert videos[0].title == "Hand-written"
    assert summary == {"found": 0, "overridden": 1, "failed": []}
    assert printed == ["AAAAAAAAAAA  Hand-written  [video_titles.yaml]"]


def test_oembed_wins_over_an_override_for_the_same_video():
    videos = [_video("AAAAAAAAAAA")]

    summary, _ = _run(
        videos,
        write=True,
        lookup=_lookup({"AAAAAAAAAAA": "From YouTube"}),
        overrides={"AAAAAAAAAAA": "Hand-written"},
    )

    assert videos[0].title == "From YouTube"
    assert summary["found"] == 1 and summary["overridden"] == 0


def test_a_video_neither_source_can_title_stays_null_and_is_reported_with_its_status():
    videos = [_video("AAAAAAAAAAA"), _video("BBBBBBBBBBB")]

    summary, printed = _run(
        videos,
        write=True,
        lookup=_lookup({
            "AAAAAAAAAAA": (None, "HTTP 401"),
            "BBBBBBBBBBB": (None, "HTTP 401"),
        }),
        overrides={},
    )

    assert videos[0].title is None and videos[1].title is None
    assert summary["failed"] == [("AAAAAAAAAAA", "HTTP 401"), ("BBBBBBBBBBB", "HTTP 401")]
    assert "AAAAAAAAAAA  FAILED: HTTP 401" in printed


def test_each_failure_keeps_its_own_status():
    videos = [_video("AAAAAAAAAAA"), _video("BBBBBBBBBBB")]
    lookup = lambda vid: (
        TitleLookup(None, "HTTP 404") if vid == "AAAAAAAAAAA" else TitleLookup(None, "ConnectError")
    )

    summary, _ = _run(videos, write=False, lookup=lookup)

    assert summary["failed"] == [
        ("AAAAAAAAAAA", "HTTP 404"),
        ("BBBBBBBBBBB", "ConnectError"),
    ]


def test_summary_counts_each_source():
    videos = [_video("AAAAAAAAAAA"), _video("BBBBBBBBBBB"), _video("CCCCCCCCCCC")]

    summary, _ = _run(
        videos,
        write=False,
        lookup=_lookup({"AAAAAAAAAAA": "One"}),
        overrides={"BBBBBBBBBBB": "Two"},
    )

    assert summary["found"] == 1
    assert summary["overridden"] == 1
    assert [vid for vid, _ in summary["failed"]] == ["CCCCCCCCCCC"]


# --- the override file -----------------------------------------------------


def test_missing_file_means_no_overrides(tmp_path):
    assert load_overrides(tmp_path / "nope.yaml") == {}


def test_commented_template_means_no_overrides(tmp_path):
    path = tmp_path / "titles.yaml"
    path.write_text("# only comments\n", encoding="utf-8")
    assert load_overrides(path) == {}


def test_reads_id_and_title_pairs(tmp_path):
    path = tmp_path / "titles.yaml"
    path.write_text('LXaFHI_2Hus: Everest base camp\nabc12345678: "2024"\n', encoding="utf-8")

    assert load_overrides(path) == {"LXaFHI_2Hus": "Everest base camp", "abc12345678": "2024"}


@pytest.mark.parametrize(
    "text, message",
    [
        ("- just\n- a list\n", "expected lines of 'video_id: title'"),
        ("LXaFHI_2Hus:\n", "needs a non-empty title"),
        ("LXaFHI_2Hus: '   '\n", "needs a non-empty title"),
        ("LXaFHI_2Hus: 2024\n", "quote it"),  # YAML reads this as a number
        ("12345678901: Title\n", "is not a video id"),  # YAML reads this as an integer
    ],
)
def test_a_bad_entry_stops_the_run_with_a_message(tmp_path, text, message):
    path = tmp_path / "titles.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_overrides(path)


# --- arguments -------------------------------------------------------------


def test_default_is_a_dry_run():
    assert parse_args([]).write is False


def test_write_flag_turns_writing_on():
    assert parse_args(["--write"]).write is True
