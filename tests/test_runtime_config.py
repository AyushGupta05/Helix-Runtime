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
