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

### 2b. AI-written questions (in progress)

- Seven subagents read the transcripts with `show_transcript.py` and wrote 3 questions per video (speaker_words, paraphrase, negative) for all 18 reported videos and the unlabelled parts of LXaFHI_2Hus (dev): 19 videos, 57 questions, merged into `evaluation/golden_set_agent.yaml` with `author: claude-code`.
- `evaluation/agent_set.py` validates the file: 0 problems.
- A word-run scan found no question or note sharing more than 6 consecutive words with its transcript; the four 6-word runs (common phrases) were reworded in the notes.
- Two independent verifier subagents are checking every positive span and every negative question.

### 2c. Answers: budget change

- Gemini's primary model (gemini-3.6-flash) returned 503 "high demand" on every request in run 1; the backup (gemini-3.5-flash-lite) answered each one. The log shows exactly one failed primary attempt per request, so each request costs 2 calls, and 120 calls is about 60 requests, not the 95 planned.
- Decision: one run of the 19 human questions, then as many AI-written questions as the budget allows, negatives first in each round. The planned second human run (spread check) is skipped for budget. For the spread, Claude also labels the earlier saved answers to the same 19 questions (rows 24 and 33 to 50, from 18 Oct and 10 Nov) at no API cost.
