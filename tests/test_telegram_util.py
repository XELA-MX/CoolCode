from perplex_agent.telegram_util import split_text


def test_split_short() -> None:
    assert split_text("abc") == ["abc"]


def test_split_long() -> None:
    s = "x" * 9000
    parts = split_text(s, chunk_size=4000)
    assert len(parts) == 3
    assert "".join(parts) == s
