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
    enabled_providers_raw: str | None = Field(default=None, alias="ARBITER_ENABLED_PROVIDERS")
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
    require_real_provider_bidding: bool = Field(default=True, alias="ARBITER_REQUIRE_REAL_PROVIDER_BIDDING")
    allow_degraded_bid_fallback: bool = Field(default=False, alias="ARBITER_ALLOW_DEGRADED_BID_FALLBACK")

    def _lane_model(self, provider: Literal["bedrock", "openai", "anthropic"], lane: str) -> str:
        return getattr(self, f"{provider}_model_{lane}")

    @cached_property
    def enabled_providers(self) -> list[Literal["bedrock", "openai", "anthropic"]]:
        if self.enabled_providers_raw:
            parsed = [item.strip() for item in self.enabled_providers_raw.split(",") if item.strip()]
            ordered = [provider for provider in ("bedrock", "openai", "anthropic") if provider in parsed]
            return ordered or [self.model_provider]
        providers: list[Literal["bedrock", "openai", "anthropic"]] = []
        if self.bedrock_profile or self.bedrock_access_key_id or self.bedrock_secret_access_key or self.bedrock_session_token or self.model_provider == "bedrock":
            providers.append("bedrock")
        if self.openai_api_key:
            providers.append("openai")
        if self.anthropic_api_key:
            providers.append("anthropic")
        if not providers:
            providers.append(self.model_provider)
        return providers

    @cached_property
    def default_provider(self) -> Literal["bedrock", "openai", "anthropic"]:
        return self.enabled_providers[0]

    @cached_property
    def model_lanes(self) -> dict[str, ModelLaneConfig]:
        lanes: dict[str, ModelLaneConfig] = {}
        for lane in ("triage", "bid_fast", "bid_deep", "test_gen", "perf_reason"):
            lanes[lane] = ModelLaneConfig(name=lane, provider=self.default_provider, model_id=self._lane_model(self.default_provider, lane))
            for provider in self.enabled_providers:
                key = f"{lane}.{provider}"
                lanes[key] = ModelLaneConfig(name=key, provider=provider, model_id=self._lane_model(provider, lane))
        return lanes


def load_runtime_config() -> RuntimeConfig:
    return RuntimeConfig()
