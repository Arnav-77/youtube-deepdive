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
