# YouTube Deep-Dive

Graded B.Tech minor project (USICT, GGSIPU) by Arnav Soni. Final project file due 23 Nov 2026, final evaluation 28 Nov 2026. Arnav must explain every line of this system out loud in a viva, without notes. Code he cannot explain is a liability, not progress.

Conversational RAG over YouTube transcripts. Every claim in an answer links to the moment in the video it came from.

- Live: https://youtube-deepdive.onrender.com (Render free tier, Docker, 512 MB RAM, 0.1 CPU, sleeps after 15 idle minutes)
- Database: Neon PostgreSQL with pgvector
- Models: gemini/gemini-3.6-flash, backup gemini/gemini-3.5-flash-lite (FALLBACK_MODELS), through LiteLLM
- Embeddings: gemini-embedding-001 at 768 dimensions, plain HTTPS from app/embeddings.py (not LiteLLM)

## Where things are

| File | Job |
| --- | --- |
| run.py | Starts uvicorn on PORT (Render sets it) |
| app/main.py | Endpoints /health, /ready, /videos, /ask; 404 check; citations; request log |
| app/youtube.py | Video id parsing, caption fetch, ingest_video |
| app/chunking.py | chunk_transcript (pure), store_chunks (append only) |
| app/embeddings.py | Embedding calls, pacing, query_embeddings cache |
| app/retrieval.py | dense_search (live), search (word search fallback), hybrid_search (tested, not live) |
| app/generation.py | Prompt rendering, LiteLLM call, citation number check |
| app/prompts/answer_v1.txt | The prompt template |
| app/models.py, app/db.py | Tables and connection |
| alembic/ | Migrations |
| evaluation/golden_set.yaml | Hand-made answer key (23 videos, 5 dev and 18 reported) |
| evaluation/run_eval.py | Retrieval metrics: Recall@k, MRR, citation offset |
| evaluation/log_stats.py | Failure rate, latency, cost from query_logs |

## Commands

Code lives inside WSL at ~/code/deepdive, never under /mnt/c.

- Activate first: `source .venv/bin/activate` (python only exists inside the venv)
- Run locally: `python run.py`, then http://localhost:8000/docs
- Retrieval eval: `python evaluation/run_eval.py --method dense --set reported` and `--set dev`. ALWAYS pass both flags: the defaults are lexical and dev, which print the wrong numbers.
- Log numbers: `python evaluation/log_stats.py`
- After adding a dependency: `pip freeze > requirements.txt`
- There is no test suite yet. When one exists: `pytest`

## Secrets, data and quotas: read before running anything

1. Never read, print, echo or log .env or any environment variable value. Error messages shown to users carry only the exception class name, because database errors embed the connection URL.
2. The production database holds query_logs, the source of every number in the report. It cannot be rebuilt. Never run migrations, INSERT, UPDATE or DELETE against production. Local .env should point to a Neon dev branch. If you are not sure which database a command will touch, stop and ask.
3. Free tier only, spend nothing. Ask before running anything that calls Gemini or the embedding API: /ask, /videos, ingestion, embed_chunks.py, or run_eval.py on questions not yet cached. Embedding limit is 100 texts per minute; batches of 50 every ~33 s.
4. YouTube blocks transcript requests from Render's IP and rate limits the laptop. One video at a time, never in a loop.
5. No new paid or hosted services without asking.

## Design rules that must not be broken

1. The model never writes a timestamp. It cites excerpt numbers [n]; code maps them to chunks and builds `?t=` links. Never send times or chunk ids to the model.
2. The model has no tools, no database access, no function calling. This is the main prompt injection defence; transcripts are untrusted text.
3. render_prompt fills the template in a single pass. Never use sequential .replace() calls.
4. Every /ask request writes exactly one query_logs row, failures included (try/finally). generate() never raises.
5. The chunks table is append only. Never UPDATE or DELETE a chunk row; log rows point at chunk ids forever. A new chunking strategy writes new rows under a new chunking_run label.
6. Status codes: 400 caller error, 404 video not added, 422 invalid input, 502 upstream failure (Gemini, Google, YouTube), 500 only for our own bugs.
7. Invalid configuration stops the app at startup (like RETRIEVAL_METHOD), never on the first real request.
8. Fallbacks stay visible in the log: retrieval_method, model vs model_requested.
9. Schema changes: a new Alembic migration, expand then contract, applied before the code that needs it. Never edit an applied migration.
10. Memory budget: the app plus LiteLLM already use about 250 MB of 512 MB. No torch, no local models, no large dependencies on the server. State the size of any new dependency.

## Evaluation rules

1. No improvement claim without a measurement against the baseline on the labelled set.
2. Never change questions or spans in evaluation/golden_set.yaml unless Arnav asks. Labels are his judgement. Use add_labels.py to append.
3. Tune only on the 5 dev videos. Never move a video between dev and reported.
4. Before any evaluation run, write the prediction into the commit message. Report predictions that failed.
5. Never edit app/prompts/answer_v1.txt. A prompt change is a new file (answer_v2.txt), a new PROMPT_VERSION, and a measured comparison.
6. Generation is not deterministic even at temperature 0. Run each question several times and report the spread.
7. Report every number with its sample size. No P95 from a handful of requests.
8. Latency figures come from production, not the laptop (about 280 ms per database round trip from Delhi to Ohio).

## How to work with Arnav

1. For anything over about 30 lines, plan first and wait for approval.
2. Small changes: one feature per branch, one concern per commit.
3. After each change, explain in plain language what it does and why. Name the alternatives and why they were not chosen. Say what will break (free tier limits, long videos, videos without captions). Define any term he might not know.
4. If he would learn something important by writing a piece himself, say which piece and why, then offer the working version.
5. Give terminal commands one at a time.
6. Do not push, deploy, or touch Render settings unless asked. Docs-only commits end with [skip render].
7. Commit messages say what changed and why.
8. Record each design decision in docs/decisions.md: date, decision, alternatives considered, reason.
9. If a change makes the README or docs/ stale, update them in the same change.
10. Anything written for his supervisor: no em dashes or en dashes.

## Current state (29 Oct 2026)

- Live retrieval: dense (pgvector cosine distance, `<=>`), chunking run fixed_1000_200, top 5. No vector index; exact scan.
- 23 videos ingested, 1,574 chunks embedded. 19 labelled questions on 5 videos (14 reported on 4 videos, 5 dev on 1).
- Reported set, dense: Recall@5 14/14, MRR 0.917, median citation offset 15 s, late citations 5/14.
- Known gaps: a refusal is logged as status "answered"; model names are not validated at startup; no tests; the live server cannot add videos; grounding is not measured.

## Build order to 23 Nov

1. Tests for pure functions (no network, no database)
2. Grounding rubric and measurement
3. Answer checker (critic) using the same grounding definition
4. Simple web page
5. Paste-transcript ingestion
6. Follow-up questions (query rewriting, both versions logged)
7. Topic-shift chunking, tuned on dev only
8. Cross-video comparison: keep or cut on 14 Nov
