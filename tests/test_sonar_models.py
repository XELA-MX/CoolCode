from perplex_agent.config import (
    SONAR_CHAT_MODEL_IDS,
    SONAR_CHAT_MODELS,
    resolve_sonar_chat_model,
)


def test_resolve_sonar_chat_model_ok() -> None:
    assert resolve_sonar_chat_model("sonar-pro") == "sonar-pro"
    assert resolve_sonar_chat_model("  SONAR  ") == "sonar"


def test_resolve_sonar_chat_model_reject() -> None:
    assert resolve_sonar_chat_model("gpt-4") is None
    assert resolve_sonar_chat_model("") is None


def test_sonar_ids_cover_tuple() -> None:
    assert SONAR_CHAT_MODEL_IDS == frozenset(mid for mid, _ in SONAR_CHAT_MODELS)
