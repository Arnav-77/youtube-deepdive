"""Add ONE captioned video to a running DeepDive server, from the laptop.

    python evaluation/add_video_live.py "https://www.youtube.com/watch?v=VIDEO_ID" --dry-run
    python evaluation/add_video_live.py "https://www.youtube.com/watch?v=VIDEO_ID"

Why this exists: YouTube blocks the live server from fetching captions
(RequestBlocked, 14 Oct 2026), but not this laptop. So the laptop fetches the
captions and hands them over. Two routes:

  link route (default). The captions are written out in the same plain-text
      shape as YouTube's "Show transcript" panel and sent to the public
      endpoint POST /videos/paste, with no credentials. The server parses and
      chunks them and embeds them in the background; this script waits and
      then checks that the video is listed. The video is labelled "not
      checked" on the page and has no title (the paste route stores none),
      because it came in through the visitor path. With --admin the
      X-Paste-Token header is sent too (loaded from PASTE_TOKEN by
      evaluation/paste_token.py, never printed): the limits are skipped and
      the page shows no "not checked" label (added_by is "admin").

  --direct --yes. The admin route, as the server's own POST /videos does it,
      but run on the laptop: ingest_video (captions, title from oEmbed,
      added_by="admin"), store_chunks, then embed_missing_chunks. It writes to
      the database that DATABASE_URL points at, so it prints the host first
      and needs --yes. Use it for a captioned video with a title and the label "captions":

          DATABASE_URL='<url>' python evaluation/add_video_live.py LINK --direct --yes

Only one video per run, never in a loop: YouTube rate limits the laptop
(CLAUDE.md). Nothing is written to disk. The transcript is third-party text
and is never printed or saved here.
"""

import argparse
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_BASE_URL = "https://youtube-deepdive.onrender.com"
VISITOR_CHUNK_LIMIT = 150      # app/limits.py PUBLIC_PASTE_CHUNK_LIMIT
POLL_EVERY_S = 10
POLL_FOR_S = 600


def clean(text: str) -> str:
    """One line of plain text: newlines and control characters become spaces. Pure."""
    kept = [" " if unicodedata.category(c).startswith("C") else c for c in str(text)]
    return " ".join("".join(kept).split())


def stamp(seconds: float) -> str:
    """Panel time for a start in seconds: whole seconds, m:ss (minutes up to 999). Pure."""
    total = int(seconds)
    if total >= 1000 * 60:
        raise ValueError("a start time past 999 minutes cannot be written as m:ss")
    return f"{total // 60}:{total % 60:02d}"


def to_panel_text(snippets: list[dict]) -> str:
    """Caption snippets as panel text, one line per snippet: 'm:ss words'. Pure.

    The words go on the same line as the time, so a caption that itself
    starts like a time ('5:30 pm') cannot be mistaken for a new segment.
    Snippets with no words are dropped. Starts are floored to whole seconds,
    the only precision the panel format has, so the order never goes
    backwards.
    """
    lines = []
    for s in snippets:
        words = clean(s.get("text", ""))
        if words:
            lines.append(f"{stamp(float(s['start']))} {words}")
    return "\n".join(lines) + "\n"


def describe(video_id: str, snippets: list[dict], chunk_count: int) -> str:
    last = snippets[-1]
    minutes = (float(last["start"]) + float(last["duration"])) / 60
    return (f"{video_id}: {len(snippets)} caption snippets, about {minutes:.1f} minutes, "
            f"{chunk_count} chunks of about 1,000 characters")


def run_direct(video_id: str, yes: bool) -> None:
    """The server's POST /videos, run here. Writes to the database DATABASE_URL names."""
    from app.chunking import DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP, chunking_run_label, store_chunks
    from app.db import SessionLocal
    from app.embeddings import embed_missing_chunks
    from app.youtube import ingest_video
    from evaluation.dev_guard import print_host

    host = print_host()
    if not yes:
        sys.exit("STOPPED: --direct writes to the database above. Add --yes to go ahead.")
    with SessionLocal() as session:
        video = ingest_video(session, video_id, added_by="admin")
        chunks = store_chunks(session, video)
        run = chunking_run_label(DEFAULT_CHUNK_SIZE, DEFAULT_OVERLAP)
        embedded = embed_missing_chunks(session, video.video_id, run)
        print(f"stored {video.video_id} on {host}: title={video.title!r}, source={video.source}, "
              f"added_by={video.added_by}, {len(chunks)} chunks, {embedded} newly embedded")


