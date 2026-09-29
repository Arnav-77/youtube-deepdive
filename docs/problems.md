# Problems

A record of things that broke or turned out to be wrong, with how each one was
diagnosed. Written as they happened, not reconstructed afterwards.

The pattern worth noticing across these: almost every entry was found by
measuring something, and several of them contradicted a confident assumption.
The ones that took longest to find are the ones where the wrong behaviour
looked completely normal.

---

## Caption timestamps were silently four seconds late

**Symptom.** None. Everything looked correct.

**What was actually happening.** YouTube's caption snippets overlap in time.
Snippet 1 reported `start=0.00, duration=4.84`; snippet 3 started at 4.84,
while snippet 2 started at 2.24, before snippet 1 had supposedly ended.

`duration` measures how long a caption stays *visible* on screen, given
YouTube's rolling two-line display. It does not measure how long the text is
*spoken*. So `start + duration` is not when the speech ends, and computing a
chunk's end time from it drifts progressively through the video.

**Why it was dangerous.** The resulting timestamps were internally consistent,
correctly typed and monotonically increasing. Every automated check would have
passed. The only symptom was that clicking a citation landed roughly four
seconds after the thing it cited, which reads as "close enough" rather than as
a bug.

**Fix.** A snippet's true end is the `start` of the next snippet;
`start + duration` is used only for the final one.

**How it was confirmed.** By opening the video and listening. Chunk 2 claimed
to begin at 51.72 s with a particular sentence, and at t=51 that exact caption
was on screen. This was the first check in the project that tested the code
against reality rather than against its own data structures.

---

## Local embedding models do not fit, and arithmetic proved it

**The assumption.** Sentence-transformers would run inside the deployed
process, as the proposal described.

**The measurement.** Peak resident memory, measured the same way for each:

| What | Peak RSS |
|---|---|
| Bare Python interpreter | 8 MiB |
| The application without LiteLLM | 74 MiB |
| Importing LiteLLM alone | 202 MiB |

The application plus LiteLLM sits near 250 MiB against the platform's 512 MB
ceiling. PyTorch alone exceeds the roughly 260 MB that remains, before any
embedding model or cross-encoder is loaded.

**Consequence.** Embeddings have to come from a hosted API. This was not a
disaster, because the embedding call had already been isolated behind a single
`embed()` function on the suspicion that this might happen. The seam stopped
being insurance and became the design.

**The wider lesson.** "It probably won't fit" and "here are three numbers
showing it cannot" are different claims. Only the second one settles an
argument.

---

## The same request took 3.6 seconds, then 27

**Symptom.** An identical question, with byte-identical retrieval, took
wildly different times across runs: 3.6 s, then 22.1 s, then 27.1 s, then
11.7 s. Roughly a sevenfold spread with no code change.

**The hypothesis.** A one-time cost on the first request after process start:
LiteLLM loading provider code, or opening its first HTTPS connection. If true,
every cold start would pay it.

**The test.** After a cold start, send the identical request twice, back to
back. A startup cost can only make the *first* request slower.

**The result.** 11.7 s, then 22.2 s. The warm request was slower, so the
hypothesis is false.

**What it actually is.** The model provider's own variance. The application's
share of the total is under a second in production, measured as the difference
between what curl reports and what the request handler times. Generation is
over 90% of every request.

**Consequence for the evaluation.** A single latency measurement means
nothing. P95 needs many samples, and the system's P95 is effectively the
provider's P95. No optimisation of this codebase would move it.

---

## Temperature 0 does not mean reproducible

**Symptom.** Two identical requests, same question, byte-identical retrieval
scores, `temperature=0.0`, produced different answers: one a bulleted list of
675 tokens, the other prose of 582.

**Cause.** Two things compound. The provider has signalled that the
temperature parameter is being deprecated. And no provider ever guaranteed
bitwise determinism at temperature 0 anyway, because batching and request
routing vary between calls.

