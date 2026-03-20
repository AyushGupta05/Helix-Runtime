from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ModelLaneConfig(BaseSettings):
    name: str
    provider: Literal["bedrock", "openai", "anthropic"] = "bedrock"
    model_id: str
    temperature: float = 0.0
    max_tokens: int = 4096


class RuntimeConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    model_provider: Literal["bedrock", "openai", "anthropic"] = Field(
        default="bedrock",
        alias="MODEL_PROVIDER",
    )
    bedrock_region: str = Field(default="us-east-1", alias="BEDROCK_REGION")
    bedrock_profile: str | None = Field(default=None, alias="BEDROCK_PROFILE")
    bedrock_access_key_id: str | None = Field(default=None, alias="BEDROCK_ACCESS_KEY_ID")
    bedrock_secret_access_key: str | None = Field(default=None, alias="BEDROCK_SECRET_ACCESS_KEY")
    bedrock_session_token: str | None = Field(default=None, alias="BEDROCK_SESSION_TOKEN")

    bedrock_model_triage: str = Field(
        default="us.amazon.nova-lite-v1:0",
        alias="BEDROCK_MODEL_TRIAGE",
    )
    bedrock_model_bid_fast: str = Field(
        default="us.amazon.nova-lite-v1:0",
        alias="BEDROCK_MODEL_BID_FAST",
    )
    bedrock_model_bid_deep: str = Field(
        default="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        alias="BEDROCK_MODEL_BID_DEEP",
    )
    bedrock_model_test_gen: str = Field(
        default="us.amazon.nova-pro-v1:0",
        alias="BEDROCK_MODEL_TEST_GEN",
    )
    bedrock_model_perf_reason: str = Field(
        default="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
        alias="BEDROCK_MODEL_PERF_REASON",
    )

    civic_url: str | None = Field(default=None, alias="CIVIC_URL")
    civic_token: str | None = Field(default=None, alias="CIVIC_TOKEN")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")

    max_parallel_bidders: int = Field(default=8, alias="ARBITER_MAX_PARALLEL_BIDDERS")
    max_parallel_validators: int = Field(default=4, alias="ARBITER_MAX_PARALLEL_VALIDATORS")
    max_runtime_minutes: int = Field(default=10, alias="ARBITER_MAX_RUNTIME_MINUTES")
    max_file_churn: int = Field(default=8, alias="ARBITER_MAX_FILE_CHURN")
    max_recovery_rounds: int = Field(default=3, alias="ARBITER_MAX_RECOVERY_ROUNDS")
    replay_mode: Literal["off", "record", "replay"] = Field(default="off", alias="ARBITER_REPLAY_MODE")

    @cached_property
    def model_lanes(self) -> dict[str, ModelLaneConfig]:
        return {
            "triage": ModelLaneConfig(name="triage", provider=self.model_provider, model_id=self.bedrock_model_triage),
            "bid_fast": ModelLaneConfig(name="bid_fast", provider=self.model_provider, model_id=self.bedrock_model_bid_fast),
            "bid_deep": ModelLaneConfig(name="bid_deep", provider=self.model_provider, model_id=self.bedrock_model_bid_deep),
            "test_gen": ModelLaneConfig(name="test_gen", provider=self.model_provider, model_id=self.bedrock_model_test_gen),
            "perf_reason": ModelLaneConfig(name="perf_reason", provider=self.model_provider, model_id=self.bedrock_model_perf_reason),
        }


def load_runtime_config() -> RuntimeConfig:
    return RuntimeConfig()

