"""Generation: turn retrieved chunks into a grounded, cited answer.

THE CITATION DESIGN, WHICH IS THE POINT OF THIS FILE
=====================================================
The model never writes a timestamp. It cites excerpt numbers -- [1], [2] --
and the caller maps those back to chunk ids and renders timestamps from the
database. A fabricated timestamp is therefore impossible by construction
rather than merely discouraged, which is a much stronger claim than "we
asked it nicely not to".

The excerpts sent to the model carry no timestamps at all, for the same
reason: nothing to copy, nothing to mangle, and fewer tokens.

WHAT THIS FILE DOES NOT DO
---------------------------
It does not raise when the model fails. It returns a result object with
`error` populated instead. The caller must always be able to write a
query_logs row, and a failed request is the most valuable row there is --
it is what Phase 04 mines for the golden set. An exception here would turn
a data point into a 500.
"""

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import litellm
from dotenv import load_dotenv

from app.retrieval import Retrieved

load_dotenv()

# LiteLLM prints a banner and assorted advice on import and on error.
# Silencing it keeps our own logs readable, which matters because stdout is
# the entire diagnostic surface on Render -- there is no shell to poke with.
litellm.suppress_debug_info = True

# The prompt lives in a text file, not in this module, so that a prompt
# change shows up as a readable diff and so Phase 04 can load several
# versions side by side and compare them. PROMPT_VERSION is the filename
# stem, and it is what goes into query_logs.prompt_version -- which is how
# the evaluation report will attribute a change in score to a change in
# wording.
PROMPT_VERSION = "answer_v1"
_TEMPLATE = (Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.txt").read_text(
    encoding="utf-8"
)

# Overridable by env var because model names change and a wrong one should
# be a config fix, not a code change.
PRIMARY_MODEL = os.getenv("GEMINI_MODEL", "gemini/gemini-3.6-flash")

# Empty on purpose. The architecture calls for a Groq fallback when Gemini's
# rate limit is exhausted, but there is no Groq key yet and shipping
# untested fallback code is worse than shipping none: it fails for the first
# time on the day the primary is already failing. Add the model string here
# once a key exists and the fallback has been exercised deliberately.
FALLBACK_MODELS: list[str] = []


@dataclass
class Generated:
    """Everything the caller needs to both answer the user and log the row.

    Every field is nullable because a failed generation still has to be
    logged, and a logging table that rejects incomplete rows loses exactly
    the requests worth studying.
    """

    answer: str | None = None
    model: str | None = None
    # What we asked for. None when no model was called (empty retrieval).
    model_requested: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    estimated_cost_usd: float | None = None
    latency_ms: int | None = None
    error: str | None = None
    # Citation numbers the model produced that do not exist in what we sent.
    # Kept separate from `error` because the answer may still be usable.
    invalid_citations: list[int] = field(default_factory=list)


def build_context(results: list[Retrieved]) -> str:
    """Number the excerpts 1..N for the model to cite.

    The number is the retrieval rank, so [1] is the top-scoring chunk. No
    chunk ids and no timestamps appear here -- the model has no use for
    either, and anything we do not send is something it cannot invent.
    """
    return "\n\n".join(f"[{r.rank}]\n{r.text}" for r in results)


_MARKER = re.compile(r"(__QUESTION__|__CONTEXT__)")


def render_prompt(question: str, context: str) -> str:
    """Fill the template in a SINGLE pass.

    This is an injection defence, not tidiness. Two sequential .replace()
    calls would scan the text inserted by the first call for the second
    call's marker. A transcript containing the literal string __QUESTION__,
    or a user question containing __CONTEXT__, would then get substituted
    into -- letting untrusted text rewrite the structure of the prompt.
    Splitting on both markers and interleaving means inserted text is never
    re-examined.
    """
    return "".join(
        question if part == "__QUESTION__"
        else context if part == "__CONTEXT__"
        else part
        for part in _MARKER.split(_TEMPLATE)
    )


_CITATION = re.compile(r"\[(\d+)\]")


def invalid_citations(answer: str, n_excerpts: int) -> list[int]:
    """Citation numbers in the answer that were never sent to the model.

    Validated in CODE, against the excerpts actually supplied, rather than
    trusted because the prompt asked for it. An injected instruction telling
    the model to cite excerpt 9 fails a bounds check here instead of
    reaching the user as a broken link.
    """
    cited = {int(n) for n in _CITATION.findall(answer)}
    return sorted(c for c in cited if c < 1 or c > n_excerpts)


def generate(question: str, results: list[Retrieved]) -> Generated:
    """Answer the question from the retrieved chunks. Never raises."""

    # Retrieval returned nothing. Do not call the model: there is nothing to
    # ground an answer in, and asking anyway invites exactly the unsupported
    # answer this whole design exists to prevent. Costs nothing, logs cleanly.
    if not results:
        return Generated(error="no_chunks_retrieved")

    prompt = render_prompt(question, build_context(results))

    started = time.perf_counter()
    try:
        response = litellm.completion(
            model=PRIMARY_MODEL,
            messages=[{"role": "user", "content": prompt}],
            # Temperature 0 to minimise sampling variance. It does NOT make
            # output reproducible: identical requests measured on 20 Sep 2026
            # produced different answers, and Gemini 3 warns that temperature
            # is being deprecated. The evaluation harness therefore runs each
            # question several times and reports the spread.
            temperature=0.0,
            fallbacks=FALLBACK_MODELS or None,
        )
    except Exception as exc:
        return Generated(
            model_requested=PRIMARY_MODEL,
            latency_ms=int((time.perf_counter() - started) * 1000),
            # Class name plus message here, unlike the public endpoints:
            # this string goes into the database, not to a user, and the
            # message is what tells a rate limit apart from a bad key.
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
    latency_ms = int((time.perf_counter() - started) * 1000)

    answer = (response.choices[0].message.content or "").strip()

    # response.model, not PRIMARY_MODEL. When a fallback fires these differ,
    # and a latency spike caused by failover would otherwise be
    # indistinguishable from a slow pipeline.
    used_model = getattr(response, "model", PRIMARY_MODEL)

    usage = getattr(response, "usage", None)

    # completion_cost can raise for a model whose pricing LiteLLM does not
    # know, which is common for preview models. A missing cost is a null
    # column, not a failed request.
    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception:
        cost = None

    return Generated(
        answer=answer or None,
        model=used_model,
        model_requested=PRIMARY_MODEL,
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        estimated_cost_usd=cost,
        latency_ms=latency_ms,
        error=None if answer else "empty_answer",
        invalid_citations=invalid_citations(answer, len(results)),
    )