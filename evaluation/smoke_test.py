"""Live smoke test of a running DeepDive server. Public endpoints only.

    python evaluation/smoke_test.py
    python evaluation/smoke_test.py --base-url http://localhost:8000 --out evaluation/results/smoke_test_local.json

Five checks, each recorded with its result:
  1. GET /health answers ok and reports a commit id (compared with
     `git rev-parse HEAD` when run inside the repository).
  2. GET /videos lists videos, each with added_by and checked.
  3. POST /ask on the habits talk (LXaFHI_2Hus) with the Everest question
     answers, and cites excerpt [1] with a link ending ?t=51.
  4. POST /videos/paste with a transcript that has no timestamps gets 422.
  5. GET /videos has the same number of videos afterwards: nothing stored.

It sends ONE question to /ask, so it spends one Gemini call and writes one
query_logs row on the server it targets. It records status codes, the model
name, the server's latency and the citation links; never the answer text.
Writes a JSON file (default evaluation/results/smoke_test_production.json)
and exits 1 if any check fails.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DEFAULT_BASE_URL = "https://youtube-deepdive.onrender.com"
DEFAULT_OUT = Path(__file__).resolve().parent / "results" / "smoke_test_production.json"
QUESTION = "What did the speaker learn from the person who climbed Everest?"
VIDEO = "LXaFHI_2Hus"
BAD_PASTE = {"url": "https://youtu.be/dQw4w9WgXcQ", "transcript": "this is not a transcript"}


def judge(r: dict, head: str | None) -> list[dict]:
    """The five checks from the recorded results. Pure: no network."""
    ask, paste = r["ask"], r["paste"]
    cites = ask.get("citations") or []
    first = next((c for c in cites if c["number"] == 1), None)
    return [
        {"check": "health ok with a commit id", "ok": r["health"].get("status") == "ok" and bool(r["health"].get("commit")),
         "detail": f"commit {r['health'].get('commit')}" + (f", repo HEAD {head[:7]}" if head else "")},
        {"check": "health commit equals the repo's HEAD", "ok": bool(head) and head.startswith(r["health"].get("commit") or "x"),
         "detail": "not compared (not run inside the repository)" if not head else ""},
        {"check": "GET /videos lists videos with added_by and checked",
         "ok": r["videos"]["count"] > 0 and r["videos"]["has_added_by"] and r["videos"]["has_checked"],
         "detail": f"{r['videos']['count']} videos"},
        {"check": "POST /ask answers and cites [1] near ?t=51",
         "ok": ask.get("http") == 200 and ask.get("status") == "answered" and first is not None
               and first["url"].endswith("?t=51"),
         "detail": f"HTTP {ask.get('http')}, model {ask.get('model')}, server latency {ask.get('latency_ms')} ms, "
                   f"links {', '.join(c['url'] for c in cites)}"},
        {"check": "bad paste gets 422", "ok": paste.get("http") == 422,
         "detail": f"HTTP {paste.get('http')}: {paste.get('detail')}"},
        {"check": "bad paste stored nothing", "ok": r["videos_after"]["count"] == r["videos"]["count"]
               and not r["videos_after"]["has_bad_paste_video"],
         "detail": f"{r['videos']['count']} videos before, {r['videos_after']['count']} after"},
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    import httpx

    started = time.perf_counter()
    h = httpx.get(f"{base}/health", timeout=150)
    health = h.json() if h.status_code == 200 else {}
    health["http"] = h.status_code
    health["seconds"] = round(time.perf_counter() - started, 1)

    def videos():
        resp = httpx.get(f"{base}/videos", timeout=60)
        data = resp.json() if resp.status_code == 200 else []
        return {"http": resp.status_code, "count": len(data), "first": data[0]["video_id"] if data else None,
                "untitled": sum(1 for v in data if not v.get("title")),
                "has_added_by": bool(data) and all("added_by" in v for v in data),
                "has_checked": bool(data) and all("checked" in v for v in data),
                "has_bad_paste_video": any(v["video_id"] == "dQw4w9WgXcQ" for v in data)}

    before = videos()
    a = httpx.post(f"{base}/ask", json={"video": VIDEO, "question": QUESTION}, timeout=150)
    body = a.json() if a.status_code == 200 else {}
    ask = {"http": a.status_code, "status": body.get("status"), "model": body.get("model"),
           "latency_ms": body.get("latency_ms"),
           "citations": [{"number": c["number"], "start_seconds": c["start_seconds"], "url": c["url"]}
                         for c in body.get("citations", [])]}
    p = httpx.post(f"{base}/videos/paste", json=BAD_PASTE, timeout=60)
    paste = {"http": p.status_code, "detail": (p.json().get("detail") if p.status_code != 200 else None)}
    after = videos()

    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              cwd=Path(__file__).resolve().parent.parent).stdout.strip() or None
    except OSError:
        head = None
    raw = {"health": health, "videos": before, "ask": ask, "paste": paste, "videos_after": after}
    checks = judge(raw, head)
    result = {"base_url": base, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "raw": raw, "checks": checks, "all_ok": all(c["ok"] for c in checks)}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    for c in checks:
        print(f"{'PASS' if c['ok'] else 'FAIL'}  {c['check']}  ({c['detail']})")
    print(f"wrote {out}")
    sys.exit(0 if result["all_ok"] else 1)


if __name__ == "__main__":
    main()
