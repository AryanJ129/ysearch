from ysearch import llm


def test_slug_uses_dot_not_dash():
    """Anti-regression: the dashed form (claude-haiku-4-5) is not a valid
    OpenRouter model ID."""
    assert llm.MODEL == "anthropic/claude-haiku-4.5"
    assert "4.5" in llm.MODEL
    assert "4-5" not in llm.MODEL


def test_model_available_found():
    payload = {"data": [{"id": "anthropic/claude-haiku-4.5"}, {"id": "other/model"}]}
    assert llm.model_available(payload)


def test_model_available_missing():
    payload = {"data": [{"id": "anthropic/claude-haiku-4-5"}]}  # dashed impostor
    assert not llm.model_available(payload)


def test_model_available_empty_payload():
    assert not llm.model_available({})
