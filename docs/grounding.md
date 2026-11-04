# Grounding: what counts as a supported claim

This is the definition the grounding measurement uses. The answer checker (build step 3) must use the same definition. If this file and the scorer disagree, the scorer is wrong.

## Unit: the segment

An answer is split into segments at citation runs.

- A citation run is one or more adjacent markers: `[1]`, `[1][2]`, `[1] [2]`, or `[1, 9]`. The markers are found by `cited_numbers` in `app/generation.py`, the same parser the citation check and the links use.
- A segment is the text from the end of the previous run (or the start of the answer) up to and including the next run.
- Text after the last run is one uncited segment.
- Whitespace-only segments are dropped.
- **A citation at the start of the answer** ("[1] The climber said X.") is not a segment on its own. It is held and attached to the claim after it, so the claim is cited 1. The segment's `leads_with_citation` flag is true, and the scorer reports how many scored segments used this rule. The rule is option A, chosen 4 Nov: the model writes its citation before the claim, and counting that claim as uncited would blame the model for layout.

Segments, not sentences, are the unit because a sentence can join two facts with one citation, and the segment is the smallest text the citation can be checked against.

## Judged against

A segment is judged only against the excerpts cited in its own run, as one union. Support that sits in an excerpt cited elsewhere in the answer does not count. The label stays partly or unsupported, with the note `support found in [n]`. The scorer counts these notes as wrong-link citations.

An uncited segment is unsupported.

## Labels for a segment

- **Supported.** The cited excerpts state the claim, including any link it asserts, such as "because" or "so".
- **Partly supported.** Some parts of the claim are stated in the cited excerpts and some are not. Only the stated parts count.
- **Unsupported.** The claim is not stated; or the citation points to excerpts that do not state it; or the segment has no citation. A claim that is true but not stated in the excerpts is unsupported.

## Refusal

Refusal is a hand label, `refused: true` or `refused: false`, set by the person labelling the answer. It is never inferred from missing citations or from an empty retrieval, because the citation-free case is exactly the case that needs a human decision.

Correctness is decided by the gold spans in `evaluation/golden_set.yaml`:

- **Correct refusal:** gold spans are `[]` (the answer is not in the video) and the app refused.
- **Missed refusal:** gold spans are `[]` and the app answered. This counts as a false answer.
- **False refusal:** gold spans are not empty and the app refused. Each false refusal has a cause:
  - *retrieval miss*: no retrieved chunk overlaps a gold span;
  - *generation miss*: a retrieved chunk overlaps a gold span, and the model refused anyway.
- **Answered:** gold spans are not empty and the app answered. Grounding applies.

## Workflow: from a logged answer to a number

Run these from the main folder, whose `.env` points at the dev branch. Each step reads files or makes one read-only query. None calls Gemini.

1. **Save the answer.** `python evaluation/save_answer.py --row N` copies one `query_logs` row and the chunks it cites into `evaluation/answers/rowN.json` (gitignored). Records the model, `model_requested`, `prompt_version`, `commit_sha`, `retrieval_method`, `chunking_run` and the chunk ids in rank order.
2. **Prepare the labels.** `python evaluation/label_answer.py evaluation/answers/rowN.json` prints each segment with its index, its hash, and the text and time of each excerpt it cites. It appends a block to `evaluation/grounding_labels.yaml` with `refused`, `label` and `note` left blank. Running it again for the same answer is refused.
3. **Label by hand.** Set `refused: true` or `false`. For an answered block, set each segment's `label` to `supported`, `partly` or `unsupported`. Add a `note` of `support found in [n]` where the support sits in another excerpt. The script never fills these.
4. **Score.** `python evaluation/score_grounding.py` prints the numbers, overall and per model, with sample sizes.

Scoring rules:

- **Stops, with every problem listed, when a block cannot be trusted:** the saved answer file is missing; the segments no longer match the saved text (a stale label); a label is not one of the three values; `refused` is not true or false.
- **Reports and leaves out, never a failure:** a block that is not yet labelled (`refused` blank, or an answered block with a blank label). Its answer is listed under "not yet labelled" and counted in no rate.
- **Reports, not a failure:** a citation to an excerpt the model was never sent. The segment still gets its hand label, normally `unsupported`.
- **Examples, left out of every number:** a saved answer whose question is not in `golden_set.yaml`. It is listed under "examples", and it counts in no rate, no refusal result and no segment count.

## Numbers the scorer reports

Every number is printed with its sample size, overall and per `model`.

- **Segment grounding rate:** supported segments divided by all segments, over answers that are not refused. Partly supported is a separate count, never added to the rate.
- **Answer-level rate:** answers that are not refused, have at least one segment, and have every segment supported, divided by all answers that are not refused. Shown next to the segment rate, because one unsupported sentence fails the whole answer.
- **Refusal table:** correct refusals, missed refusals, and false refusals with their cause. Refusal accuracy is never combined with the grounding rate.
- **Unlabelled and unanswered:** counts of questions with no saved answer and of answers with unlabelled segments. The report says how much is missing.

## Worked cases from this project

**"how to be consistent"** (LXaFHI_2Hus, spans 480-610). The speaker never says "consistent", so word search retrieves nothing. The answer is in the video. A refusal here is a **false refusal**: retrieval miss under word search, generation miss under dense search, where chunks do overlap the gold span.

**sklearn** (prWyZhcktn4, spans 880-900). The video mentions sklearn for 20 seconds and does not explain it. A sentence restating that the speaker mentions sklearn, with its citation, is **supported**. A sentence saying what sklearn is, from the model's own knowledge, is **unsupported**, even if true. A mixed answer gives **partly supported** segments.

**Everest, "This is because"** (LXaFHI_2Hus, spans 51-128). The example answer is `evaluation/answers/row26.json` (local, gitignored), with its block in `grounding_labels.yaml`. Its question is not in the golden set, so the scorer lists it as an example and leaves it out of every number. It exists to show the rule, not to be counted. The scored Everest answer is row 24, whose question is the golden one. An answer might read: "The climber was asked what he remembered [1]. This is because ... [2]." The second segment is judged only against excerpt 2. If excerpt 2 states the second fact but not the link, the segment is **partly supported**. If the link is stated only in excerpt 1, the segment is still partly or unsupported, with the note `support found in [1]`.

## Known limits

- One labeller. No agreement measurement yet, so the boundary between partly and unsupported is one person's judgement. Report it as such.
- Segments are judged on excerpt text only. The excerpts are the 1,000-character chunks the model saw, so a claim can be supported by the video and still be unsupported by the chunks retrieved.
- Answers are not deterministic. A grounding rate is one answer per question until a spread check is run; the spread is reported with its sample size.
