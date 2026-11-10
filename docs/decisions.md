# Design decisions

## 10 Nov 2026: a missing PASTE_TOKEN disables paste without stopping the app (refines rule 7)

**Decision.** Rule 7 says invalid configuration stops the app at startup. An unset PASTE_TOKEN is not invalid configuration; it is a missing optional feature. So the app starts, prints one line saying paste is disabled, and POST /videos/paste answers 503 "paste is disabled on this server" before it reads the body. The app still fails closed: with no token configured, no paste is accepted. A token that is set but wrong gets 401. The token value is never printed.

**Why.** /ask must not stop because a feature it does not use is unconfigured. Stopping would take the whole live app down for a missing feature flag. Failing closed keeps the public endpoint from spending quota when nobody set a gate.

**Alternatives considered.**
- Stop at startup when PASTE_TOKEN is unset, like RETRIEVAL_METHOD. Rejected: it ties the availability of /ask to an optional feature.
- Leave the route open when the token is unset. Rejected: a public quota drain and a way to attach wrong transcripts to real video ids.

**Also decided.** The token is an environment variable only. It is never in the repo, in page code, in a query string, or in a log line. The web page asks the viewer to type it, sends it as the X-Paste-Token header, and keeps it in memory only.

## 10 Nov 2026: pasted transcripts are embedded in a background thread, resumed by sending the same request again

**Decision.** POST /videos/paste parses the paste, stores the video and its chunks, starts a daemon thread that embeds the missing chunks, and returns 202 at once. The thread opens its own database session. "Already running" is tracked in memory, so a second request does not start a duplicate run. Progress is read from the database by GET /videos/{id}. After a restart nothing runs by itself: sending the same paste (or the same URL) again embeds only the missing chunks. This assumes one worker process.

**Why.** Embedding 300 chunks takes about three minutes; Render cuts requests at about 100 s. Progress lives in the database (embed_missing_chunks commits per batch), so a crash loses at most one batch of work and the restart story is "send it again", which needs no state beyond the database.

**Alternatives considered.**
- FastAPI BackgroundTasks. Rejected: they run with the request's session, which is closed once the response is sent. A thread with its own session is simpler to reason about.
- A separate worker process or a queue. Rejected: a second process and a queue are new services and new memory on a 512 MB instance (rule 10).
- Resume automatically at boot. Rejected for now: it spends free-tier quota with no request behind it. Revisit if restarts become frequent.

**Also decided.** While a video is partly embedded, questions about it are answered from word search and logged as lexical_fallback:MissingEmbeddings. No question embedding is attempted, because dense_search checks for missing chunks first. This is tested.

## 10 Nov 2026: the captions route POST /videos stays synchronous

**Decision.** POST /videos (captions path) is unchanged and still embeds inside the request.

**Why.** YouTube blocks transcript requests from Render, so this route only runs from the laptop, which has no 100 second limit.

**Revisit when.** The captions path ever runs on the server. At that point, move it to the same background job as the paste route, in its own commit.

## 10 Nov 2026: paste parsing limits, the 4.0 s final duration, and whole-second timestamps

**Decision.** The parser (app/paste.py) refuses a transcript over 300 KB of UTF-8 bytes, over 5,000 segments, over 12 hours, with timestamps that go backwards, or with control characters (NUL included). The final segment gets a fixed 4.0 s duration, because the panel gives no end time. Timestamps are whole seconds, so citations land on the whole second. Pasted videos are never added to evaluation/golden_set.yaml, because their timings are coarser than the captions API's.

**Why.** The paste is untrusted public input, so every limit is checked in code. A NUL would make Postgres reject the row, which would be a 500 from our code. Chunking reads only text and start, and the final duration is used only for the last snippet, so the 4.0 s estimate changes no chunk boundary.

**Alternatives considered.**
- Use the median gap between snippets for the last duration. Deferred: no evidence yet that it matters, and the estimate is only used for duration_seconds and the last chunk's end time.
- Store only the parsed snippets, not the raw paste. Rejected: the raw text is what Arnav copied, and it is kept word for word in videos.raw_paste so a parser change can be re-run on the original.

**Also decided.** Injection: the paste reaches the model only as excerpts, through the existing path. The model has no tools, render_prompt is single pass, and citations are checked in code. Accepted residue: a "[3]" typed into the transcript can be echoed by the model. The link still resolves to the number the code assigned, so no wrong link results. The future web page must render transcript text escaped, never as HTML.

## 4 Nov 2026: a citation at the start of an answer belongs to the claim after it, and non-golden answers are examples

**Decision.** A citation run at the very start of an answer ("[1] The climber said X.") is held and attached to the next claim, so the claim is one segment cited 1. The segment is flagged `leads_with_citation`, and the scorer reports how many scored segments used the rule. A saved answer whose question is not in the golden set is listed as an example and left out of every rate, refusal result and segment count.

**Why.** Before this, the bare "[1]" became its own segment, and the claim after it counted as uncited. That penalised the model for where it put a bracket. Counting the example would also mix a question nobody chose to measure into the numbers.

**Alternatives considered.**
- Keep the bare "[1]" as its own segment. Rejected: it counts a claim as uncited because of layout.
- Treat a leading citation as a formatting error reported on its own. Rejected for now: it would hide a real case of citing before the claim, and the report count gives the same visibility.
- Flag examples with a label field in the labels file. Rejected: the golden set already decides what is judged, so a second switch could disagree with it.

**Also decided.** The count is shown per answer set, so a rule that fires often is visible. row26.json is kept as the worked "This is because" example and is not a scored answer.

