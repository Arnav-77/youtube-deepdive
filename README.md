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

1. **Fetch** the transcript for a YouTube URL (youtube-transcript-api).
2. **Chunk** it into pieces of about 1,000 characters, each with its start and end time.
3. **Embed** each chunk (gemini-embedding-001, 768 dimensions) and store the vectors in Postgres with pgvector.
4. **Retrieve** the 5 chunks closest in meaning to the question (cosine similarity). Word search is the logged fallback.
5. **Generate** an answer from those chunks only (Gemini via LiteLLM, with a backup model).
6. **Cite**: the model writes `[1]`, `[2]`, and the application turns each into a timestamp link.
7. **Log** every request to Postgres, including the ones that fail.


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

**One datastore, deliberately.** Postgres on Neon holds transcripts, chunks,
embeddings (through pgvector) and logs, so search runs as SQL against the same
rows the logs point to. The original plan used ChromaDB for vectors, but
Render's free tier has no persistent disk, so a local vector index would be
wiped on every deploy. Neon rather than Render's own Postgres because Render's
free database deletes itself 30 days after creation, which would have
destroyed the logging table twice before submission.

**The retrieval method is a setting, and every request records which one
ran.** `RETRIEVAL_METHOD` selects dense or word search and is checked at
startup, so a typo stops the deploy instead of failing the first real
question. If dense search cannot run for a request (a chunk with no embedding,
or the embedding service failing), the request falls back to word search and
its log row says `lexical_fallback:<reason>`, so a fallback is never invisible.

## Measured results

Numbers from the deployed system, not estimates.

| Measurement | Value |
|---|---|
| Cold start (idle service to first response) | 41.5 s |
| Warm response, `/health` | 0.32 s |
| End-to-end `/ask`, same request, observed range | 3.6 s to 27.1 s |
| Application's own share of that latency | under 1 s |
| Benchmark corpus | 23 videos, 1,574 chunks, all embedded |

The latency range is the interesting result. The same question, with identical
retrieval and identical code, varied roughly sevenfold across runs. A
first-call-after-startup effect was hypothesised and ruled out by experiment:
after a cold start, two identical back-to-back requests took 11.7 s and then
22.2 s, and a startup cost cannot make the second request slower. Generation is
therefore over 90% of every request, and the system's P95 latency is
effectively the model provider's P95. No amount of optimising this codebase
would move it.

### Retrieval quality

19 hand-labelled questions on 5 videos: 14 on 4 reported videos and 5 on one
development video. `fixed_1000_200` chunks, top 5 retrieved.

| Reported set (14 questions) | Word search | Dense | Hybrid (RRF) |
|---|---|---|---|
| Recall@5 | 0.86 (12/14) | 1.00 (14/14) | 1.00 (14/14) |
| Right chunk ranked first | 7/14 | 12/14 | 10/14 |
| MRR | 0.63 | 0.92 | 0.82 |
| Median distance of citation from the answer | 40 s | 15 s | 22 s |
| Citations landing late (of those found) | 1/12 | 5/14 | 3/14 |

Compared question by question across all 19, dense ranked the right chunk
higher than word search on 9, equal on 10 and lower on none. With so few
questions, from only five videos, that is a clear direction rather than a final
result. Dense has been the live method since October 2026. Hybrid scored below
dense because on none of these questions did word search rank the right chunk
above dense, so mixing it in could only pull rankings down; the questions
contain no case where exact matching should win (rare names, new terms), so
hybrid stays in the code as a tested alternative.

Retrieval is not the whole story. The first real user's question, "how to be
consistent", found nothing under word search because the speaker never says the
word. Under dense search the right passage is retrieved, and the model still
replies that the excerpts do not contain the answer. A retrieval failure became
a generation failure, and the log shows which. Measuring whether answers are
supported by the passages they cite is the next stage.

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
- Dense retrieval (Gemini embeddings in pgvector), live since October 2026, with word search as a logged fallback
- Embeddings created automatically when a video is added
- Grounded generation with code-validated citations and a backup model
- Complete per-request logging, including which retrieval method answered
- Retrieval evaluation harness comparing word, dense and hybrid search
- Benchmark: 23 of 25 videos chosen and ingested, 19 questions labelled

In progress:

