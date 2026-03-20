from __future__ import annotations

from functools import cached_property
from typing import Literal

from pydantic import AliasChoices, Field
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
    bedrock_region: str = Field(default="us-east-1", validation_alias=AliasChoices("BEDROCK_REGION", "AWS_REGION"))
    bedrock_profile: str | None = Field(default=None, validation_alias=AliasChoices("BEDROCK_PROFILE", "AWS_PROFILE"))
    bedrock_access_key_id: str | None = Field(default=None, validation_alias=AliasChoices("BEDROCK_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"))
    bedrock_secret_access_key: str | None = Field(default=None, validation_alias=AliasChoices("BEDROCK_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"))
    bedrock_session_token: str | None = Field(default=None, validation_alias=AliasChoices("BEDROCK_SESSION_TOKEN", "AWS_SESSION_TOKEN"))

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
    
    bidder_models: list[str] = Field(
        default_factory=lambda: ["triage", "bid_fast", "bid_deep"],
        alias="ARBITER_BIDDER_MODELS",
    )

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_model_triage: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL_TRIAGE")
    openai_model_bid_fast: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL_BID_FAST")
    openai_model_bid_deep: str = Field(default="gpt-4.1", alias="OPENAI_MODEL_BID_DEEP")
    openai_model_test_gen: str = Field(default="gpt-4.1", alias="OPENAI_MODEL_TEST_GEN")
    openai_model_perf_reason: str = Field(default="gpt-4.1", alias="OPENAI_MODEL_PERF_REASON")
    anthropic_model_triage: str = Field(default="claude-sonnet-4-5", alias="ANTHROPIC_MODEL_TRIAGE")
    anthropic_model_bid_fast: str = Field(default="claude-sonnet-4-5", alias="ANTHROPIC_MODEL_BID_FAST")
    anthropic_model_bid_deep: str = Field(default="claude-sonnet-4-5", alias="ANTHROPIC_MODEL_BID_DEEP")
    anthropic_model_test_gen: str = Field(default="claude-sonnet-4-5", alias="ANTHROPIC_MODEL_TEST_GEN")
    anthropic_model_perf_reason: str = Field(default="claude-sonnet-4-5", alias="ANTHROPIC_MODEL_PERF_REASON")

    max_parallel_bidders: int = Field(default=8, alias="ARBITER_MAX_PARALLEL_BIDDERS")
    max_parallel_validators: int = Field(default=4, alias="ARBITER_MAX_PARALLEL_VALIDATORS")
    max_runtime_minutes: int = Field(default=10, alias="ARBITER_MAX_RUNTIME_MINUTES")
    max_file_churn: int = Field(default=8, alias="ARBITER_MAX_FILE_CHURN")
    max_recovery_rounds: int = Field(default=3, alias="ARBITER_MAX_RECOVERY_ROUNDS")
    replay_mode: Literal["off", "record", "replay"] = Field(default="off", alias="ARBITER_REPLAY_MODE")

    def _lane_model(self, lane: str) -> str:
        if self.model_provider == "bedrock":
            return getattr(self, f"bedrock_model_{lane}")
        if self.model_provider == "openai":
            return getattr(self, f"openai_model_{lane}")
        return getattr(self, f"anthropic_model_{lane}")

    @cached_property
    def model_lanes(self) -> dict[str, ModelLaneConfig]:
        return {
            "triage": ModelLaneConfig(name="triage", provider=self.model_provider, model_id=self._lane_model("triage")),
            "bid_fast": ModelLaneConfig(name="bid_fast", provider=self.model_provider, model_id=self._lane_model("bid_fast")),
            "bid_deep": ModelLaneConfig(name="bid_deep", provider=self.model_provider, model_id=self._lane_model("bid_deep")),
            "test_gen": ModelLaneConfig(name="test_gen", provider=self.model_provider, model_id=self._lane_model("test_gen")),
            "perf_reason": ModelLaneConfig(name="perf_reason", provider=self.model_provider, model_id=self._lane_model("perf_reason")),
        }


def load_runtime_config() -> RuntimeConfig:
    return RuntimeConfig()