## 4 Nov 2026: the scorer groups models by name without the provider prefix

**Decision.** `score_grounding.model_name` removes the text before the first `/` from a saved answer's model name. "gemini/gemini-3.6-flash" and "gemini-3.6-flash" are one model, so their rows and their per-model section are one.

**Why.** The request is logged with the provider prefix, but LiteLLM's response reports the bare name. Grouping on the raw string would split one model into two sections and halve each sample size without saying so.

**Alternatives considered.**
- Group on `model_requested` instead. Rejected: a fallback would then be counted under the model that failed, and the fallback must stay visible (design rule 8).
- Strip everything before the last `/`. Rejected: "openrouter/google/gemma" would become "gemma", merging it with any other gemma.

**Also decided.** Only the scorer's grouping changes. Stored answers and query_logs keep the raw names.

## 4 Nov 2026: the scorer stops on untrustworthy labels, reports unlabelled and unsent citations, and the parsers are pure

**Decision.** The scorer stops, printing every problem and no numbers, when a label block cannot be trusted: a missing answer file, a stale hash, a label outside the three values, or a refused value that is not true or false. Blocks that are not yet labelled are listed and left out of every number. A citation to an excerpt the model was never sent is counted and reported, not treated as a scorer error, because it is a real model failure that the hand label already judges. The citation parser and the segmenter live in app/citations.py, and the relevance rule in evaluation/relevance.py. Both are pure, so the helpers and the scorer run with no DATABASE_URL.

**Why.** The integrity checks guard against scoring a label against text it was not written for, which is the one error that would silently corrupt a number. An unlabelled block is work in progress, not an error, so stopping on it would make partial reporting impossible. A bad citation is data about the model. The pure modules exist because importing app.generation or run_eval loads app.db, which raises when DATABASE_URL is unset, and the helpers must run in a worktree with no .env.

**Alternatives considered.**
- Stop on any unlabelled block. Rejected: the report could never be run mid-labelling.
- Stop on a citation to an unsent excerpt, as the plan first said. Changed: it would block scoring on a model behaviour the label already captures.
- Keep the parser in app/generation.py and import it. Rejected: the import chain reaches the database layer.

**Also decided.** The skeleton has a blank note per segment, so a "support found in [n]" note can be counted as a wrong-link citation without a new label category.

## 4 Nov 2026: grounding is measured per citation segment, refusal is a hand label scored by gold spans

**Decision.** An answer is split into segments at citation runs, using the existing `cited_numbers` parser. Each segment is labelled supported, partly supported or unsupported, judged only against the excerpts its own run cites. The grounding rate is reported next to an answer-level rate (every segment supported). Refusal is a hand label per answer. Its correctness is decided by the gold spans: a refusal is correct only when the gold spans are empty, and a refusal on a non-empty span is a false refusal with a retrieval or generation cause. Refusal accuracy is never combined with the grounding rate. Rules are in docs/grounding.md.

**Why.** Sentences can join two facts under one citation ("This is because"), and a segment is the smallest text the citation can be checked against. A hand refusal label avoids inferring refusal from missing citations, which is the known gap. Gold spans, not retrieval, decide correctness, so a retrieval miss cannot count as a correct refusal.

**Alternatives considered.**
- Sentence-level, two labels. Rejected: one unsupported clause fails the whole sentence, so the sklearn partial answer and the Everest link cannot be told apart.
- Refusal inferred from "no citations" or from an empty retrieval. Rejected: that is the known gap, and an empty retrieval is a retrieval failure, not a decision the model made.
- Refusal judged only by whether a retrieved chunk overlaps a gold span. Rejected: "how to be consistent" retrieves nothing and would count as a correct refusal, though the answer is in the video.
- Grounding and refusal as one combined score. Rejected: a refusal scores well on grounding trivially, which hides the failure.

**Also decided.** Labels live in evaluation/grounding_labels.yaml and hold no transcript text. Saved answers, which do hold excerpt text, are gitignored. Labels are never filled by a script; the helper only prints and writes a skeleton.

## 3 Nov 2026: tests isolate configuration in conftest.py, not by changing app code

**Decision.** The test suite runs with no network, no database and no .env. tests/conftest.py, before any app module is imported, disables python-dotenv (PYTHON_DOTENV_DISABLED), overwrites DATABASE_URL with an unreachable local address, tells LiteLLM to use its bundled price list (LITELLM_LOCAL_MODEL_COST_MAP), removes GEMINI_API_KEY, and replaces Python's socket connect so any HTTP call (Gemini, GitHub, YouTube) raises. The socket guard does not stop the database driver: psycopg connects through libpq in C, bypassing Python's socket module (verified 3 Nov). The overwritten DATABASE_URL is the database protection.

**Why it was needed.** Every module under test imports app/db.py, directly or through app.models or app.embeddings. db.py reads .env and raises at import if DATABASE_URL is unset. Importing litellm downloads a price list from GitHub.

**Alternatives considered.**
- Make db.py create the engine lazily, inside a function. Rejected: changes production code in a test branch, and design rule 7 wants missing configuration to stop the app at startup, which db.py does correctly now.
- Move the pure functions into modules that never import the database. Rejected for now for the same reason; worth revisiting if test import time becomes a problem.

**Also decided.** pytest goes in requirements-dev.txt, not requirements.txt. The Dockerfile installs only requirements.txt, so test tools never reach Render. tests/, pytest.ini and requirements-dev.txt are in .dockerignore so test-only commits do not change the image.
