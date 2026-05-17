from typing import Protocol, runtime_checkable

from assay.config import settings


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, prompt: str, **kwargs: object) -> str: ...


class AnthropicClient:
    def __init__(self, model: str) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "Install the 'anthropic' extra: uv sync --extra anthropic"
            ) from exc
        self._client = anthropic.Anthropic()
        self._model = model

    def complete(self, prompt: str, **kwargs: object) -> str:
        import anthropic

        msg = self._client.messages.create(
            model=self._model,
            max_tokens=kwargs.get("max_tokens", 1024),  # type: ignore[arg-type]
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text  # type: ignore[union-attr]


class OpenAIClient:
    def __init__(self, model: str) -> None:
        try:
            import openai
        except ImportError as exc:
            raise ImportError(
                "Install the 'openai' extra: uv sync --extra openai"
            ) from exc
        self._client = openai.OpenAI()
        self._model = model

    def complete(self, prompt: str, **kwargs: object) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=kwargs.get("max_tokens", 1024),  # type: ignore[arg-type]
        )
        return resp.choices[0].message.content or ""


def get_llm_client() -> LLMClient:
    provider = settings.llm_provider.lower()
    if provider == "anthropic":
        return AnthropicClient(settings.llm_model)
    if provider == "openai":
        return OpenAIClient(settings.llm_model)
    raise ValueError(
        f"Unknown llm_provider {settings.llm_provider!r}. Choose 'anthropic' or 'openai'."
    )
