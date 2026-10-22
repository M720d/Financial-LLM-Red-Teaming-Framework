import os
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel

from finguard import config


class MissingCredentialsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    key_env: str
    base_url: str | None
    default_model: str


# All three expose OpenAI-compatible chat completions with tool calling.
PROVIDERS = {
    "xai": Provider("XAI_API_KEY", "https://api.x.ai/v1", "grok-3-mini"),
    "groq": Provider("GROQ_API_KEY", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    "openai": Provider("OPENAI_API_KEY", None, "gpt-4.1-mini"),
}


def provider() -> Provider:
    if config.PROVIDER not in PROVIDERS:
        raise MissingCredentialsError(
            f"Unknown FINGUARD_PROVIDER '{config.PROVIDER}'. Choose one of: {', '.join(PROVIDERS)}."
        )
    return PROVIDERS[config.PROVIDER]


def model_name() -> str:
    return config.MODEL_NAME or provider().default_model


def has_credentials() -> bool:
    return config.PROVIDER in PROVIDERS and bool(os.getenv(provider().key_env, "").strip())


def get_chat_model() -> BaseChatModel:
    p = provider()
    if not has_credentials():
        raise MissingCredentialsError(f"{p.key_env} is not set. Add it to .env in the project folder.")
    from langchain_openai import ChatOpenAI

    kwargs = {"model": model_name(), "api_key": os.environ[p.key_env], "timeout": 60, "max_retries": 8}
    if p.base_url:
        kwargs["base_url"] = p.base_url
    if config.TEMPERATURE.strip():
        kwargs["temperature"] = float(config.TEMPERATURE)
    return ChatOpenAI(**kwargs)


def check_connection(llm: BaseChatModel) -> None:
    """Fail fast on a bad key or model name instead of recording every case as an error."""
    import openai

    try:
        llm.invoke("Reply with OK.")
    except openai.OpenAIError as exc:
        raise MissingCredentialsError(f"Could not reach {config.PROVIDER} model '{model_name()}': {exc}") from exc
