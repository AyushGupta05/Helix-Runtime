from __future__ import annotations

import sys
import types
from pathlib import Path

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
