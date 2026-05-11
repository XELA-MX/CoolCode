import pytest

from perplex_agent.orchestrator import _parse_agent_json


def test_parse_plain_json() -> None:
    raw = '{"action": "final_answer", "final_text": "ok", "subagent_tasks": [], "wait_ids": []}'
    assert _parse_agent_json(raw)["action"] == "final_answer"


def test_parse_fenced_json() -> None:
    raw = 'Here:\n```json\n{"action": "final_answer", "final_text": "x", "subagent_tasks": [], "wait_ids": []}\n```'
    assert _parse_agent_json(raw)["final_text"] == "x"


def test_parse_invalid() -> None:
    with pytest.raises(ValueError):
        _parse_agent_json("not json")
