import pytest

from perplex_agent.subagents import SubagentManager, extract_message_content


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def chat_completion(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return {"choices": [{"message": {"role": "assistant", "content": "fake-result"}}]}


@pytest.mark.asyncio
async def test_spawn_wait_result(tmp_path) -> None:
    fake = _FakeClient()
    mgr = SubagentManager(
        fake,  # type: ignore[arg-type]
        default_model="sonar",
        max_concurrent=2,
        subagent_timeout_s=30.0,
        system_prompt="sys",
        state_file=tmp_path / "s.json",
    )
    sid = await mgr.spawn("do research on X")
    await mgr.wait_ids([sid])
    rec = mgr.get(sid)
    assert rec is not None
    assert rec.status.value == "done"
    assert rec.result == "fake-result"
    assert fake.calls and fake.calls[0]["model"] == "sonar"


@pytest.mark.asyncio
async def test_spawn_custom_model() -> None:
    fake = _FakeClient()
    mgr = SubagentManager(
        fake,  # type: ignore[arg-type]
        default_model="sonar",
        max_concurrent=1,
        subagent_timeout_s=30.0,
        system_prompt="sys",
        state_file=None,
    )
    sid = await mgr.spawn("task", model="sonar-pro")
    await mgr.wait_ids([sid])
    assert fake.calls[0]["model"] == "sonar-pro"


def test_extract_string_content() -> None:
    data = {"choices": [{"message": {"content": "hello"}}]}
    assert extract_message_content(data) == "hello"
