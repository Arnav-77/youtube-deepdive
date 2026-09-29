# YouTube Deep-Dive

A conversational retrieval-augmented generation (RAG) system over YouTube
transcripts. Ask a question about a video; get an answer built only from what
was actually said, with citations that link to the moment in the video where
each claim was made.

**Live:** https://youtube-deepdive.onrender.com/docs

The service runs on a free tier and sleeps when idle, so the first request
after a quiet period takes around 40 seconds. Later requests are immediate.

---

## The problem

Tools that summarise YouTube videos give you one block of text and stop. They
do not answer follow-up questions, they cannot point you to the moment a claim
was made, and they give you no way to check whether the summary reflects the
video or the model's own knowledge.

This project targets the third problem in particular. An answer is only useful
if you can verify it, so every claim carries a timestamp you can click.

## Pipeline

YouTube URL
→ transcript fetch (youtube-transcript-api)
→ chunking with timestamp metadata
→ retrieval (Postgres full-text search)
→ generation (Gemini via LiteLLM)
→ answer with citations resolved to video timestamps
→ request logged to Postgres


## Design decisions

These are the choices that shaped the system, with the reasoning behind them.

**The model never writes a timestamp.** It cites excerpt numbers (`[1]`,
`[2]`), and the application maps those back to chunk IDs and renders
timestamps from the database. The excerpts sent to the model contain no
timestamps at all. A fabricated timestamp is therefore impossible by
construction rather than discouraged by instruction: a capability that does not
exist cannot be misused.

**Prompt injection is treated as architectural, not textual.** Transcripts are
untrusted text written by strangers, and a video can contain spoken or
captioned instructions aimed at the model. The defences, strongest first: the
model has no tools, no database access and no function calling, so a successful
injection can produce bad text but never a bad action; citation numbers are
validated in code against the excerpts actually sent; the prompt template is
filled in a single pass, so untrusted text cannot rewrite the prompt's
structure. The instruction telling the model to treat excerpts as data is the
weakest layer and is included only as defence in depth.

**Every request is logged, including the ones that fail.** A `finally` block
writes the row on every path. Question, retrieved chunk IDs, prompt version,
model requested, model that answered, token counts, latency, estimated cost and
error all persist. The evaluation report is built from this table, and it
cannot be reconstructed after the fact, so it was built before anything else
worked.

**Chunks are append-only.** Every chunk row carries the chunking run that
produced it, so a second chunking strategy is written alongside the first
rather than replacing it. Retrieval requires the run to be named explicitly, so
two strategies can never be silently mixed. This is what makes the comparison
between chunking strategies reproducible from the database months later.

**Caption timestamps are computed from the next snippet, not from duration.**
YouTube's caption snippets overlap: `duration` measures how long text stays
visible on screen, not how long it is spoken, so `start + duration` is not when
the speech ends. Using it puts every citation several seconds late. The fix is
to take a snippet's end from the start of the next one. This was found by
inspecting real data and confirmed by opening the video at the timestamp; no
unit test would have caught it, because the wrong timestamps are internally
consistent and correctly typed.

**Two datastores, deliberately.** Postgres on Neon holds transcripts, chunks
and logs. Render's free Postgres deletes itself 30 days after creation, and
this project runs for 13 weeks, so the platform would have destroyed the
logging table twice before submission.

## Measured results

Numbers from the deployed system, not estimates.

| Measurement | Value |
|---|---|
| Cold start (idle service to first response) | 41.5 s |
| Warm response, `/health` | 0.32 s |
| End-to-end `/ask`, same request, observed range | 3.6 s to 27.1 s |
| Application's own share of that latency | under 1 s |
| Benchmark corpus | 16 videos, ~1,030 chunks |

The latency range is the interesting result. The same question, with identical
retrieval and identical code, varied roughly sevenfold across runs. A
first-call-after-startup effect was hypothesised and ruled out by experiment:
after a cold start, two identical back-to-back requests took 11.7 s and then
22.2 s, and a startup cost cannot make the second request slower. Generation is
therefore over 90% of every request, and the system's P95 latency is
effectively the model provider's P95. No amount of optimising this codebase
would move it.

Retrieval quality figures are not reported yet. The evaluation harness runs and
produces Recall@k, MRR and citation offset, but the benchmark is only partly
labelled, and a number from two questions would not mean anything.

## Evaluation

Retrieval is measured against a hand-written golden set: for each question, the
timestamp span where the answer actually is.

**Labels are timestamp spans, not chunk IDs.** A chunk ID belongs to one
chunking run, so a label naming a chunk cannot score a different chunking
strategy at all. Seconds belong to the video, so one label scores every
strategy.

**The benchmark is split into a development set and a reported set, and videos
never move between them.** All tuning happens on the development videos. The
reported videos are evaluated, never tuned against. Without that separation,
reported results would measure how well the system does on the videos it was
tuned on.

**Labels are written by hand.** Using the system's own retrieval to suggest
where answers are would be faster and would make every measurement circular.

Three metrics are reported together, because each hides something the others
catch. Recall@k asks whether the right passage was retrieved at all. MRR asks
whether it was near the top. Citation offset measures how far the citation
lands from where the answer actually starts, signed, because a citation that
lands late drops the user into the middle of an answer they may not realise
they missed the start of.

## Status

Built and deployed:

