# Demo script (5 minutes)

For the final evaluation on 28 Nov 2026. The site is deployed (commit `65b7047` or later on https://youtube-deepdive.onrender.com). Questions that answered well are in `docs/demo_questions.md`.

## Before the demo

1. **Three minutes early, open https://youtube-deepdive.onrender.com/ui/**. The free server sleeps after 15 idle minutes, and a cold start took 41.5 s when measured (22 Sep). Keep the tab open; any request keeps it awake.
2. Open https://youtube-deepdive.onrender.com/health in another tab. It should show `"status": "ok"`.
3. Check the habits talk answers: pick it, ask the Everest question once, and see `▶ 0:51` under the answer. This also warms the model.
4. Have the commands under "Adding a video live" ready in a WSL terminal (`cd ~/code/deepdive && source .venv/bin/activate`).

## The demo

**0:00 to 0:30. The problem, in one sentence.** Summaries of long videos cannot be checked; this system answers from the transcript only, and every claim links to the moment it was said.

**0:30 to 1:30. The Everest question.** Pick the habits talk (it is the one with the "DO THIS EVERYDAY" thumbnail; video id `LXaFHI_2Hus`). The videos show "Untitled video" because titles have not been filled in on the production rows; say so if asked. Ask:

> What did the speaker learn from the person who climbed Everest?

Point out the citation button. Say: the model only ever saw five numbered excerpts, with no times; the code turned each number into a link. The line under the answer says which model answered and how long it took.

**1:30 to 2:00. Click the citation.** It opens YouTube at about 0:51, where the speaker tells the Everest story. Say: the link starts at the beginning of the cited chunk, so it can land up to a minute before the exact sentence; that offset is measured in the report.

**2:00 to 2:45. A paraphrase that word search misses.** Pick "All Machine Learning algorithms explained in 17 min" (`E0Hmnixke2g`). Ask:

> How does a model get trained on data where every example comes with the correct answer?

The video calls this supervised learning and the question never uses those words. Say: in the evaluation, word search missed this passage entirely and dense search ranked it first (`evaluation/results/retrieval_human.txt`).

**2:45 to 3:45. A video added by a visitor.** The DNS video (`3cvGhO3FTtc`, "Master DNS in 5 MINS!") was added to the live site through the public paste endpoint and is first in the list, with the **"not checked"** label: a visitor added it, so nobody has verified the text. Pick it and ask:

> What does DNS do when I type a website name into my browser?

It answers with three citation buttons. Rules to mention if asked: at most 150 chunks per paste (422 above that), 600 visitor chunks per rolling 24 hours across all visitors (429 when reached), an admin token in the `X-Paste-Token` header skips both, a wrong token gets 401, a repeated paste gets 409.

**3:45 to 4:30. The `/docs` page.** Open https://youtube-deepdive.onrender.com/docs. Show `POST /ask`, its response (`citations` with `chunk_id`, `start_seconds` and `url`, and `model`), and `GET /health`. Mention that every request, failures included, writes one `query_logs` row.

**4:30 to 5:00. What was measured, honestly.** Human-labelled retrieval: dense 14/14 against word search 12/14 on the reported set. Live latency, 25 requests: median 1.8 s, slowest 48 s. The grounding figures are an AI judge's; only 2 answers were labelled by hand. All of it is in Chapter 7 of the report.

## Adding a video live (to show it working)

The laptop fetches the captions (YouTube blocks the server, not the laptop) and sends them to the server, which chunks and embeds them. Use a captioned video, ideally under 10 minutes. One video at a time; do not loop it, because YouTube rate limits the laptop.

Check it first. This only reads the captions and sends nothing:

```bash
python evaluation/add_video_live.py "https://www.youtube.com/watch?v=VIDEO_ID" --dry-run
```

Then add it. A 5-minute video took about 10 seconds:

```bash
python evaluation/add_video_live.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

It ends with a line starting `listed in GET /videos`. Reload the page: the video is first in the list with "not checked". To skip the visitor limits and the "not checked" label, add `--admin` (it reads `PASTE_TOKEN` from `.env`, never printing it). A video with a title and the label "captions" needs the database route: `DATABASE_URL='<url>' python evaluation/add_video_live.py LINK --direct --yes`, run by you, because it writes to the database it names.

**The paste way**, if YouTube blocks the laptop or the video has no captions the library can read: on the video page click "..." under the video, "Show transcript", select the whole panel, copy, and save it to `~/transcript.txt`. Then send it:

```bash
python -c "import httpx,sys; r=httpx.post('https://youtube-deepdive.onrender.com/videos/paste', json={'url': sys.argv[1], 'transcript': open(sys.argv[2]).read()}, timeout=120); print(r.status_code, r.text[:300])" "https://youtu.be/VIDEO_ID" ~/transcript.txt
```

It answers 202 while embedding and 200 when ready; the same command again resumes an interrupted embedding. Progress is at `https://youtube-deepdive.onrender.com/videos/VIDEO_ID`.

## If something goes wrong

**Gemini is busy (503 "high demand").** The backup model (`gemini-3.5-flash-lite`) answers automatically; the line under the answer names the model. Each model attempt is now cut off after 45 seconds, so the slowest an answer can take is about 90 seconds before a 502, and in the live measurement the slowest took 48 seconds. If you get a 502, wait 30 seconds and ask again.

**The answer says the excerpts do not contain it.** That is a refusal, not a crash. Ask the Everest question, which has answered every time it was asked.

**The live site is down.** Run it on the laptop, one command at a time, in a WSL terminal:

```bash
cd ~/code/deepdive
```

```bash
source .venv/bin/activate
```

```bash
python run.py
```

Then open http://localhost:8000/ui/ (and http://localhost:8000/docs). The laptop's `.env` points at the Neon dev branch, which holds the same 23 benchmark videos plus one pasted CS229 lecture. Answers take longer on the laptop (each database round trip to Ohio is about 280 ms).
