"""Unit tests for aru.providers — multi-provider LLM abstraction."""

from unittest.mock import patch, MagicMock

import pytest

from aru.providers import (
    BUILTIN_PROVIDERS,
    LEGACY_MODEL_ALIASES,
    ProviderConfig,
    create_model,
    get_available_models,
    get_model_display,
    get_provider,
    list_providers,
    load_providers_from_config,
    register_provider,
    resolve_model_ref,
    _get_actual_model_id,
    _get_max_tokens,
    _init_providers,
)


class TestResolveModelRef:
    def test_provider_slash_model(self):
        assert resolve_model_ref("anthropic/claude-sonnet-4-5") == ("anthropic", "claude-sonnet-4-5")

    def test_ollama_model(self):
        assert resolve_model_ref("ollama/llama3.1") == ("ollama", "llama3.1")

    def test_legacy_alias_sonnet(self):
        assert resolve_model_ref("sonnet") == ("anthropic", "claude-sonnet-4-5")

    def test_legacy_alias_opus(self):
        assert resolve_model_ref("opus") == ("anthropic", "claude-opus-4")

    def test_legacy_alias_haiku(self):
        assert resolve_model_ref("haiku") == ("anthropic", "claude-haiku-3-5")

    def test_provider_name_only_uses_default(self):
        provider_key, model_name = resolve_model_ref("openai")
        assert provider_key == "openai"
        assert model_name == "gpt-4o"

    def test_unknown_name_falls_back_to_anthropic(self):
        provider_key, model_name = resolve_model_ref("some-random-model")
        assert provider_key == "anthropic"
        assert model_name == "some-random-model"


class TestGetActualModelId:
    def test_known_model_returns_full_id(self):
        provider = BUILTIN_PROVIDERS["anthropic"]
        assert _get_actual_model_id(provider, "claude-sonnet-4-5") == "claude-sonnet-4-5-20250929"

    def test_unknown_model_passes_through(self):
        provider = BUILTIN_PROVIDERS["ollama"]
        assert _get_actual_model_id(provider, "my-custom-model") == "my-custom-model"

    def test_full_id_also_works(self):
        provider = BUILTIN_PROVIDERS["anthropic"]
        assert _get_actual_model_id(provider, "claude-sonnet-4-5-20250929") == "claude-sonnet-4-5-20250929"


class TestGetMaxTokens:
    def test_known_model(self):
        provider = BUILTIN_PROVIDERS["anthropic"]
        assert _get_max_tokens(provider, "claude-sonnet-4-5") == 8192

    def test_unknown_model_uses_default(self):
        provider = BUILTIN_PROVIDERS["ollama"]
        assert _get_max_tokens(provider, "llama3.1", default=2048) == 2048


class TestProviderRegistry:
    def setup_method(self):
        _init_providers()  # Reset to built-in defaults

    def test_builtin_providers_exist(self):
        providers = list_providers()
        assert "anthropic" in providers
        assert "openai" in providers
        assert "ollama" in providers
        assert "groq" in providers
        assert "openrouter" in providers
        assert "deepseek" in providers

    def test_get_provider(self):
        provider = get_provider("anthropic")
        assert provider is not None
        assert provider.name == "Anthropic"

    def test_get_unknown_provider(self):
        assert get_provider("nonexistent") is None

    def test_register_custom_provider(self):
        custom = ProviderConfig(
            name="My Custom",
            api_key_env="MY_API_KEY",
            base_url="https://my-api.example.com/v1",
            default_model="my-model",
        )
        register_provider("my-custom", custom)
        assert get_provider("my-custom") is not None
        assert get_provider("my-custom").name == "My Custom"


class TestLoadProvidersFromConfig:
    def setup_method(self):
        _init_providers()

    def test_override_existing_provider(self):
        config = {
            "providers": {
                "ollama": {
                    "base_url": "http://my-server:11434",
                    "models": {
                        "deepseek-coder": {"id": "deepseek-coder-v2:latest"},
                    },
                },
            },
        }
        load_providers_from_config(config)
        provider = get_provider("ollama")
        assert provider.base_url == "http://my-server:11434"
        assert "deepseek-coder" in provider.models

    def test_add_new_provider(self):
        config = {
            "providers": {
                "my-llm": {
                    "type": "openai",
                    "name": "My LLM Service",
                    "api_key_env": "MY_LLM_KEY",
                    "base_url": "https://api.my-llm.com/v1",
                    "default_model": "my-model-v1",
                },
            },
        }
        load_providers_from_config(config)
        provider = get_provider("my-llm")
        assert provider is not None
        assert provider.name == "My LLM Service"
        assert provider.options.get("_provider_type") == "openai"

    def test_empty_config(self):
        load_providers_from_config({})
        # Should not crash, providers unchanged
        assert get_provider("anthropic") is not None


class TestCreateModel:
    @patch("aru.providers._create_provider_model")
    def test_anthropic_model(self, mock_create):
        mock_create.return_value = MagicMock()
        create_model("anthropic/claude-sonnet-4-5")
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert call_kwargs[1]["model_id"] == "claude-sonnet-4-5-20250929"

    @patch("aru.providers._create_provider_model")
    def test_ollama_model(self, mock_create):
        mock_create.return_value = MagicMock()
        create_model("ollama/llama3.1")
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args
        assert call_kwargs[1]["model_id"] == "llama3.1"

    @patch("aru.providers._create_provider_model")
    def test_legacy_alias(self, mock_create):
        mock_create.return_value = MagicMock()
        create_model("sonnet")
        call_kwargs = mock_create.call_args
        assert call_kwargs[1]["model_id"] == "claude-sonnet-4-5-20250929"

    @patch("aru.providers._create_provider_model")
    def test_max_tokens_override(self, mock_create):
        mock_create.return_value = MagicMock()
        create_model("anthropic/claude-sonnet-4-5", max_tokens=16384)
        call_kwargs = mock_create.call_args
        assert call_kwargs[1]["max_tokens"] == 16384

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            create_model("nonexistent-provider/some-model")


class TestGetModelDisplay:
    def test_anthropic(self):
        display = get_model_display("anthropic/claude-sonnet-4-5")
        assert "Anthropic" in display
        assert "claude-sonnet-4-5" in display

    def test_ollama(self):
        display = get_model_display("ollama/llama3.1")
        assert "Ollama" in display

    def test_legacy_alias(self):
        display = get_model_display("sonnet")
        assert "Anthropic" in display

    def test_unknown_returns_as_is(self):
        display = get_model_display("unknown/model")
        assert display == "unknown/model"


class TestGetAvailableModels:
    def test_includes_legacy_aliases(self):
        models = get_available_models()
        assert "sonnet" in models
        assert "opus" in models
        assert "haiku" in models

    def test_includes_provider_models(self):
        models = get_available_models()
        # Should have at least some anthropic and openai models
        anthropic_models = [k for k in models if k.startswith("anthropic/")]
        openai_models = [k for k in models if k.startswith("openai/")]
        assert len(anthropic_models) > 0
        assert len(openai_models) > 0
