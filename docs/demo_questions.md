# Demo questions

Questions that answered well in the finish run on 18 Nov 2026: each one was answered (not refused), and the AI judge (Claude Code) labelled every segment of the answer **supported** by the excerpts it cites (`evaluation/grounding_labels_agent.yaml`, AI-labelled, not checked by Arnav). They were answered through the real `/ask` code on the dev branch by the backup model, gemini-3.5-flash-lite, because the main model was returning 503 that day. On the live site the main model may answer instead, and the wording will differ: answers are not reproducible.

Latency is the laptop's (dev branch, Delhi to Ohio), not production's.

| # | Video | Question (exact text) | Row | Laptop latency |
|---|---|---|---|---|
| 1 | LXaFHI_2Hus (habits talk) | What did the speaker learn from the person who climbed Everest? | 56 | 10.8 s |
| 2 | E0Hmnixke2g (ML algorithms in 17 min) | How does a model get trained on data where every example comes with the correct answer? | 66 | 8.9 s |
| 3 | i3AkTO9HLXo (Markov chains, part 1) | what is the markov property | 61 | 9.7 s |
| 4 | prWyZhcktn4 (confusion matrix) | why do we need a confusion matrix in the first place? | 70 | 13.0 s |
| 5 | 4HKqjENq9OU (KNN) | what the hell is knn? | 72 | 20.7 s |
| 6 | KZeIEiBrT_w (the strange math that predicts almost anything) | What is the damping factor in PageRank, and what share of the time does the random surfer follow a link versus jump to a random page? | 82 | 8.7 s |
| 7 | LXaFHI_2Hus (habits talk) | What are the ultradian cycles that Nathaniel Kleitman described, and how long does one wave of alertness last? | 113 | 3.6 s |

Notes:

- Questions 1 to 5 are Arnav's hand-labelled golden questions. Questions 6 and 7 are from the AI-written set.
- Question 1: both evaluated answers (rows 24 and 56) cite excerpt [1], which starts at 51.72 s, so the link opens at about 0:51.
- Question 2 is the paraphrase case: word search missed this passage and dense search ranked it first (`evaluation/results/retrieval_human.txt`).
- A refusal to show deliberately, if asked "does it ever say no?": "How many days does it take for a new habit to become automatic?" on LXaFHI_2Hus (AI-written negative, row 75) was refused correctly, because the talk never says.
- Avoid "how to be consistent" on LXaFHI_2Hus in the demo: it was refused in both runs although the answer is in the video (Report, Section 7.8).
