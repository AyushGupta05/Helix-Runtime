from __future__ import annotations

import pytest
from pydantic import ValidationError

from arbiter.runtime.config import RuntimeConfig


def test_bedrock_model_provider_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("MODEL_PROVIDER", "bedrock")
    monkeypatch.delenv("ARBITER_ENABLED_PROVIDERS", raising=False)

    with pytest.raises(ValidationError):
        RuntimeConfig()
