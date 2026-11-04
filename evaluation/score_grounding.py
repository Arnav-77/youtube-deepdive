"""Score grounding and refusal from the hand labels. Reads files only.

    python evaluation/score_grounding.py

Inputs: evaluation/grounding_labels.yaml (hand labels), evaluation/answers/*.json
(saved answers) and evaluation/golden_set.yaml (gold spans). No database, no
network, no Gemini call. The rules are in docs/grounding.md.

If a label block cannot be trusted, the scorer prints every problem and
prints no numbers: a saved answer file is missing; the segments no longer
match the saved text (a stale label); a label or refused value is not
allowed. A block that is simply not labelled yet is listed and left out of
every number. It is never counted as a failure.

Every number is printed with its sample size. Refusal accuracy is never
combined with the grounding rate.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.citations import citation_segments
from evaluation.answer_files import (
    ANSWERS_DIR,
    LABELS_PATH,
    excerpts_by_rank,
    load_answer,
    segment_hash,
)
from evaluation.relevance import overlaps

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_set.yaml"
LABEL_VALUES = ("supported", "partly", "unsupported")
# A note with this text marks a segment whose support sits in another excerpt.
LINK_NOTE = "support found in"


def model_name(name: str) -> str:
    """Drop the LiteLLM provider prefix, so one model has one name.

    The request is logged as "gemini/gemini-3.6-flash", but the response
    reports "gemini-3.6-flash". Both are the same model, so both group together.
    """
    return name.partition("/")[2] or name


def norm(question: str) -> str:
    """Compare questions the way a person would: case and spacing ignored."""
    return " ".join(str(question).split()).lower()


@dataclass
class Row:
    """One labelled answer, reduced to what the numbers need."""

    file: str
    question: str
    model: str
    refused: bool
    labels: list = field(default_factory=list)   # one per segment; empty if refused
    notes: list = field(default_factory=list)    # same length as labels
    invalid_citations: int = 0                   # cites to excerpts never sent
    gold: list | None = None                     # gold spans; None if not in golden set
    retrieved: list = field(default_factory=list)  # (start, end) of each excerpt sent


@dataclass
class Collected:
    rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    unlabelled: list = field(default_factory=list)
    saved: dict = field(default_factory=dict)    # file name -> saved answer
    examples: list = field(default_factory=list)  # labelled, question not in golden set
    leading_segments: int = 0                    # scored segments that opened with a citation


def golden_lookup(golden: dict) -> dict:
    """Normalised question -> its gold spans as (start, end) tuples."""
    out = {}
    for video in golden.get("videos") or []:
        for question in video.get("questions") or []:
            spans = [(float(a), float(b)) for a, b in question.get("spans") or []]
            out[norm(question["q"])] = spans
    return out


def check_block(block: dict, answer: dict) -> list:
    """Problems that make this block untrustworthy. Empty list means it is usable."""
    problems = []
    refused = block.get("refused")
    if refused not in (None, True, False):
        problems.append(f"refused must be true or false, not {refused!r}")

    segments = block.get("segments") or []
    want = [[s.index, segment_hash(s.text)] for s in citation_segments(answer["answer"])]
    got = [[s.get("index"), s.get("hash")] for s in segments]
    if got != want:
        problems.append("segments no longer match the saved text (stale label); "
                        "re-label from the current answer file")

    for s in segments:
        label = s.get("label")
        if label is not None and label not in LABEL_VALUES:
            problems.append(f"segment {s.get('index')}: label {label!r} is not one of "
                            + ", ".join(LABEL_VALUES))
        note = s.get("note")
        if note is not None and not isinstance(note, str):
            problems.append(f"segment {s.get('index')}: note must be text")
    return problems


def collect(labels: dict, golden: dict, answers_dir: Path) -> Collected:
    """Read every saved answer and every label block. Pure apart from reading files."""
    result = Collected()
    gold = golden_lookup(golden)

    if answers_dir.exists():
        for path in sorted(answers_dir.glob("*.json")):
            try:
                result.saved[path.name] = load_answer(path)
            except (ValueError, KeyError) as exc:
                result.errors.append(f"{path.name}: {exc}")

    seen = set()
    for block in (labels or {}).get("answers") or []:
        name = block.get("answer_file")
        if name in seen:
            result.errors.append(f"{name}: has two blocks in the labels file")
            continue
        seen.add(name)
        if name not in result.saved:
            result.errors.append(f"{name}: labelled, but its saved answer file is missing")
            continue

        answer = result.saved[name]
        problems = check_block(block, answer)
        if problems:
            result.errors += [f"{name}: {p}" for p in problems]
            continue

        refused = block.get("refused")
        if refused is None:
            result.unlabelled.append(name)
            continue
        segments = block.get("segments") or []
        if not refused and any(s.get("label") is None for s in segments):
            result.unlabelled.append(name)
            continue

        question = norm(answer["question"])
        if question not in gold:
            # Not in the golden set: an example, shown but kept out of every number.
            result.examples.append(name)
            continue

        segs = citation_segments(answer["answer"])
        excerpts = excerpts_by_rank(answer)
        cited = [n for seg in segs for n in seg.cited]
        if not refused:
            result.leading_segments += sum(seg.leads_with_citation for seg in segs)
        result.rows.append(Row(
            file=name,
            question=answer["question"],
            model=model_name(answer.get("model") or "unknown"),
            refused=bool(refused),
            labels=[] if refused else [s.get("label") for s in segments],
            notes=[] if refused else [s.get("note") or "" for s in segments],
            invalid_citations=sum(1 for n in cited if n not in excerpts),
            gold=gold[question],
            retrieved=[(float(e["start_seconds"]), float(e["end_seconds"]))
                       for e in answer["excerpts"]],
        ))
    return result


def refusal_table(rows: list) -> dict:
    """Refusal correctness, judged only against gold spans. Never mixed with grounding."""
    t = {"judged": 0, "correct_refusal": 0, "missed_refusal": 0,
         "false_refusal": 0, "false_refusal_retrieval": 0, "false_refusal_generation": 0,
         "answered_with_gold": 0}
    for r in rows:
        if r.gold is None:
            continue
        t["judged"] += 1
        if not r.gold:
            # The answer is not in the video: refusing is right, answering is a miss.
            t["correct_refusal" if r.refused else "missed_refusal"] += 1
        elif r.refused:
            t["false_refusal"] += 1
            # Did the excerpts the model saw contain the answer? If not, retrieval failed.
            if any(overlaps(s, e, r.gold) for s, e in r.retrieved):
                t["false_refusal_generation"] += 1
            else:
                t["false_refusal_retrieval"] += 1
        else:
            t["answered_with_gold"] += 1
    t["accurate"] = t["correct_refusal"] + t["answered_with_gold"]
    return t


def summarise(rows: list) -> dict:
    answered = [r for r in rows if not r.refused]
    segments = [(label, note) for r in answered for label, note in zip(r.labels, r.notes)]
    every_supported = [r for r in answered if r.labels and all(l == "supported" for l in r.labels)]
    return {
        "answers": len(rows),
        "refused": len(rows) - len(answered),
        "answered": len(answered),
        "segments": len(segments),
        "supported": sum(l == "supported" for l, _ in segments),
        "partly": sum(l == "partly" for l, _ in segments),
        "unsupported": sum(l == "unsupported" for l, _ in segments),
        "every_segment_supported": len(every_supported),
        "wrong_link_notes": sum(LINK_NOTE in n for _, n in segments),
        "answers_citing_unsent": sum(1 for r in answered if r.invalid_citations),
        "refusal": refusal_table(rows),
    }


def pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}% ({part}/{whole})" if whole else "n/a (0 of 0)"


def section(title: str, s: dict) -> list:
    out = [f"== {title}: {s['answers']} labelled answers "
           f"(refused {s['refused']}, answered {s['answered']}) =="]
    out.append(f"  grounding rate, segments: {pct(s['supported'], s['segments'])}"
               f"  [of answered answers]")
    out.append(f"    supported {s['supported']}, partly {s['partly']}, "
               f"unsupported {s['unsupported']}")
    out.append(f"  answer-level rate: {pct(s['every_segment_supported'], s['answered'])}"
               f"  [answered answers where every segment is supported]")
    out.append(f"  wrong-link notes (support in another excerpt): {s['wrong_link_notes']}"
               f" of {s['segments']} segments")
    out.append(f"  answers citing excerpts never sent: {s['answers_citing_unsent']}"
               f" of {s['answered']} answered")
    rt = s["refusal"]
    out.append(f"  refusal accuracy: {pct(rt['accurate'], rt['judged'])}"
               f"  [{rt['judged']} answers matched to a golden question]")
    out.append(f"    correct refusals {rt['correct_refusal']}, "
               f"missed refusals {rt['missed_refusal']} (answered, gold span empty), "
               f"false refusals {rt['false_refusal']}")
    out.append(f"      false refusal causes: retrieval miss {rt['false_refusal_retrieval']}, "
               f"generation miss {rt['false_refusal_generation']}")
    out.append(f"    answered where gold span exists: {rt['answered_with_gold']}")
    return out


def format_report(collected: Collected, golden: dict) -> str:
    gold = golden_lookup(golden)
    rows = collected.rows
    lines = section("All models", summarise(rows))
    for model in sorted({r.model for r in rows}):
        lines.append("")
        lines += section(f"model {model}", summarise([r for r in rows if r.model == model]))

    saved_questions = {norm(a["question"]) for a in collected.saved.values()}
    labelled_questions = {norm(r.question) for r in rows if r.gold is not None}
    lines += ["", "== Coverage =="]
    lines.append(f"  golden questions: {len(gold)}")
    lines.append(f"  with a saved answer: {len(saved_questions & set(gold))} of {len(gold)}")
    lines.append(f"  with a scored answer: {len(labelled_questions)} of {len(gold)}")
    lines.append(f"  saved answers not yet labelled: {len(collected.unlabelled)}")
    for name in collected.unlabelled:
        lines.append(f"    {name}")
    answered_segments = summarise(rows)["segments"]
    lines.append(f"  segments that opened with a citation, carried to the claim after it: "
                 f"{collected.leading_segments} of {answered_segments} answered segments")
    lines.append(f"  examples, question not in golden_set.yaml, left out of every number: "
                 f"{len(collected.examples)}")
    for name in collected.examples:
        lines.append(f"    {name}")
    return "\n".join(lines)


def main() -> None:
    golden = yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8")) or {}
    labels = (yaml.safe_load(LABELS_PATH.read_text(encoding="utf-8"))
              if LABELS_PATH.exists() else {}) or {}
    collected = collect(labels, golden, ANSWERS_DIR)
    if collected.errors:
        print("NOT SCORED. The labels file has problems that must be fixed first:")
        for problem in collected.errors:
            print(f"  - {problem}")
        sys.exit(1)
    print(format_report(collected, golden))


if __name__ == "__main__":
    main()
