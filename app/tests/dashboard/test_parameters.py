from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.dashboard.parameters import _normalise_model, fetch_openrouter_models


class Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit):
        return self.payload


class ParameterPageTests(unittest.TestCase):
    def test_model_metadata_exposes_safe_filters(self):
        model = _normalise_model(
            {
                "id": "provider/reasoning-model",
                "name": "Reasoning Model",
                "context_length": 128000,
                "architecture": {
                    "input_modalities": ["text", "image"],
                    "output_modalities": ["text"],
                },
                "supported_parameters": [
                    "tools",
                    "reasoning",
                    "structured_outputs",
                    "web_search_options",
                ],
            }
        )
        self.assertEqual(model["company"], "provider")
        self.assertTrue(model["reasoning"])
        self.assertTrue(model["tools"])
        self.assertTrue(model["web"])
        self.assertTrue(model["structured"])
        self.assertIn("image", model["inputs"])

    def test_catalog_uses_authenticated_official_endpoint(self):
        response = Response(
            {
                "data": [
                    {
                        "id": "provider/model",
                        "architecture": {"output_modalities": ["text"]},
                        "supported_parameters": [],
                    }
                ]
            }
        )
        with patch("app.dashboard.parameters.urllib.request.urlopen", return_value=response) as call:
            models = fetch_openrouter_models("private-key")
        request = call.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/models")
        self.assertEqual(request.headers["Authorization"], "Bearer private-key")
        self.assertEqual(models[0]["id"], "provider/model")

    def test_page_renders_main_controls_without_loading_existing_secrets(self):
        from streamlit.testing.v1 import AppTest

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = AppTest.from_string(
                f"""
from types import SimpleNamespace
from pathlib import Path
from app.dashboard.parameters import parameters_view
settings = SimpleNamespace(configuration=Path({str(root / 'settings.json')!r}), database=Path({str(root / 'dashboard.db')!r}))
parameters_view(settings, {{'username':'tester'}})
"""
            ).run()
        self.assertFalse(app.exception)
        labels = {widget.label for widget in app.text_input}
        self.assertIn("Senha atual do painel", labels)
        self.assertIn("Chave API do OpenRouter", labels)
        self.assertIn("Token da API do bot", labels)
        self.assertIn("Senha do Neo4j", labels)
        self.assertTrue(any(button.label == "Salvar parâmetros" for button in app.button))


if __name__ == "__main__":
    unittest.main()
