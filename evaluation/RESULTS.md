# Retrieval results

All numbers use `fixed_1000_200` chunks, k = 5, the hand-labelled golden set in
`evaluation/golden_set.yaml`, and `evaluation/run_eval.py --method <m> --set <s>`.
Retrieval is deterministic and question embeddings are cached, so every number
can be reproduced by rerunning that command on the code at commit `36e3320`.

## Benchmark (14 Oct 2026)

19 questions on 5 videos. **Dev:** 5 questions on 1 video, the only set used for
any tuning. **Reported:** 14 questions on 4 videos, never tuned against. Each
video's set was fixed when it was chosen, before any evaluation ran.

## Results

| Set | Method | Recall@5 | Recall@1 | MRR | Median distance from answer start | Late citations |
|---|---|---|---|---|---|---|
| Reported (14) | Lexical | 0.857 | 7/14 | 0.631 | 40 s | 1 of 12 |
| Reported (14) | **Dense** | **1.000** | **12/14** | **0.917** | **15 s** | 5 of 14 |
| Reported (14) | Hybrid (RRF) | 1.000 | 10/14 | 0.815 | 22 s | 3 of 14 |
| Dev (5) | Lexical | 0.800 | 3/5 | 0.640 | 45 s | 2 of 4 |
| Dev (5) | **Dense** | **1.000** | 3/5 | **0.750** | **20 s** | 3 of 5 |
| Dev (5) | Hybrid (RRF) | 1.000 | 3/5 | 0.717 | 28 s | 2 of 5 |

- **Lexical:** Postgres full-text search. **Dense:** `gemini-embedding-001` at 768
  dimensions, cosine similarity in pgvector. **Hybrid:** Reciprocal Rank Fusion of
  the two (k = 60, top 20 from each).
- **Distance from answer start:** seconds between the start of the cited chunk and
  the start of the labelled answer, ignoring direction. **Late:** the citation
  starts after the answer does, so the user misses its beginning.

## Paired comparison (the claim that holds at this sample size)

Comparing each question with itself across methods:

- **Dense vs lexical:** dense ranked the labelled passage higher on 9 questions,
  equal on 10, lower on 0. A sign test gives about 0.4% (two sided), optimistic
  because questions on the same video are not independent.
- **Hybrid vs dense:** hybrid higher on 0, lower on 5, equal on 14. On no question
  did lexical rank the passage above dense, so fusing it in could only lower ranks.

## Controlled pairs (same passage, different wording)

| Video | Speaker's words | Lexical | Dense | Own words | Lexical | Dense |
|---|---|---|---|---|---|---|
| `LXaFHI_2Hus` | "first energy habit" | rank 1 | rank 1 | "how to be consistent" | miss | rank 4 |
| `E0Hmnixke2g` | "What is supervised learning?" | rank 1 | rank 1 | paraphrase avoiding "supervised" and "learn" | miss | rank 1 |

## Disclosures and limitations

- Predictions were committed before each result (commits `447c94d`, `f8e087a`,
  `22fea44`, `f42aaeb`, `36e3320`, `56b1972`). Not all held; the failures are kept.
- One label was corrected after a run (commit `ba0bfe6`): an ambiguous question had
  two correct answers and listed one. The correction raised its score.
- 19 questions on 5 videos. Recall@5 is now at its ceiling, so MRR, Recall@1 and
  citation distance carry comparisons from here.
- The set has no question that depends on a rare name the embedding model would
  not know, which is the case hybrid retrieval exists for.
- Citations point at the start of a chunk, so they are off by up to a minute;
  dense cites late more often than lexical.
- Retrieval only. Answer quality and grounding are not yet measured.