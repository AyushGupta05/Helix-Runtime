from __future__ import annotations

import pytest
from pydantic import ValidationError

from arbiter.runtime.config import RuntimeConfig


def test_bedrock_model_provider_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "bedrock")
    monkeypatch.delenv("ARBITER_ENABLED_PROVIDERS", raising=False)

    with pytest.raises(ValidationError):
        RuntimeConfig()


def test_anthropic_defaults_use_supported_sonnet_models() -> None:
    assert RuntimeConfig.model_fields["anthropic_model_triage"].default == "claude-sonnet-4-20250514"
    assert RuntimeConfig.model_fields["anthropic_model_bid_fast"].default == "claude-sonnet-4-20250514"
    assert RuntimeConfig.model_fields["anthropic_model_bid_deep"].default == "claude-sonnet-4-20250514"


def test_provider_timeout_has_a_default_guardrail() -> None:
    assert RuntimeConfig.model_fields["provider_request_timeout_seconds"].default == 45.0


def test_preview_timeout_has_a_tighter_default_guardrail() -> None:
    assert RuntimeConfig.model_fields["preview_request_timeout_seconds"].default == 18.0


def test_proposal_timeout_has_a_guardrail() -> None:
    assert RuntimeConfig.model_fields["proposal_request_timeout_seconds"].default == 24.0


def test_proposal_generation_tokens_default_is_tighter() -> None:
    assert RuntimeConfig.model_fields["proposal_generation_max_tokens"].default == 4096


def test_anthropic_market_lanes_default_to_triage_and_deep_strategy_only() -> None:
    config = RuntimeConfig()

    assert config.market_lanes_for("anthropic") == ["triage", "bid_deep"]
    assert config.market_lanes_for("openai") == ["triage", "bid_fast", "bid_deep", "test_gen", "perf_reason"]


def test_single_provider_market_lanes_cover_the_full_market(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ARBITER_ENABLED_PROVIDERS", raising=False)
    config = RuntimeConfig(
        MODEL_PROVIDER="anthropic",
        OPENAI_API_KEY="",
        ANTHROPIC_API_KEY="test-anthropic-key",
    )

    assert config.market_lanes_for("anthropic") == ["triage", "bid_fast", "bid_deep", "test_gen", "perf_reason"]
