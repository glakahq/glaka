"""Chat model access. The ChatModel protocol keeps the provider swappable."""

from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel

from glaka.config import get_settings

T = TypeVar("T", bound=BaseModel)


class LlmError(RuntimeError):
    """The model provider rejected or failed a request."""


class ChatModel(Protocol):
    def generate(self, system: str, user: str, schema: type[T]) -> T:
        """Return the model's reply parsed and validated into `schema`."""
        ...


class OpenRouterChat:
    """OpenRouter's OpenAI-compatible chat API with strict JSON-schema output."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        key = api_key or (settings.openrouter_api_key and settings.openrouter_api_key.get_secret_value())
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set. Add it to .env (see .env.example).")
        self.api_key = key
        self.model = model or settings.chat_model
        self.base_url = settings.openrouter_url
        self.data_collection = "allow" if settings.openrouter_allow_data_collection else "deny"

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "X-Title": "Glaka",  # app name shown in your OpenRouter activity log
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "strict": True,
                        "schema": schema.model_json_schema(),
                    },
                },
                # No "temperature": newer models (e.g. Claude Haiku 5.5) don't accept it, and
                # require_parameters below would then find no host at all.
                "max_tokens": 1000,  # caps cost and runaway replies
                "provider": {
                    "require_parameters": True,  # only route to hosts that support the schema
                    # "deny" skips hosts that store or train on prompts (see config.py).
                    "data_collection": self.data_collection,
                },
            },
            timeout=60,
        )
        if response.is_error:
            # OpenRouter explains failures in the body (bad model id, no matching host...).
            raise LlmError(f"OpenRouter {response.status_code}: {response.text[:500]}")
        return schema.model_validate_json(response.json()["choices"][0]["message"]["content"])


class OllamaChat:
    """Local model via Ollama /api/chat, with the reply constrained to a JSON schema."""

    def __init__(self, base_url: str | None = None, model: str | None = None) -> None:
        settings = get_settings()
        self.base_url = base_url or settings.ollama_url
        self.model = model or settings.chat_model

    def generate(self, system: str, user: str, schema: type[T]) -> T:
        response = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "format": schema.model_json_schema(),
                "stream": False,
                "options": {"temperature": 0},
            },
            timeout=300,
        )
        response.raise_for_status()
        return schema.model_validate_json(response.json()["message"]["content"])


def make_chat_model() -> ChatModel:
    """Pick the provider from settings (LLM_PROVIDER)."""
    if get_settings().llm_provider == "ollama":
        return OllamaChat()
    return OpenRouterChat()
