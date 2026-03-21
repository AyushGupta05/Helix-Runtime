from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from arbiter.agents.backend import ProviderModelRouter
from arbiter.runtime.config import RuntimeConfig
from arbiter.runtime.replay import ReplayManager
from arbiter.runtime.store import MissionStore


def _replay_manager(tmp_path: Path) -> ReplayManager:
    store = MissionStore(str(tmp_path / "state.db"))
    return ReplayManager(store, str(tmp_path / "replay"), mode="off")


def test_openai_router_uses_request_timeout(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class _DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "langchain_openai",
        types.SimpleNamespace(ChatOpenAI=_DummyOpenAI),
    )

    replay = _replay_manager(tmp_path)
    try:
        config = RuntimeConfig(
            MODEL_PROVIDER="openai",
            OPENAI_API_KEY="test-openai-key",
            ARBITER_PROVIDER_REQUEST_TIMEOUT_SECONDS=12.5,
        )

        router = ProviderModelRouter(config, replay)
        router._get_model("bid_fast.openai")
    finally:
        replay.store.close()

    assert captured["request_timeout"] == 12.5
    assert "timeout" not in captured


def test_anthropic_router_uses_default_request_timeout(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    class _DummyAnthropic:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setitem(
        sys.modules,
        "langchain_anthropic",
        types.SimpleNamespace(ChatAnthropic=_DummyAnthropic),
    )

    replay = _replay_manager(tmp_path)
    try:
        config = RuntimeConfig(
            MODEL_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="test-anthropic-key",
            ARBITER_PROVIDER_REQUEST_TIMEOUT_SECONDS=17.0,
        )

        router = ProviderModelRouter(config, replay)
        router._get_model("proposal_gen.anthropic")
    finally:
        replay.store.close()

    assert captured["default_request_timeout"] == 17.0
    assert "timeout" not in captured


def test_openai_router_retries_transient_invoke_failures(monkeypatch, tmp_path: Path) -> None:
    invoke_attempts = 0

    class _DummyOpenAI:
        def __init__(self, **kwargs) -> None:
            del kwargs

        def invoke(self, messages):
            del messages
            nonlocal invoke_attempts
            invoke_attempts += 1
            if invoke_attempts == 1:
                raise RuntimeError("error return without exception set")
            return types.SimpleNamespace(
                content=json.dumps({"status": "ok"}),
                usage_metadata={},
                response_metadata={"model_name": "gpt-5-mini"},
            )

    monkeypatch.setitem(
        sys.modules,
        "langchain_openai",
        types.SimpleNamespace(ChatOpenAI=_DummyOpenAI),
    )

    replay = _replay_manager(tmp_path)
    try:
        config = RuntimeConfig(
            MODEL_PROVIDER="openai",
            OPENAI_API_KEY="test-openai-key",
        )
        router = ProviderModelRouter(config, replay)
        result = router.invoke("bid_fast.openai", {"system": "Return JSON", "user": "Make a bounded strategy."})
    finally:
        replay.store.close()

    assert invoke_attempts == 2
    assert result.provider == "openai"
    assert result.model_id == "gpt-5-mini"


def test_anthropic_router_does_not_retry_model_not_found(monkeypatch, tmp_path: Path) -> None:
    invoke_attempts = 0

    class _DummyAnthropic:
        def __init__(self, **kwargs) -> None:
            del kwargs

        def invoke(self, messages):
            del messages
            nonlocal invoke_attempts
            invoke_attempts += 1
            raise RuntimeError(
                "Error code: 404 - {'type': 'error', 'error': {'type': 'not_found_error', 'message': 'model: missing-model'}}"
            )

    monkeypatch.setitem(
        sys.modules,
        "langchain_anthropic",
        types.SimpleNamespace(ChatAnthropic=_DummyAnthropic),
    )

    replay = _replay_manager(tmp_path)
    try:
        config = RuntimeConfig(
            MODEL_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="test-anthropic-key",
        )
        router = ProviderModelRouter(config, replay)
        with pytest.raises(RuntimeError):
            router.invoke("bid_fast.anthropic", {"system": "Return JSON", "user": "Make a bounded strategy."})
    finally:
        replay.store.close()

    assert invoke_attempts == 1
