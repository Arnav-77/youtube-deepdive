# Design decisions

## 10 Nov 2026: the web page is one static file served by the API, at /ui/

**Decision.** The web page is one HTML file (`app/static/index.html`, inline CSS and JS, no build step) served by FastAPI at `/ui/` from the same origin as the API. It uses two endpoints: `GET /videos` to list the videos `/ask` can answer, and `POST /ask`. Each video shows a YouTube thumbnail, loaded by the visitor's browser, next to its id. `GET /` stays JSON for Render's probe and now includes `"ui": "/ui/"`. The branch's commits do not use `[skip render]`, because Render deploys the newest pushed commit and a skip would leave the page undeployed.

**Why.** The page is a simple form and a list. A second service adds a deploy to keep in step, a hosted account, CORS rules, and a framework. The viva rule ("explain every line without notes") and the time left before 23 Nov both count against that. The static file adds about 0 MB to the server, needs no new package, and is one deploy. Thumbnails come from the visitor's browser, so the server makes no request to YouTube and no server change is needed.

**Alternatives considered.**
- Next.js on Vercel. Rejected for now: the first page load would be instant, but that is the only gain. It adds a hosted service (CLAUDE.md asks first), a second deploy, CORS, and React, which the author has not yet learned. Revisit only if the cold start after Render's 15-minute sleep is a real problem in production, measured there.
- Fetching titles at ingestion (YouTube oEmbed) or backfilling the 23 existing rows. Rejected for now: oEmbed adds an outbound request from Render, which YouTube already blocks for transcripts, and the backfill writes to the production `videos` table. The list shows the video id until titles are decided separately.
- A `<select>` dropdown. Rejected: it cannot show thumbnails.
- Serving the page from `/` instead of `/ui/`. Rejected: `/` must answer Render's HEAD probe with the JSON it already returns.
- Building the page with `innerHTML`. Rejected: model output is untrusted text. The page builds elements with `createElement` and `textContent`, and a test fails if the code sets `innerHTML`.

**Consequences.** The page's JavaScript is not run by the tests, which check the file's text only; it is checked by hand in a browser. The first visit after Render's sleep waits for the service to wake. The `videos` listing filter is checked by hand on the dev branch, not in tests.

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
