# Finish run log (17 to 18 Nov 2026)

Unattended run carried out by Claude Code (Opus 5.5) from `report_src/finish_plan.md`.
One entry per phase: what was done, commit ids, numbers produced, and anything skipped with the reason.

## Phase 1: setup

- `git status --short` on `main` showed only the untracked `report_src/` folder, as required.
- Branch `finish` created from `paste-transcript` (5cad71f). The `~/code/deepdive-paste` worktree was not touched.
- `.gitignore` now ignores `report_src/` and `report_out/`; `git check-ignore -v` confirmed both (commit 8e42b65).
- Baseline test suite at 5cad71f: 401 collected, 398 passed, 3 skipped, 0 failed.
  The 3 skips are `tests/test_paste.py` tests that need the git-ignored local transcript samples, which are not in this worktree.

## Phase 2: evaluation

### 2a. Retrieval on the human-labelled set (done)

- `run_eval.py` gained `--golden PATH` and `--cache-only`, a pure `summarise()` with per-type output, and a dev-branch guard (`evaluation/dev_guard.py`). Commit d0bddf0, prediction written in its message.
- Output: `evaluation/results/retrieval_human.txt` (all three methods, reported and dev sets). 0 embedding API requests.
- Reported set (14 questions, 4 videos): dense Recall@5 14/14, MRR 0.917, Recall@1 12/14, |offset| median 15 s, late 5/14. Lexical 12/14, MRR 0.631. Hybrid 14/14, MRR 0.815, late 3/14.
- Dev set (5 questions, 1 video): dense 5/5, MRR 0.750; lexical 4/5, MRR 0.640; hybrid 5/5, MRR 0.717.
- Every predicted number matched Arnav's 14 to 18 Oct notes exactly. No differences to record.

### 2b. AI-written questions (done)

- Seven subagents read the transcripts with `show_transcript.py` and wrote 3 questions per video (speaker_words, paraphrase, negative) for all 18 reported videos and the unlabelled parts of LXaFHI_2Hus (dev): 19 videos, 57 questions, merged into `evaluation/golden_set_agent.yaml` with `author: claude-code`.
- `evaluation/agent_set.py` validates the file: 0 problems.
- A word-run scan found no question or note sharing more than 6 consecutive words with its transcript; the four 6-word runs (common phrases) were reworded in the notes.
- Two independent verifier subagents checked the set: 38 of 38 positive spans hold their answer (one span widened by 5 s); 18 of 19 negatives are clean; the 19th (4HKqjENq9OU, a yes/no question answerable "no") was replaced. Five wording flags are listed in REVIEW.md.
- Commit d620884 (set, prediction); results a8e4360: `evaluation/results/retrieval_agent.txt`. Every prediction held. Reported set: speaker_words dense 18/18 (MRR 1.000), lexical 18/18 (0.963); paraphrase dense 18/18 (0.972), lexical 7/18 (0.292), hybrid 15/18 (0.604); every method returned 5 chunks for all negatives. 57 embedding API requests.

### 2c. Answers: budget change

- Gemini's primary model (gemini-3.6-flash) returned 503 "high demand" on every request in run 1; the backup (gemini-3.5-flash-lite) answered each one. The log shows exactly one failed primary attempt per request, so each request costs 2 calls, and 120 calls is about 60 requests, not the 95 planned.
- Decision: one run of the 19 human questions, then as many AI-written questions as the budget allows, negatives first in each round. The planned second human run (spread check) is skipped for budget. For the spread, Claude also labels the earlier saved answers to the same 19 questions (rows 24 and 33 to 50, from 18 Oct and 10 Nov) at no API cost.

- Human run 1 (label human-r1): 19 of 19 returned 200, all answered by the backup model, dense retrieval, rows 56 to 74, 38 calls.
- AI-set run (label agent-r1): negatives first in each round. At 09:29 UTC one gemini-3.6-flash call hung for about 5 minutes. LiteLLM's default timeout is 6000 s and `generate()` sets none. The process was stopped (charged 2 calls, no log row written), the runner gained `--llm-timeout` (default 120 s, sets LiteLLM's `REQUEST_TIMEOUT` for its own process only; commit 4610223), and the run resumed. The bug is recorded as a strict xfail test (c225305), not fixed: app/ is frozen.

### 2d. AI judge (done)

- Subagents label each saved answer blind (no access to either labels file or to the gold spans) with `label_answer.py --show` and the rubric in docs/grounding.md. `evaluation/agent_labels.py` checks every segment index and computes each hash from the saved answer.
- Labelled: 79 answers, 132 segments: rows 24, 26, 33 to 50 (earlier run), 56 to 74 (human-r1), 75 to 114 (agent-r1). File `evaluation/grounding_labels_agent.yaml` (commit 10dd29b). The judge flagged row 86 as a borderline refusal.
- Batches 5 onward were given one extra sentence on refusals ("an answer that says the excerpts do not cover the question and only adds loosely related material is still refused: true"), after the first AI-set answers showed that case. Batches 1 to 4 (human questions) had no such answer.

### 2c. Answers (done)

- Budget used: 120 of 120 answer-generation calls (60 requests: 19 human, 40 AI-set, 1 hung and stopped), 57 of 200 embedding texts. The main model returned 503 or hung on every request; the backup answered all 59 logged requests.
- 17 of the 57 AI-written questions were not asked (budget): all 19 negatives were asked, plus 21 positive questions.
- The planned repeat of the human questions was skipped for budget. The spread is measured instead against the earlier saved answers (rows 24, 33 to 50), labelled by the same AI judge.

### 2e to 2i (done)

- 2e: `evaluation/spot_check.py` wrote 20 segments (seed 20261118) to the gitignored `evaluation/answers/spot_check.md` without AI labels, and a blank `evaluation/spot_check_labels.yaml`; `evaluation/spot_check_agreement.py` prints agreement and a 3x3 table (commit 59ea4a7).
- 2f: citation rate (`evaluation/results/citation_rate.txt`): this run 39/59 (66.1%), 38/38 (100%) excluding the 21 labelled refusals, 19 of which are the deliberately unanswerable negatives; earlier dev rows 40/43 (93.0%).
- 2g: `export_logs.py`, `live_latency.py` (not run: production is Arnav's), CSV-based `log_stats.py` tested on a dev export. This run, laptop: 59 requests, 0 failed, P50 6.9 s, P95 36.7 s, max 123.3 s (the 120 s timeout fired once), backup 59/59. The dev copy also holds 20 real production rows (21 Sep to 27 Oct): 13 answered, P50 4.5 s, P95 23.9 s (n=13).
- 2h: `evaluation/run_all.py` (no API calls; stops on a missing cached embedding): `evaluation/results/summary.txt` and `.json`, commit 91c77b8. Prediction held.
- 2i: `evaluation/REPORT_DATA.md`, generated by `evaluation/report_data.py`.
- Tests after Phase 2: 478 collected, 474 passed, 3 skipped, 1 xfailed (the known timeout bug).
