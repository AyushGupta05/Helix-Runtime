from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class ModelLaneConfig(BaseSettings):
    name: str
    provider: Literal["openai", "anthropic"] = "openai"
    model_id: str
    temperature: float = 0.0
    max_tokens: int = 2048


class RuntimeConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    model_provider: Literal["openai", "anthropic"] = Field(
        default="openai",
        alias="MODEL_PROVIDER",
    )
    enabled_providers_raw: str | None = Field(default=None, alias="ARBITER_ENABLED_PROVIDERS")
    civic_url: str | None = Field(default=None, alias="CIVIC_URL")
    civic_token: str | None = Field(default=None, alias="CIVIC_TOKEN")
    
    bidder_models: list[str] = Field(
        default_factory=lambda: ["triage", "bid_fast", "bid_deep"],
        alias="ARBITER_BIDDER_MODELS",
    )

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_model_triage: str = Field(default="gpt-5-mini", alias="OPENAI_MODEL_TRIAGE")
    openai_model_bid_fast: str = Field(default="gpt-5-mini", alias="OPENAI_MODEL_BID_FAST")
    openai_model_bid_deep: str = Field(default="gpt-5.1-codex-mini", alias="OPENAI_MODEL_BID_DEEP")
    openai_model_test_gen: str = Field(default="gpt-5.1-codex-mini", alias="OPENAI_MODEL_TEST_GEN")
    openai_model_perf_reason: str = Field(default="gpt-5.1-codex-mini", alias="OPENAI_MODEL_PERF_REASON")
    anthropic_model_triage: str = Field(default="claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL_TRIAGE")
    anthropic_model_bid_fast: str = Field(default="claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL_BID_FAST")
    anthropic_model_bid_deep: str = Field(default="claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL_BID_DEEP")
    anthropic_model_test_gen: str = Field(default="claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL_TEST_GEN")
    anthropic_model_perf_reason: str = Field(default="claude-sonnet-4-20250514", alias="ANTHROPIC_MODEL_PERF_REASON")

    max_parallel_bidders: int = Field(default=8, alias="ARBITER_MAX_PARALLEL_BIDDERS")
    max_parallel_validators: int = Field(default=4, alias="ARBITER_MAX_PARALLEL_VALIDATORS")
    max_runtime_minutes: int = Field(default=10, alias="ARBITER_MAX_RUNTIME_MINUTES")
    max_file_churn: int = Field(default=8, alias="ARBITER_MAX_FILE_CHURN")
    max_recovery_rounds: int = Field(default=3, alias="ARBITER_MAX_RECOVERY_ROUNDS")
    replay_mode: Literal["off", "record", "replay"] = Field(default="off", alias="ARBITER_REPLAY_MODE")
    require_real_provider_bidding: bool = Field(default=True, alias="ARBITER_REQUIRE_REAL_PROVIDER_BIDDING")
    allow_degraded_bid_fallback: bool = Field(default=False, alias="ARBITER_ALLOW_DEGRADED_BID_FALLBACK")

    def _lane_model(self, provider: Literal["openai", "anthropic"], lane: str) -> str:
        return getattr(self, f"{provider}_model_{lane}")

    @cached_property
    def enabled_providers(self) -> list[Literal["openai", "anthropic"]]:
        if self.enabled_providers_raw:
            parsed = [item.strip() for item in self.enabled_providers_raw.split(",") if item.strip()]
            ordered = [provider for provider in ("openai", "anthropic") if provider in parsed]
            return ordered or [self.model_provider]
        providers: list[Literal["openai", "anthropic"]] = []
        if self.openai_api_key:
            providers.append("openai")
        if self.anthropic_api_key:
            providers.append("anthropic")
        if not providers:
            providers.append(self.model_provider)
        return providers

    @cached_property
    def default_provider(self) -> Literal["openai", "anthropic"]:
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