- Hand-labelling the rest of the golden set
- Measuring whether answers are supported by the passages they cite

Planned:

- Runtime grounding critic, using the same definition as the offline measurement
- Topic-shift chunking, measured against the fixed-size baseline
- Follow-up questions (multi-turn conversation)
- A simple chat page in place of `/docs`
- Cross-video synthesis across 2-3 videos on one topic
- Tracing, P95 latency and cost reporting

Not planned before submission:

- Cross-encoder reranking (no free place to run the model)
- Whisper transcription for videos without captions

## Limitations

**The live server cannot add new videos.** YouTube refuses transcript requests
from cloud servers (`RequestBlocked`, observed October 2026). Videos are added
from a home connection that writes to the same database, and the live site
answers questions about those. Asking about a video that has not been added
returns 404.

**A refusal is logged as an answer.** A reply saying the excerpts do not
contain the answer is recorded with status `answered`. Telling the two apart
belongs to the grounding critic.

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

**English only.** Transcripts are fetched in English, the transcript library's
default, and the word-search fallback uses Postgres's English configuration.
The embedding model supports other languages, but nothing here has been tested
on them. Videos whose captions are auto-translated rather than auto-transcribed
are excluded, because the retrievable text would not be the words spoken.

**Cost figures are list prices.** The per-request cost recorded in the logs is
computed from published rates, not billed. On a free tier the real constraint is
rate limits rather than money, so the column answers what the system would cost
at scale.

**The benchmark is concentrated.** The reported videos are weighted toward
introductory machine-learning explainers and university lectures, because the
labels have to be written by someone who understands the content well enough to
judge where an answer is.

## Stack

Python, FastAPI, PostgreSQL (Neon) with pgvector, SQLAlchemy, Alembic, LiteLLM
over Gemini, Gemini embeddings (gemini-embedding-001) over plain HTTPS,
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

- `DATABASE_URL=postgresql+psycopg://user:password@host/dbname?sslmode=require`
- `GEMINI_API_KEY=your-key` (used for both answers and embeddings)
- `GEMINI_MODEL=gemini/gemini-3.6-flash` (optional; this is the default)
- `FALLBACK_MODELS=gemini/gemini-3.5-flash-lite` (optional, comma separated)
- `RETRIEVAL_METHOD=dense` (optional; `dense` or `lexical`, checked at startup)


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

Videos added through `POST /videos` are embedded automatically. Chunks that
already existed can be embedded with `python evaluation/embed_chunks.py --videos all`,
which is resumable and paced under the free tier's limits.

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
| POST | `/videos` | Add a video: fetch transcript, chunk, embed, store. Repeating it finishes an interrupted embedding. |
| POST | `/ask` | Ask a question about an added video. Returns 404 if the video has not been added. |

`/health` deliberately checks nothing external. A health check that fails on a
transient database blip makes the platform restart the application, which fixes
nothing and drops in-flight requests.

## Layout

| Path | What it is |
|---|---|
| `app/main.py` | FastAPI app, endpoints, citation resolution, request logging |
| `app/models.py` | SQLAlchemy models: videos, chunks, query_logs, chunk and question embeddings |
| `app/db.py` | Engine and session |
| `app/youtube.py` | URL parsing, transcript fetch, ingestion |
| `app/chunking.py` | Fixed-size chunking with timestamp metadata |
| `app/embeddings.py` | Gemini embedding client: batching, rate-limit pacing, question cache |
| `app/retrieval.py` | Word search, dense search and hybrid (RRF) search |
| `app/generation.py` | Prompt rendering, model call with a backup, citation validation |
| `app/prompts/` | Versioned prompt templates |
| `alembic/` | Database migrations |
| `evaluation/golden_set.yaml` | Hand-written relevance labels |
| `evaluation/run_eval.py` | Retrieval harness: Recall@k, MRR, citation offset |
| `evaluation/explain_query.py` | How every chunk ranked for one question, and why |
| `evaluation/embed_chunks.py` | Resumable embedding backfill for existing chunks |


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
comparing two strategies a matter of changing one argument. `--method lexical`,
`dense` or `hybrid` selects the retrieval method (default `lexical`, the
original baseline). Question embeddings are cached, so repeat runs make no
embedding calls.

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