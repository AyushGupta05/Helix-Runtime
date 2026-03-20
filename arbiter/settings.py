from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    civic_url: str = Field(
        default="https://app.civic.com/hub/mcp?accountId=f05f13ef-c392-4ec2-a403-9c6c4a2fb06a&profile=default",
        alias="CIVIC_URL",
    )
    civic_token: str | None = Field(default=None, alias="CIVIC_TOKEN")

    model_provider: Literal["openai", "anthropic", "bedrock"] = Field(
        default="openai",
        alias="MODEL_PROVIDER",
    )
    model_name: str = Field(default="gpt-4.1-mini", alias="MODEL_NAME")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    aws_region: str | None = Field(default=None, alias="AWS_REGION")
    aws_profile: str | None = Field(default=None, alias="AWS_PROFILE")
    aws_access_key_id: str | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(
        default=None,
        alias="AWS_SECRET_ACCESS_KEY",
    )
    aws_session_token: str | None = Field(default=None, alias="AWS_SESSION_TOKEN")


def load_settings() -> Settings:
    return Settings()
