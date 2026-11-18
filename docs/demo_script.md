# Demo script (5 minutes)

For the final evaluation on 28 Nov 2026. Questions that answered well in the finish run are in `docs/demo_questions.md`.

## Before the demo

1. **Three minutes early, open https://youtube-deepdive.onrender.com/ui/**. The free server sleeps after 15 idle minutes, and a cold start took 41.5 s when measured (22 Sep). Keep the tab open; any request keeps it awake.
2. Check https://youtube-deepdive.onrender.com/health returns `"status": "ok"` and the commit you deployed.
3. For the paste step, save the transcript of one short captioned video to `~/demo_transcript.txt` (YouTube: "..." under the video, "Show transcript", select the whole panel, copy, paste into the file). Use a video that is **not** already added: a re-paste gets 409. A paste on the live site is real and permanent (chunks are append-only); it can only be hidden afterwards with `HIDDEN_VIDEO_IDS`. The web page has no paste form: pasting is the `POST /videos/paste` endpoint, which takes a JSON body `{"url": ..., "transcript": ...}`.
4. Have this file and `docs/demo_questions.md` open in another window.

## The demo

**0:00 to 0:30. The problem, in one sentence.** Summaries of long videos cannot be checked; this system answers from the transcript only, and every claim links to the moment it was said.

**0:30 to 1:30. The Everest question.** Pick the habits talk (LXaFHI_2Hus, "9 Boring Habits That Will Put You Ahead of 99% of People"). Ask:

> What did the speaker learn from the person who climbed Everest?

Point out the numbered citations in the answer. Say: the model only ever saw five numbered excerpts, with no times; the code turned each number into a link.

**1:30 to 2:00. Click a citation.** It opens YouTube at about 0:51. The speaker is telling the Everest story there. Say: the link starts at the beginning of the cited chunk, so it can land up to a minute before the exact sentence; that offset is measured in the report.

**2:00 to 2:45. A paraphrase that word search misses.** Pick "All Machine Learning algorithms explained in 17 min" (E0Hmnixke2g). Ask:

> How does a model get trained on data where every example already has the right answer attached?

The video calls this supervised learning and the question never uses those words. Say: in the evaluation, word search missed this passage entirely and dense search ranked it first (`evaluation/results/retrieval_human.txt`).

**2:45 to 3:45. The public paste flow.** In a WSL terminal on the laptop, with the venv active, send the saved transcript as a visitor (no token), replacing VIDEO_ID:

```bash
python -c "import httpx,sys; r=httpx.post('https://youtube-deepdive.onrender.com/videos/paste', json={'url': sys.argv[1], 'transcript': open(sys.argv[2]).read()}, timeout=120); print(r.status_code, r.text[:300])" "https://youtu.be/VIDEO_ID" ~/demo_transcript.txt
```

It answers 202 while the chunks are embedded in the background (a batch of 50 chunks takes about half a minute) and 200 when the video is ready; sending the same command again resumes an interrupted embedding. Progress is at `https://youtube-deepdive.onrender.com/videos/VIDEO_ID`. Reload the page: the video appears in the list with the **"not checked"** label, because visitor pastes are never presented as verified captions. Ask one question about it.

Rules to mention if asked: at most 150 chunks per paste (422 above that), 600 visitor chunks per rolling 24 hours across all visitors (429 when reached), an admin token in the `X-Paste-Token` header skips both, a wrong token gets 401, a repeated paste gets 409.

**3:45 to 4:30. The `/docs` page.** Open https://youtube-deepdive.onrender.com/docs. Show `POST /ask`, its response (`citations` with `chunk_id`, `start_seconds` and `url`, and `model`), and `GET /health`. Mention that every request, failures included, writes one `query_logs` row.

**4:30 to 5:00. What was measured, honestly.** Human-labelled retrieval: dense 14/14 against word search 12/14 on the reported set. The rest is in Chapter 7, with AI-labelled numbers kept separate.

## If something goes wrong

**Gemini is busy (503 "high demand").** Nothing to do: the backup model (`gemini-3.5-flash-lite`) answers automatically. In `/docs` the response's `model` field shows which model answered. In the finish run the main model failed on every request and the backup answered all of them. If both fail, `/ask` returns 502; wait 30 seconds and ask again.

**The answer says the excerpts do not contain it.** That is a refusal, not a crash. Ask the Everest question, which was answered, not refused, in every evaluation run that asked it (rows 24 and 56).

**A request hangs for more than a minute.** Known bug: the model call has no timeout (REVIEW.md, "Known bugs"). Reload the page and ask again.

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

Then open http://localhost:8000/ui/ (and http://localhost:8000/docs). The laptop's `.env` points at the Neon dev branch, which holds the same 23 videos. Answers take longer on the laptop (each database round trip to Ohio is about 280 ms). Do not use the paste flow locally unless you mean to add the video to the dev branch.