**Consequence.** Any evaluation has to run each question several times and
report the spread. Otherwise the difference between two runs contains both the
effect of a change and sampling noise, with no way to separate them, and any
claimed improvement is unfalsifiable.

**How it was found.** By accident, reading two adjacent rows in the request
log. Which is what the request log is for.

---

## Local and production requests were indistinguishable in the database

**Symptom.** Four rows in the log table, all of them the same question. Two
were sent from a laptop and two from the deployed service, and nothing in the
table said which was which.

**Why it matters.** A development laptop and the deployed service write to the
same database. Every local test lands in the table the evaluation report is
built from. Local requests carry hundreds of milliseconds per database round
trip that production never pays, so mixing them corrupts any latency figure
drawn from the table.

**Fix.** A `commit_sha` column, filled from the deployment's own commit
variable and reading `local` on a laptop. One column now answers both "where
did this run" and "which version of the code produced it".

**What could not be fixed.** Rows written before the column existed are
permanently ambiguous. They are identifiable only as the ones where
`commit_sha` is null. A logging table cannot be backfilled with facts nobody
recorded.

---

## A crash would have written a row that said nothing

**Symptom.** None yet. Found by reading the code rather than by failing.

**The hole.** Every request path writes a log row in a `finally` block, and
`error` is populated on each anticipated failure. But an *unanticipated*
exception, a database error inside retrieval or a plain bug, would write a row
with both `answer` and `error` null. In the report, such a row reads as a
request that neither succeeded nor failed.

**A second hole in the same area.** When a database query fails, the session
refuses all further work until it is rolled back. The log writer used that same
session, so the row about the database failure would itself fail to be
written, losing exactly the evidence worth having.

**Fix.** An `except Exception` clause that records the exception's class name
and re-raises, ordered after the clause that handles deliberate HTTP errors so
it does not swallow them; and a rollback before the log write, which is safe
only because this endpoint writes nothing else.

---

## The first real user hit a retrieval failure

**What happened.** The first person other than the author to use the deployed
system asked "how to be consistent" of a talk about habits. The system
retrieved nothing and correctly said so.

**Diagnosis, measured rather than guessed.** Postgres full-text search drops
`how`, `to` and `be` as stopwords, leaving the single stem `consist`. A raw
substring search for `consist` across every chunk of that video matched
nothing: the speaker never says the word in seventeen minutes. The control word
`habit` matched 19 of 20 chunks, which proves the query mechanism works.

**But the answer is in the video**, expressed in other words. Confirmed by
watching: the speaker covers writing things down daily and managing energy so
you keep going.

**So this is a vocabulary mismatch**, the failure that dense retrieval exists
to fix, found by a real user within minutes of the system going public. It is
now the first labelled case in the benchmark and the clearest argument in the
project for semantic search.

**A side finding.** A word appearing in 19 of 20 chunks cannot discriminate
between them. Real BM25 handles this with inverse document frequency, weighting
rare terms more heavily. Postgres `ts_rank` consults no corpus statistics and
has no IDF at all, which is why calling it BM25 would be a factual error.

---

## The transcript source blocked the requesting IP

**Symptom.** Eleven videos ingested successfully; the next seven all failed
with `IpBlocked`.

**Cause.** YouTube publishes no transcript API. The library in use reads what
the player uses, so there is no published rate limit and no contract. Eleven
sequential fetches about a second apart, several of them over eighty minutes
long, were enough to trigger blocking.

**What the system did right.** The endpoint returned 502 rather than 500,
correctly attributing the fault upstream rather than claiming its own code had
broken. That distinction keeps the failure-rate column meaningful.

**What it lacked.** No retry, no backoff, no jitter, and no way to tell
"throttled" apart from "this video has no captions" in the logs, since both
produce a 502.

**Blocking is per IP**, so the laptop and the deployed service are limited
separately, and a local block does not mean the live service is down.

**Open.** Exponential backoff with jitter, a distinct error string for the
throttled case, and eventually the background job this deferred: a retrying
fetch cannot sit inside a request the platform cuts off after 100 seconds.