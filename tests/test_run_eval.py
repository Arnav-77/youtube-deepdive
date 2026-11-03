import pytest
from evaluation.run_eval import overlaps


@pytest.mark.parametrize("chunk_start, chunk_end, spans, expected", [
    (20, 40, [(0, 100)], True),  # chunk inside span
    (0, 100, [(20, 40)], True),  # span inside chunk
    (10, 30, [(20, 40)], True),  # overlap on the left: chunk covers the span's start
    (30, 50, [(20, 40)], True),  # overlap on the right: chunk covers the span's end
    (50, 60, [(20, 40)], False),  # completely apart
    (40, 60, [(20, 40)], False),  # edges only touch: chunk starts as the span ends
    (5, 15, [(0, 2), (10, 20)], True),  # misses the first span, hits the second
    (0, 100, [], False),  # no spans: nothing to be relevant to
])
def test_overlaps(chunk_start, chunk_end, spans, expected):
    assert overlaps(chunk_start, chunk_end, spans) == expected
