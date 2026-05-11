from perplex_agent.history_compact import trim_planner_history, total_content_chars


def test_trim_drops_old_turns() -> None:
    history = [
        {"role": "user", "content": "original"},
        {"role": "assistant", "content": "a" * 5000},
        {"role": "user", "content": "b" * 5000},
        {"role": "assistant", "content": "c" * 5000},
    ]
    out = trim_planner_history(
        history,
        max_total_chars=4000,
        inject_max_chars=8000,
    )
    assert out[0]["content"] == "original"
    assert total_content_chars(out) <= 4000


def test_compact_subagent_block() -> None:
    big = "<subagent_results>\n" + "x" * 9000 + "\n</subagent_results>"
    history = [{"role": "user", "content": "q"}, {"role": "user", "content": big}]
    out = trim_planner_history(
        history,
        max_total_chars=100_000,
        inject_max_chars=2000,
    )
    assert len(out[1]["content"]) < len(big)
