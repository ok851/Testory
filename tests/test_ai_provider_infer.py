import json
from pathlib import Path

from modules.ai.ai_provider_infer import infer_provider_from_simple_config

CATALOG_PATH = Path(__file__).resolve().parents[1] / "ai_provider_catalog.json"


def _providers():
    data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    return data.get("providers") or []


def test_infer_openai_when_key_only():
    assert infer_provider_from_simple_config("", "sk-test", _providers()) == "openai"


def test_infer_ollama_when_no_key_and_empty_base():
    assert infer_provider_from_simple_config("", "", _providers()) == "ollama"


def test_infer_ollama_from_local_base_url():
    assert infer_provider_from_simple_config("http://127.0.0.1:11434", "", _providers()) == "ollama"


def test_infer_custom_openai_for_proxy_key():
    assert infer_provider_from_simple_config("", "tp-abc", _providers()) == "custom_openai"


def test_infer_deepseek_from_base_url():
    assert (
        infer_provider_from_simple_config("https://api.deepseek.com/v1", "sk-x", _providers())
        == "deepseek"
    )


def test_infer_custom_openai_for_unknown_host():
    assert (
        infer_provider_from_simple_config("https://proxy.example.com/v1", "sk-x", _providers())
        == "custom_openai"
    )
