from src.integrations.claude.converter import map_model_name


def test_map_model_name_accepts_canonical_models_from_ui():
    assert map_model_name("claude-sonnet-4-20250514") == "claude-sonnet-4"
    assert map_model_name("claude-sonnet-4-5-20250929") == "claude-sonnet-4.5"
    assert map_model_name("claude-haiku-4-5-20251001") == "claude-haiku-4.5"
    assert map_model_name("claude-opus-4-5-20251101") == "claude-opus-4.5"


def test_map_model_name_maps_family_variants_to_supported_models():
    assert map_model_name("claude-opus-4-20250514") == "claude-opus-4.5"
    assert map_model_name("claude-opus-4-1-20250805") == "claude-opus-4.5"
    assert map_model_name("claude-3-5-haiku-20241022") == "claude-haiku-4.5"

