from __future__ import annotations

import os

from arbiter.settings import Settings, load_settings


def build_chat_model(settings: Settings | None = None):
    """Instantiate the configured chat model provider."""

    settings = settings or load_settings()

    if settings.model_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when MODEL_PROVIDER=openai.")

        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.model_name,
            temperature=0,
            api_key=settings.openai_api_key,
        )

    if settings.model_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is required when MODEL_PROVIDER=anthropic."
            )

        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.model_name,
            temperature=0,
            api_key=settings.anthropic_api_key,
        )

    if not (settings.aws_region or os.environ.get("AWS_REGION")):
        raise ValueError("AWS_REGION is required when MODEL_PROVIDER=bedrock.")

    if settings.aws_profile:
        os.environ["AWS_PROFILE"] = settings.aws_profile

    from langchain_aws import ChatBedrockConverse

    kwargs = {
        "model_id": settings.model_name,
        "temperature": 0,
    }
    if settings.aws_region:
        kwargs["region_name"] = settings.aws_region
    if settings.aws_access_key_id:
        kwargs["aws_access_key_id"] = settings.aws_access_key_id
    if settings.aws_secret_access_key:
        kwargs["aws_secret_access_key"] = settings.aws_secret_access_key
    if settings.aws_session_token:
        kwargs["aws_session_token"] = settings.aws_session_token

    return ChatBedrockConverse(**kwargs)
