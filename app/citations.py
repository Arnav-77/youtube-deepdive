"""Citation parsing: the one place that reads [n] markers out of an answer.

Pure: no model, no database, no configuration. citation checks (generation),
link building (main.py) and grounding segments (evaluation/) all read
citations through this module, so they cannot disagree about a bracket.
"""

import re
from dataclasses import dataclass, replace

# One bracket holding one number or a comma list: [1], [2][3], [1, 9].
_CITATION = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def cited_numbers(answer: str) -> set[int]:
    """Every excerpt number the answer cites, including inside [1, 9].

    The one parser for citations. invalid_citations and main.py's link
    building both use it, so a number the bounds check sees is a number the
    links see, and the reverse.
    """
    return {int(n) for group in _CITATION.findall(answer) for n in group.split(",")}


# A citation run: one or more adjacent brackets, with whitespace allowed
# between them, so "[1][2]" and "[1] [2]" are one run. The full stop or other
# punctuation right after the run belongs to the claim before it, so it is
# consumed too: "Claim [1]. Next" splits after "[1]. ", not before the dot.
# Built from _CITATION so the segmenter and cited_numbers agree on a bracket.
_CITATION_RUN = re.compile(rf"(?:{_CITATION.pattern}\s*)+[.,;:!?]*\s*")
_WORD = re.compile(r"\w")


@dataclass(frozen=True)
class Segment:
    """A piece of an answer, ending at a citation run (see docs/grounding.md).

    `text` is stripped and includes its citation run. `cited` is the sorted
    excerpt numbers in that run, empty for trailing uncited text. `start` and
    `end` are offsets of `text` in the answer. `leads_with_citation` is true
    when the answer opened this claim with its own citation, "[1] The climber
    said X.", and the segmenter carried that citation forward to the claim.
    """

    index: int
    text: str
    cited: tuple[int, ...]
    start: int
    end: int
    leads_with_citation: bool = False


def citation_segments(answer: str) -> list[Segment]:
    """Split an answer at its citation runs. Pure: no model, no database.

    Each segment runs from the end of the previous citation run (or the
    answer start) to the end of the next one. Text after the last run is one
    uncited segment. Whitespace-only pieces are dropped, so an answer that
    ends in a newline does not gain an empty segment.

    A run at the very start of the answer, with no claim before it, is not a
    segment of its own. It is held and attached to the claim after it, so
    "[1] The climber said X." is one segment, cited 1, and the claim is not
    counted as uncited.
    """
    segments: list[Segment] = []
    # A leading run waiting for its claim: where it starts, and its citations.
    lead_start: int | None = None
    lead_cited: set[int] = set()
    lead_end = 0

    def add(piece_start: int, piece_end: int, cited: set[int]) -> None:
        nonlocal lead_start, lead_end
        piece = answer[piece_start:piece_end]
        text = piece.strip()
        if not text:
            return
        first = piece_start + (len(piece) - len(piece.lstrip()))
        # A piece with no words, only brackets, such as "[2]" after "[1]. ",
        # is another citation for the claim before it: merge, and union the
        # citations, rather than making a segment with no claim in it.
        if not _WORD.search(_CITATION_RUN.sub(" ", piece)):
            if segments:
                previous = segments[-1]
                start = previous.start
                text = answer[start:piece_end].strip()
                segments[-1] = replace(
                    previous,
                    text=text,
                    cited=tuple(sorted(set(previous.cited) | cited)),
                    end=start + len(text),
                )
            else:
                # Leading run with no claim before it: hold it for the next claim.
                if lead_start is None:
                    lead_start = first
                lead_cited.update(cited)
                lead_end = piece_end
            return
        leads = lead_start is not None
        start = lead_start if leads else first
        cited_all = lead_cited | cited
        text = answer[start:piece_end].strip()
        segments.append(Segment(
            index=len(segments),
            text=text,
            cited=tuple(sorted(cited_all)),
            start=start,
            end=start + len(text),
            leads_with_citation=leads,
        ))
        lead_start = None
        lead_cited.clear()

    position = 0
    for run in _CITATION_RUN.finditer(answer):
        add(position, run.end(), cited_numbers(run.group(0)))
        position = run.end()
    add(position, len(answer), set())
    if lead_start is not None:
        # The answer is only citations, with no claim at all. Keep it as a
        # segment, so it is labelled and counted rather than silently dropped.
        text = answer[lead_start:lead_end].strip()
        segments.append(Segment(
            index=len(segments),
            text=text,
            cited=tuple(sorted(lead_cited)),
            start=lead_start,
            end=lead_start + len(text),
        ))
    return segments
