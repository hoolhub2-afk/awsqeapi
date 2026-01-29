from src.core.model_mapping import VALID_AMAZONQ_MODELS, map_model_to_amazonq


def test_map_model_to_amazonq_always_returns_supported_model():
    assert map_model_to_amazonq("claude-sonnet-4") in VALID_AMAZONQ_MODELS
    assert map_model_to_amazonq("claude-opus-4-5-20251101") in VALID_AMAZONQ_MODELS
    assert map_model_to_amazonq("gpt-4.1") in VALID_AMAZONQ_MODELS


def test_map_model_to_amazonq_unknown_model_falls_back_to_default():
    assert map_model_to_amazonq("totally-unknown-model", default_model="claude-haiku-4.5") == "claude-haiku-4.5"
