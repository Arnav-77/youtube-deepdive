"""Every evaluation number in one command, with no API calls.

    python evaluation/run_all.py
    python evaluation/run_all.py > evaluation/results/summary.txt

1. Retrieval: dense, lexical and hybrid on the human set (golden_set.yaml)
   and the AI-written set (golden_set_agent.yaml), reported and dev apart,
   AI-written results also per question type. Query embeddings come from the
   query_embeddings cache only: if one is missing, the run stops with a
   message instead of calling the embedding API (run_eval.forbid_embedding_calls).
2. Paired comparison, dense against lexical, question by question
   (reciprocal rank; a miss counts 0).
3. Grounding: the scorer on Arnav's labels (grounding_labels.yaml) and on
   Claude's labels (grounding_labels_agent.yaml), each on its own. Claude's
   labels are scored by question source (human-written or AI-written) and
   by run. Agreement is printed on the answers both labelled.

Reads the dev branch only (dev_guard). Writes nothing anywhere: the
embedding call is replaced before the first query, so the cache insert in
embed_query is never reached either.
"""

import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

HERE = Path(__file__).resolve().parent
HUMAN_GOLDEN = HERE / "golden_set.yaml"
AGENT_GOLDEN = HERE / "golden_set_agent.yaml"
HUMAN_LABELS = HERE / "grounding_labels.yaml"
AGENT_LABELS = HERE / "grounding_labels_agent.yaml"
RUN = "fixed_1000_200"
K = 5
METHODS = ("dense", "lexical", "hybrid")


def load(path: Path) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}) or {}


def paired(a: list, b: list) -> dict:
    """Question-by-question comparison of two methods' records. Pure.

    Records must be in the same question order. Reciprocal rank decides:
    higher is better, equal is a tie. Negative questions are skipped.
    """
    out = {"better": 0, "worse": 0, "tied": 0}
    for ra, rb in zip(a, b):
        if ra["negative"]:
            continue
        sa = 1 / ra["rank"] if ra["rank"] else 0.0
        sb = 1 / rb["rank"] if rb["rank"] else 0.0
        out["better" if sa > sb else "worse" if sa < sb else "tied"] += 1
    return out


def fmt(v, spec=".3f"):
    return "n/a" if v is None else format(v, spec)


def retrieval_section(session, golden: dict, name: str, by_type: bool) -> tuple[list, dict]:
    from evaluation.run_eval import METHODS as FUNCS, evaluate, summarise

    lines, records = [], {}
    for set_name in ("reported", "dev"):
        for method in METHODS:
            recs = []
            evaluate(session, golden, RUN, K, set_name, FUNCS[method], recs)
            records[(set_name, method)] = recs
            groups = [("all", recs)]
            if by_type:
                for t in ("speaker_words", "paraphrase", "negative"):
                    groups.append((t, [r for r in recs if r["type"] == t]))
            for label, group in groups:
                if not group:
                    continue
                s = summarise(group, K)
                if s["n"]:
                    lines.append(f"  {name:<6} {set_name:<9} {label:<14} {method:<8} "
                                 f"{s['hits']:>3}/{s['n']:<3} {fmt(s['recall']):>7} {fmt(s['mrr']):>7} "
                                 f"{s['rank1']:>3}/{s['n']:<3} {fmt(s['abs_offset_median'], '.0f'):>6} "
                                 f"{s['late']:>3}/{s['hits']:<3}")
                else:
                    lines.append(f"  {name:<6} {set_name:<9} {label:<14} {method:<8} "
                                 f"negatives: {s['negatives']}, retrieval returned nothing for "
                                 f"{s['negatives_empty']}")
    return lines, records


def grounding_section(title: str, labels: dict, goldens: list, commit: str | None) -> list:
    from evaluation.answer_files import ANSWERS_DIR
    from evaluation.score_grounding import collect, merge_golden, summarise

    golden = merge_golden(goldens)
    c = collect(labels, golden, ANSWERS_DIR, commit)
    if c.errors:
        return [f"  {title}: NOT SCORED, {len(c.errors)} label problems (run score_grounding.py)"]
    s = summarise(c.rows)
    rt = s["refusal"]

    def p(a, b):
        return f"{100 * a / b:.0f}% ({a}/{b})" if b else "n/a (0/0)"

    by_model = {}
    for r in c.rows:
        by_model.setdefault(r.model, []).append(r)
    models = "; ".join(f"{m} {p(sm['every_segment_supported'], sm['answered'])}"
                       for m, sm in ((m, summarise(rs)) for m, rs in sorted(by_model.items())))
    return [
        f"  {title}",
        f"    answers {s['answers']} (refused {s['refused']}), segments {s['segments']}",
        f"    grounding rate {p(s['supported'], s['segments'])}, partly {p(s['partly'], s['segments'])}, "
        f"unsupported {p(s['unsupported'], s['segments'])}",
        f"    answer-level (every segment supported) {p(s['every_segment_supported'], s['answered'])}",
        f"    answer-level by model: {models or 'none'}",
        f"    refusal: correct {rt['correct_refusal']}, missed {rt['missed_refusal']}, "
        f"false {rt['false_refusal']} (retrieval {rt['false_refusal_retrieval']}, "
        f"generation {rt['false_refusal_generation']}), answered with gold {rt['answered_with_gold']}",
        f"    unlabelled saved answers: {len(c.unlabelled)}",
    ]


