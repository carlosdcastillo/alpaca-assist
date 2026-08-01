"""Coverage for the served model list and the Ollama-name -> Fireworks/

Anthropic model-ID mapping in anthropic_ollama_server.py. Both must agree:
a model listed in MODELS_JSON (what populates the Preferences model
dropdown) that map_ollama_to_model doesn't know how to route would look
selectable but silently fall back to DEFAULT_MODEL when used.
"""
from __future__ import annotations

import json

from anthropic_ollama_server import MODELS_JSON
from anthropic_ollama_server import map_ollama_to_model


def _model_names() -> set[str]:
    return {m["name"] for m in json.loads(MODELS_JSON)["models"]}


class TestModelsJson:
    def test_is_valid_json(self) -> None:
        json.loads(MODELS_JSON)  # raises if malformed

    def test_includes_kimi_k3(self) -> None:
        assert "kimi-k3" in _model_names()

    def test_every_fireworks_kimi_or_glm_model_has_a_mapping(self) -> None:
        """Every kimi-/glm- entry in the served list must route somewhere

        other than the DEFAULT_MODEL fallback, or picking it in the UI
        would silently run a different model than the one selected.
        """
        for name in _model_names():
            if name.startswith("kimi-") or name.startswith("glm-"):
                assert map_ollama_to_model(name).startswith("accounts/fireworks/"), name


class TestMapOllamaToModel:
    def test_kimi_k3_maps_to_the_fireworks_model_id(self) -> None:
        assert map_ollama_to_model("kimi-k3") == "accounts/fireworks/models/kimi-k3"

    def test_unknown_model_falls_back_to_default(self) -> None:
        from anthropic_ollama_server import DEFAULT_MODEL

        assert map_ollama_to_model("not-a-real-model") == DEFAULT_MODEL
