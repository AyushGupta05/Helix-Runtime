from __future__ import annotations

from pathlib import Path

import pytest

from arbiter.agents.backend import _estimate_cost_usage, _normalize_usage_metadata
from arbiter.agents.backend import ModelInvocationResult
from arbiter.core.contracts import MissionSpec, MissionSummary
from arbiter.runtime.store import MissionStore


def test_estimates_openai_cost_from_usage_metadata_when_billing_metadata_is_missing() -> None:
    raw_usage = {
        "usage_metadata": {
            "input_tokens": 285,
            "output_tokens": 1420,
            "total_tokens": 1705,
            "input_token_details": {"audio": 0, "cache_read": 0},
            "output_token_details": {"audio": 0, "reasoning": 768},
        },
        "response_metadata": {
            "token_usage": {
                "completion_tokens": 1420,
                "prompt_tokens": 285,
                "total_tokens": 1705,
                "completion_tokens_details": {
                    "accepted_prediction_tokens": 0,
                    "audio_tokens": 0,
                    "reasoning_tokens": 768,
                    "rejected_prediction_tokens": 0,
                },
                "prompt_tokens_details": {"audio_tokens": 0, "cached_tokens": 0},
            },
            "model_provider": "openai",
            "model_name": "gpt-5-mini-2025-08-07",
        },
    }

    token_usage = _normalize_usage_metadata(raw_usage)
    cost_usage = _estimate_cost_usage(
        raw_usage=raw_usage,
        token_usage=token_usage,
        provider="openai",
        model_id="gpt-5-mini",
    )

    assert token_usage == {
        "input_tokens": 285,
        "output_tokens": 1420,
        "reasoning_tokens": 768,
        "total_tokens": 1705,
    }
    assert cost_usage == pytest.approx({"usd": 0.00291125})


def test_estimates_anthropic_cost_from_usage_metadata_when_billing_metadata_is_missing() -> None:
    raw_usage = {
        "usage_metadata": {
            "input_tokens": 288,
            "output_tokens": 406,
            "total_tokens": 694,
            "input_token_details": {
                "cache_read": 0,
                "cache_creation": 0,
                "ephemeral_5m_input_tokens": 0,
                "ephemeral_1h_input_tokens": 0,
            },
        },
        "response_metadata": {
            "model_provider": "anthropic",
            "model_name": "claude-sonnet-4-20250514",
            "usage": {
                "cache_creation": {
                    "ephemeral_1h_input_tokens": 0,
                    "ephemeral_5m_input_tokens": 0,
                },
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "input_tokens": 288,
                "output_tokens": 406,
            },
        },
    }

    token_usage = _normalize_usage_metadata(raw_usage)
    cost_usage = _estimate_cost_usage(
        raw_usage=raw_usage,
        token_usage=token_usage,
        provider="anthropic",
        model_id="claude-sonnet-4-20250514",
    )

    assert token_usage == {
        "input_tokens": 288,
        "output_tokens": 406,
        "total_tokens": 694,
    }
    assert cost_usage == pytest.approx({"usd": 0.006954})


def test_estimates_anthropic_haiku_cost_from_usage_metadata_when_billing_metadata_is_missing() -> None:
    raw_usage = {
        "usage_metadata": {
            "input_tokens": 500,
            "output_tokens": 250,
            "total_tokens": 750,
            "input_token_details": {
                "cache_read": 0,
                "cache_creation": 0,
                "ephemeral_5m_input_tokens": 0,
                "ephemeral_1h_input_tokens": 0,
            },
        },
        "response_metadata": {
            "model_provider": "anthropic",
            "model_name": "claude-3-5-haiku-20241022",
            "usage": {
                "cache_creation": {
                    "ephemeral_1h_input_tokens": 0,
                    "ephemeral_5m_input_tokens": 0,
                },
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "input_tokens": 500,
                "output_tokens": 250,
            },
        },
    }

    token_usage = _normalize_usage_metadata(raw_usage)
    cost_usage = _estimate_cost_usage(
        raw_usage=raw_usage,
        token_usage=token_usage,
        provider="anthropic",
        model_id="claude-3-5-haiku-20241022",
    )

    assert token_usage == {
        "input_tokens": 500,
        "output_tokens": 250,
        "total_tokens": 750,
    }
    assert cost_usage == pytest.approx({"usd": 0.0014})


def test_usage_summary_prefers_total_tokens_and_usd_without_double_counting(tmp_path: Path) -> None:
    mission_id = "usage-mission"
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    store = MissionStore(str(tmp_path / "state.db"))
    store.upsert_mission(
        mission_id=mission_id,
        status="running",
        repo_path=str(repo_path),
        objective="Test usage accounting",
        branch_name=None,
        outcome=None,
        spec=MissionSpec(mission_id=mission_id, repo_path=str(repo_path), objective="Test usage accounting"),
        summary=MissionSummary(mission_id=mission_id, repo_path=str(repo_path), objective="Test usage accounting"),
    )

    invocation = ModelInvocationResult(
        content="{}",
        provider="openai",
        model_id="gpt-5-mini",
        lane="bid_fast.openai",
        token_usage={"input_tokens": 285, "output_tokens": 1420, "total_tokens": 1705},
        cost_usage={"usd": 0.00291125},
    )
    store.save_model_invocation(
        mission_id,
        invocation,
        invocation_id="inv-1",
        task_id="task-1",
        bid_id="bid-1",
        provider="openai",
        lane="bid_fast.openai",
        model_id="gpt-5-mini",
        invocation_kind="bid_generation",
        status="completed",
        raw_usage={},
        token_usage=invocation.token_usage,
        cost_usage=invocation.cost_usage,
    )

    usage_summary = store._usage_summary(mission_id, "task-1")
    store.close()

    assert usage_summary["mission"]["total_tokens"] == 1705
    assert usage_summary["active_task"]["total_tokens"] == 1705
    assert usage_summary["mission"]["total_cost"] == pytest.approx(0.00291125)
    assert usage_summary["active_task"]["total_cost"] == pytest.approx(0.00291125)
    assert usage_summary["by_provider"]["openai"]["total_tokens"] == 1705
    assert usage_summary["by_provider"]["openai"]["total_cost"] == pytest.approx(0.00291125)
    assert usage_summary["invocations"][0]["invocation_id"] == "inv-1"
