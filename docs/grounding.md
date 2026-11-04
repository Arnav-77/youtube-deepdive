# Grounding: what counts as a supported claim

This is the definition the grounding measurement uses. The answer checker (build step 3) must use the same definition. If this file and the scorer disagree, the scorer is wrong.

## Unit: the segment

An answer is split into segments at citation runs.

- A citation run is one or more adjacent markers: `[1]`, `[1][2]`, `[1] [2]`, or `[1, 9]`. The markers are found by `cited_numbers` in `app/generation.py`, the same parser the citation check and the links use.
- A segment is the text from the end of the previous run (or the start of the answer) up to and including the next run.
- Text after the last run is one uncited segment.
- Whitespace-only segments are dropped.

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

## Numbers the scorer reports

Every number is printed with its sample size, overall and per `model`.

- **Segment grounding rate:** supported segments divided by all segments, over answers that are not refused. Partly supported is a separate count, never added to the rate.
- **Answer-level rate:** answers that are not refused, have at least one segment, and have every segment supported, divided by all answers that are not refused. Shown next to the segment rate, because one unsupported sentence fails the whole answer.
- **Refusal table:** correct refusals, missed refusals, and false refusals with their cause. Refusal accuracy is never combined with the grounding rate.
- **Unlabelled and unanswered:** counts of questions with no saved answer and of answers with unlabelled segments. The report says how much is missing.

## Worked cases from this project

**"how to be consistent"** (LXaFHI_2Hus, spans 480-610). The speaker never says "consistent", so word search retrieves nothing. The answer is in the video. A refusal here is a **false refusal**: retrieval miss under word search, generation miss under dense search, where chunks do overlap the gold span.

**sklearn** (prWyZhcktn4, spans 880-900). The video mentions sklearn for 20 seconds and does not explain it. A sentence restating that the speaker mentions sklearn, with its citation, is **supported**. A sentence saying what sklearn is, from the model's own knowledge, is **unsupported**, even if true. A mixed answer gives **partly supported** segments.

**Everest, "This is because"** (LXaFHI_2Hus, spans 51-128). An answer might read: "The climber was asked what he remembered [1]. This is because ... [2]." The second segment is judged only against excerpt 2. If excerpt 2 states the second fact but not the link, the segment is **partly supported**. If the link is stated only in excerpt 1, the segment is still partly or unsupported, with the note `support found in [1]`.

## Known limits

- One labeller. No agreement measurement yet, so the boundary between partly and unsupported is one person's judgement. Report it as such.
- Segments are judged on excerpt text only. The excerpts are the 1,000-character chunks the model saw, so a claim can be supported by the video and still be unsupported by the chunks retrieved.
- Answers are not deterministic. A grounding rate is one answer per question until a spread check is run; the spread is reported with its sample size.