def run_link(video_id: str, base: str, snippets: list[dict], text: str, chunk_count: int,
             admin: bool) -> None:
    import httpx

    headers = {}
    if admin:
        from evaluation.paste_token import load_paste_token
        token = load_paste_token()
        if not token:
            sys.exit("STOPPED: --admin needs PASTE_TOKEN in the environment or .env")
        headers["X-Paste-Token"] = token
    elif chunk_count > VISITOR_CHUNK_LIMIT:
        sys.exit(f"STOPPED: {chunk_count} chunks is over the visitor limit of {VISITOR_CHUNK_LIMIT}. "
                 "Pick a shorter video, or use --admin.")

    url = f"https://youtu.be/{video_id}"
    r = httpx.post(f"{base}/videos/paste", json={"url": url, "transcript": text},
                   headers=headers, timeout=120)
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    print(f"POST /videos/paste: HTTP {r.status_code}, status={body.get('status')}, "
          f"detail={str(body.get('detail') or body.get('message') or '')[:160]!r}")
    if r.status_code not in (200, 202):
        sys.exit(f"STOPPED: the server did not accept the paste ({r.status_code})")

    waited = 0
    status = body.get("status")
    while status != "ready" and waited <= POLL_FOR_S:
        time.sleep(POLL_EVERY_S)
        waited += POLL_EVERY_S
        p = httpx.get(f"{base}/videos/{video_id}", timeout=60).json()
        status = p.get("status")
        print(f"  {waited:>3}s: {status}, embedded {p.get('embedded_count')} of {p.get('chunk_count')}")
    if status != "ready":
        sys.exit("STOPPED: embedding did not finish. Send the same command again to resume.")

    listing = httpx.get(f"{base}/videos", timeout=60).json()
    row = next((v for v in listing if v["video_id"] == video_id), None)
    if row is None:
        sys.exit("STOPPED: the video is ready but is not in GET /videos")
    print(f"listed in GET /videos: title={row.get('title')!r}, added_by={row.get('added_by')}, "
          f"checked={row.get('checked')}, duration_seconds={row.get('duration_seconds')}")
    print(f"ask about it: {base}/ui/  (video id {video_id})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("link", help="YouTube link or 11-character video id")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch the captions and check them, send nothing")
    parser.add_argument("--admin", action="store_true",
                        help="link route: also send X-Paste-Token (limits skipped, no 'not checked' label)")
    parser.add_argument("--direct", action="store_true",
                        help="write to the database DATABASE_URL names, like POST /videos")
    parser.add_argument("--yes", action="store_true", help="confirm --direct")
    args = parser.parse_args()

    from app.chunking import chunk_transcript
    from app.paste import parse_paste
    from app.youtube import extract_video_id, fetch_transcript

    video_id = extract_video_id(args.link)
    try:
        snippets, _source = fetch_transcript(video_id)
    except Exception as exc:
        sys.exit(f"STOPPED: could not fetch captions for {video_id}: {type(exc).__name__}. "
                 "If YouTube is blocking this laptop, use the paste way instead.")
    if not snippets:
        sys.exit(f"STOPPED: {video_id} has no captions")

    text = to_panel_text(snippets)
    # The server will run exactly this parser: check the text before sending it.
    parsed = parse_paste(text)
    chunk_count = len(chunk_transcript(parsed.snippets))
    print(describe(video_id, parsed.snippets, chunk_count))

    if args.dry_run:
        print("dry run: nothing sent, nothing written")
        return
    if args.direct:
        run_direct(video_id, args.yes)
    else:
        run_link(video_id, args.base_url.rstrip("/"), snippets, text, chunk_count, args.admin)


if __name__ == "__main__":
    main()