- Transcript ingestion with caption-overlap correction
- Fixed-size chunking with timestamp metadata, as a measured baseline
- Lexical retrieval (Postgres full-text search)
- Grounded generation with code-validated citations
- Complete per-request logging
- Retrieval evaluation harness
- Benchmark video set, 16 of 25 chosen and ingested

In progress:

- Hand-labelling the golden set
- Dense retrieval via a hosted embedding API
- Topic-shift chunking, measured against the fixed-size baseline
- Hybrid retrieval and cross-encoder reranking

Planned:

- Runtime grounding critic
- Cross-video synthesis across 2-3 videos on one topic
- Tracing, cost tracking and prompt versioning per trace

## Limitations

**Citations resolve to a chunk, not to a sentence.** A 1000-character chunk can
span over a minute, so a citation can land up to a minute before the claim it
supports. Smaller or semantically-bounded chunks reduce this; sentence-level
attribution would eliminate it. The offset is measured rather than described.

**Transcript ingestion depends on an unofficial interface.** YouTube publishes
no transcript API. The library used reads what the player uses, so there is no
published rate limit and no contract. Ingesting several long videos in quick
succession resulted in the source blocking the requesting IP address. This is
the least reliable dependency in the system and it is outside the project's
control.

**Generation is not reproducible.** Two identical requests at temperature 0
produced different answers, and the provider has signalled that the temperature
parameter is being deprecated. Any evaluation must therefore run each question
several times and report the spread, or an apparent improvement cannot be
distinguished from sampling noise.

**English only.** The embedding model and the Postgres full-text configuration
are both English. Videos whose captions are auto-translated rather than
auto-transcribed are excluded, because the retrievable text would not be the
words spoken.

**Cost figures are list prices.** The per-request cost recorded in the logs is
computed from published rates, not billed. On a free tier the real constraint is
rate limits rather than money, so the column answers what the system would cost
at scale.

**The benchmark is concentrated.** The reported videos are weighted toward
introductory machine-learning explainers and university lectures, because the
labels have to be written by someone who understands the content well enough to
judge where an answer is.

## Stack

Python, FastAPI, PostgreSQL (Neon), SQLAlchemy, Alembic, LiteLLM over Gemini,
youtube-transcript-api, Docker, Render.

## Licence

MIT. See `LICENSE`.

---

## Running it locally

**Prerequisites:** Python 3.12, a PostgreSQL database (Neon's free tier works),
and a Gemini API key from Google AI Studio.

```bash
git clone https://github.com/Arnav-77/youtube-deepdive.git
cd youtube-deepdive
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in:

DATABASE_URL=postgresql+psycopg://user:password@host/dbname?sslmode=require
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini/gemini-3.6-flash


The `postgresql+psycopg://` prefix matters. Plain `postgresql://` makes
SQLAlchemy reach for psycopg2, which is not installed, and the resulting error
names a package you never asked for.

Create the schema, then start the server:

```bash
alembic upgrade head
python run.py
```

It serves on port 8000 by default, or on `$PORT` if set. Interactive API docs
are at `/docs`.

### With Docker

```bash
docker build -t deepdive .
docker run --env-file .env -p 8000:8000 deepdive
```

To reproduce the deployment's resource limits, which is the only way to find
out what the code does at 0.1 CPU and a hard 512 MB ceiling:

```bash
docker run --env-file .env -p 8000:8000 --cpus=0.1 --memory=512m deepdive
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness. Touches no dependencies, returns the commit SHA. |
| GET | `/ready` | Readiness. Checks the database. |
| POST | `/videos` | Ingest a video: fetch transcript, chunk, store. |
| POST | `/ask` | Ask a question about an ingested video. |

`/health` deliberately checks nothing external. A health check that fails on a
transient database blip makes the platform restart the application, which fixes
nothing and drops in-flight requests.

## Layout

app/
main.py FastAPI app, endpoints, citation resolution
models.py SQLAlchemy models: videos, chunks, query_logs
db.py engine and session
youtube.py URL parsing, transcript fetch, ingestion
chunking.py fixed-size chunking with timestamp metadata
retrieval.py Postgres full-text search
generation.py prompt rendering, model call, citation validation
prompts/ versioned prompt templates
alembic/ database migrations
evaluation/
golden_set.yaml hand-written relevance labels
run_eval.py retrieval harness: Recall@k, MRR, citation offset


## Running the evaluation

```bash
python evaluation/run_eval.py --set dev
```

Retrieval only: no model is called, so it costs nothing and is safe to run as
often as you like. It defaults to the development set. Reported numbers require
`--set reported`, which is deliberately a separate flag, so that tuning against
the reported videos takes a conscious act rather than a default.

```bash
python evaluation/run_eval.py --run fixed_1000_200 --k 5 --set dev
```

`--run` selects which chunking strategy to evaluate, which is what makes
comparing two strategies a matter of changing one argument.

## Notes for contributors

**Never UPDATE or DELETE a chunk row.** Every chunk ID recorded in
`query_logs.retrieved_chunks` must keep resolving to the same text forever.
Re-chunking writes new rows under a new run label.

**Never move a video between the development and reported sets** in
`evaluation/golden_set.yaml` once it has been evaluated. The split exists to
keep reported numbers honest, and moving one after seeing results defeats it.

**Prompts are versioned files, not string constants.** A change belongs in a
new file under `app/prompts/`, so that a change in output quality can be
attributed to a specific edit.

A fuller record of what broke and how each problem was diagnosed is in
[docs/problems.md](docs/problems.md).