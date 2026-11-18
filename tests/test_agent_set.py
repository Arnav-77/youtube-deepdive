"""evaluation/agent_set.validate, and the committed AI-written set itself.

Reads YAML files only; runs no script.
"""

import copy

import pytest
import yaml

from evaluation.agent_set import AGENT_PATH, GOLDEN_PATH, validate

GOLDEN = {"videos": [{"video_id": "vid00000001", "set": "reported", "duration_s": 600,
                      "questions": [{"q": "Human question?", "spans": [[1, 2]]}]}]}
GOOD = {"videos": [{"video_id": "vid00000001", "set": "reported", "duration_s": 600,
                    "questions": [
                        {"q": "a", "type": "speaker_words", "author": "claude-code",
                         "spans": [[10, 60]]},
                        {"q": "b", "type": "paraphrase", "author": "claude-code",
                         "spans": [[100, 160]]},
                        {"q": "c", "type": "negative", "author": "claude-code", "spans": []},
                    ]}]}


def test_a_good_file_has_no_problems():
    assert validate(GOOD, GOLDEN) == []


@pytest.mark.parametrize("change, expect", [
    (lambda d: d["videos"][0].update(set="dev"), "differs from golden_set"),
    (lambda d: d["videos"][0]["questions"][0].update(spans=[[10, 700]]), "outside"),
    (lambda d: d["videos"][0]["questions"][0].update(spans=[[60, 10]]), "outside"),
    (lambda d: d["videos"][0]["questions"][2].update(spans=[[1, 2]]), "negative question"),
    (lambda d: d["videos"][0]["questions"][1].update(author="arnav"), "author"),
    (lambda d: d["videos"][0]["questions"][1].update(type="speaker_words"), "one question of each"),
    (lambda d: d["videos"][0]["questions"][0].update(q="human  QUESTION?"), "repeats a human"),
    (lambda d: d["videos"][0].update(video_id="other000001"), "not in golden_set"),
])
def test_each_rule_is_enforced(change, expect):
    bad = copy.deepcopy(GOOD)
    change(bad)
    assert any(expect in p for p in validate(bad, GOLDEN))


def test_the_committed_agent_set_is_valid():
    if not AGENT_PATH.exists():
        pytest.skip("golden_set_agent.yaml not written yet")
    agent = yaml.safe_load(AGENT_PATH.read_text(encoding="utf-8"))
    golden = yaml.safe_load(GOLDEN_PATH.read_text(encoding="utf-8"))
    assert validate(agent, golden) == []