def main() -> None:
    from evaluation.dev_guard import require_dev
    from evaluation.run_eval import CacheMiss, forbid_embedding_calls

    require_dev()
    forbid_embedding_calls()

    from app.db import SessionLocal
    from app.embeddings import CACHE_STATS
    from evaluation.score_grounding import agreement, format_agreement

    human, agent = load(HUMAN_GOLDEN), load(AGENT_GOLDEN)
    out = ["DeepDive evaluation summary (evaluation/run_all.py). No API calls.",
           f"chunking_run {RUN}, k={K}. Human = Arnav's labels; AI = written or labelled by "
           "Claude Code, never mixed into human numbers.", ""]
    out.append("RETRIEVAL")
    out.append(f"  {'labels':<6} {'set':<9} {'questions':<14} {'method':<8} {'hits':>7} "
               f"{'R@5':>7} {'MRR':>7} {'R@1':>7} {'|off|s':>6} {'late':>7}")
    session = SessionLocal()
    try:
        lines, human_recs = retrieval_section(session, human, "human", False)
        out += lines
        if agent:
            lines, agent_recs = retrieval_section(session, agent, "AI", True)
            out += lines
    except CacheMiss as exc:
        sys.exit(f"STOPPED: {exc}. Run run_eval.py once without --cache-only "
                 "(it spends embedding quota), then run this again.")
    finally:
        session.close()

    out += ["", "PAIRED, dense vs lexical (reciprocal rank per question)"]
    both = {m: human_recs[("reported", m)] + human_recs[("dev", m)] for m in METHODS}
    pr = paired(both["dense"], both["lexical"])
    out.append(f"  human set, all {sum(pr.values())} questions: dense better {pr['better']}, "
               f"worse {pr['worse']}, tied {pr['tied']}")
    ph = paired(both["dense"], both["hybrid"])
    out.append(f"  human set, dense vs hybrid: dense better {ph['better']}, worse {ph['worse']}, "
               f"tied {ph['tied']}")
    if agent:
        ab = {m: agent_recs[("reported", m)] + agent_recs[("dev", m)] for m in METHODS}
        for t in ("speaker_words", "paraphrase"):
            pa = paired([r for r in ab["dense"] if r["type"] == t],
                        [r for r in ab["lexical"] if r["type"] == t])
            out.append(f"  AI set, {t}: dense better {pa['better']}, worse {pa['worse']}, "
                       f"tied {pa['tied']}")

    human_labels, agent_labels = load(HUMAN_LABELS), load(AGENT_LABELS)
    out += ["", "GROUNDING (segments judged against their own cited excerpts; docs/grounding.md)"]
    out += grounding_section("HUMAN labels (Arnav), human questions, all saved answers",
                             human_labels, [human], None)
    if agent_labels:
        who = agent_labels.get("labeller", "claude-code")
        out += grounding_section(f"AI labels ({who}), human questions, this run (local-*)",
                                 agent_labels, [human], "local-")
        out += grounding_section(f"AI labels ({who}), human questions, earlier run (commit 'local' and Render)",
                                 {"answers": [b for b in agent_labels.get("answers") or []
                                              if b.get("run") == "earlier"]}, [human], None)
        out += grounding_section(f"AI labels ({who}), AI-written questions, this run",
                                 agent_labels, [agent], "local-")
        from evaluation.answer_files import ANSWERS_DIR
        from evaluation.score_grounding import collect, merge_golden
        saved = collect({}, merge_golden([human]), ANSWERS_DIR).saved
        out += [""] + format_agreement(agreement(human_labels, agent_labels, saved),
                                       "human", "AI").splitlines()
    out += ["", f"query embeddings: {CACHE_STATS['hits']} cached, {CACHE_STATS['misses']} API requests"]
    print("\n".join(out))


if __name__ == "__main__":
    main()
