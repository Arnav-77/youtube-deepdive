"""Limits on public pasting, and hiding videos. Pure rules plus one count.

Who these apply to: a request with the admin token (see app/main.py) skips
the chunk limit and the daily cap. Everyone else is a visitor.

Settings are read once, at startup, and a bad value stops the app (rule 7):
an unreadable daily cap or a malformed hidden id is a configuration mistake
that should never reach a visitor.
"""

import re

from sqlalchemy import text

# Visitors may add at most this many chunks in one paste.
PUBLIC_PASTE_CHUNK_LIMIT = 150
# Default for PUBLIC_PASTE_DAILY_CHUNKS: chunks stored for visitor pastes in
# any rolling 24 hours, across all visitors.
DEFAULT_PUBLIC_DAILY_CHUNKS = 600

_VIDEO_ID = re.compile(r"[0-9A-Za-z_-]{11}")


def load_daily_cap(raw: str | None) -> int:
    """Read PUBLIC_PASTE_DAILY_CHUNKS. Unset or blank means the default.

    Zero is allowed and means visitors cannot paste at all (admin still can).
    """
    if raw is None or not raw.strip():
        return DEFAULT_PUBLIC_DAILY_CHUNKS
    value = raw.strip()
    if not value.isdigit():
        raise RuntimeError(
            f"PUBLIC_PASTE_DAILY_CHUNKS must be a whole number, got {raw!r}"
        )
    return int(value)


def load_hidden_ids(raw: str | None) -> frozenset[str]:
    """Read HIDDEN_VIDEO_IDS: comma-separated bare 11-character video ids.

    Blank means nothing is hidden. A URL, a typo or a wrong length stops the
    app, because a hidden video that is not actually hidden is the failure
    this setting exists to prevent.
    """
    if raw is None or not raw.strip():
        return frozenset()
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    for video_id in ids:
        if not _VIDEO_ID.fullmatch(video_id):
            raise RuntimeError(
                "HIDDEN_VIDEO_IDS must be comma-separated 11-character video ids "
                f"(not URLs), got an entry of length {len(video_id)}"
            )
    return frozenset(ids)


def public_paste_refusal(
    chunk_count: int, used_today: int, daily_cap: int
) -> tuple[int, str] | None:
    """Why a visitor's new paste is refused, as (status code, message), or None.

    Checks the per-paste limit first (422: this paste is too big, a retry will
    not help) and then the daily cap (429: try again later). The paste must
    fit under the cap in full: a paste that would take the total over the cap
    is refused, not cut short.
    """
    if chunk_count > PUBLIC_PASTE_CHUNK_LIMIT:
        return 422, (
            f"This transcript makes {chunk_count} chunks. Public pastes are "
            f"limited to {PUBLIC_PASTE_CHUNK_LIMIT} chunks each. "
            "Ask the site owner to add this video."
        )
    if used_today + chunk_count > daily_cap:
        return 429, (
            f"Public pastes are limited to {daily_cap} chunks per 24 hours across "
            f"all visitors, and {used_today} have been used. This paste would add "
            f"{chunk_count} more. Try again later."
        )
    return None


# Chunks stored for visitor pastes in the last 24 hours. Counted when stored,
# not when embedded, so two pastes in flight cannot both pass the check.
_VISITOR_CHUNKS_SQL = text("""
    SELECT count(*)
    FROM chunks c
    JOIN videos v ON v.video_id = c.video_id
    WHERE v.added_by = 'visitor'
      AND c.created_at > now() - interval '24 hours'
""")


def visitor_chunks_last_24h(session) -> int:
    return int(session.execute(_VISITOR_CHUNKS_SQL).scalar() or 0)
