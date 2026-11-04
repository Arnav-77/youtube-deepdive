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
    `end` are offsets of `text` in the answer.
    """

    index: int
    text: str
    cited: tuple[int, ...]
    start: int
    end: int


def citation_segments(answer: str) -> list[Segment]:
    """Split an answer at its citation runs. Pure: no model, no database.

    Each segment runs from the end of the previous citation run (or the
    answer start) to the end of the next one. Text after the last run is one
    uncited segment. Whitespace-only pieces are dropped, so an answer that
    ends in a newline does not gain an empty segment.
    """
    segments: list[Segment] = []

    def add(piece_start: int, piece_end: int, cited: set[int]) -> None:
        piece = answer[piece_start:piece_end]
        text = piece.strip()
        if not text:
            return
        # A piece with no words, only brackets, such as "[2]" after "[1]. ",
        # is another citation for the claim before it: merge, and union the
        # citations, rather than making a segment with no claim in it.
        if segments and not _WORD.search(_CITATION_RUN.sub(" ", piece)):
            previous = segments[-1]
            start = previous.start
            text = answer[start:piece_end].strip()
            segments[-1] = replace(
                previous,
                text=text,
                cited=tuple(sorted(set(previous.cited) | cited)),
                end=start + len(text),
            )
            return
        start = piece_start + (len(piece) - len(piece.lstrip()))
        segments.append(Segment(
            index=len(segments),
            text=text,
            cited=tuple(sorted(cited)),
            start=start,
            end=start + len(text),
        ))

    position = 0
    for run in _CITATION_RUN.finditer(answer):
        add(position, run.end(), cited_numbers(run.group(0)))
        position = run.end()
    add(position, len(answer), set())
    return segments
