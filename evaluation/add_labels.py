"""Add hand-written labels to one video in the golden set.

Edit VIDEO_ID and QUESTIONS, then run. The script replaces that video's
`questions: []` with the labels below. It refuses to touch a video that
already has questions, so a re-run cannot silently overwrite earlier
labelling work.
"""

from pathlib import Path

VIDEO_ID = "i3AkTO9HLXo"

# (question, [[start, end], ...], quote, note)
QUESTIONS = [
    ("what is the markov property",
     [[110, 150]],
     "now let's discuss some properties",
     "Plain definition question using the video's own vocabulary. Expected to be the easy case for lexical retrieval."),

    ("what does the restaurant example show",
     [[150, 200]],
     "pizza on the first day",
     "Tests whether retrieval finds the EXAMPLE rather than the DEFINITION, when both discuss the same concept within a minute of each other."),

    ("how do you calculate the probability of the next day",
     [[340, 565]],
     "",
     "Span is wide (225s) because the worked calculation genuinely runs from 5:40 to the end of the video. Recorded as a known-wide label: it will flatter any retriever, so read its per-question result with that in mind."),

    ("does what happened yesterday matter for tomorrow",
     [[110, 150]],
     "",
     "THE VOCABULARY-MISMATCH CASE. Asks about the Markov property using none of its words: no markov, no property, no state, no transition. Prediction recorded 12 Oct 2026, BEFORE running: lexical retrieval misses this, dense retrieval finds it. Checking that prediction is the point."),
]

path = Path(__file__).resolve().parent / "golden_set.yaml"
text = path.read_text()

marker = f"  - video_id: {VIDEO_ID}\n"
if marker not in text:
    raise SystemExit(f"{VIDEO_ID} is not in the golden set")

start = text.index(marker)
end = text.find("\n  - video_id:", start + 1)
entry = text[start:end] if end != -1 else text[start:]

if "questions: []" not in entry:
    raise SystemExit(f"{VIDEO_ID} already has questions; refusing to overwrite")

block = "    questions:\n"
for question, spans, quote, note in QUESTIONS:
    spans_yaml = "[" + ", ".join(f"[{a}, {b}]" for a, b in spans) + "]"
    block += f"      - q: {question}\n"
    block += f"        spans: {spans_yaml}\n"
    if quote:
        block += f"        quote: {quote}\n"
    block += f"        note: >\n"
    for line in [note[i:i + 68] for i in range(0, len(note), 68)]:
        block += f"          {line}\n"

text = text[:start] + entry.replace("    questions: []\n", block) + (text[end:] if end != -1 else "")
path.write_text(text)
print(f"wrote {len(QUESTIONS)} questions for {VIDEO_ID}")