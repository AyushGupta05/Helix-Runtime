from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from arbiter.agents.backend import EditProposal, FileUpdate, ModelInvocationResult, ScriptedStrategyBackend
from arbiter.core.contracts import (
    BidGenerationMode,
    BidStatus,
    CapabilitySet,
    GenerationMode,
    PolicyDecision,
    RepoSnapshot,
    RolloutLevel,
    TaskNode,
    TaskRequirementLevel,
    TaskStatus,
    TaskType,
    SuccessCriteria,
)
from arbiter.market.archetypes import ARCHETYPES
from arbiter.mission.decomposer import GoalDecomposer
from arbiter.mission.runner import start_mission
from arbiter.sim.factory import BidGenerationBatch, SimulationFactory, VARIANTS
from tests.fake_provider_backend import make_provider_backend

NUM_ARCHETYPES = len(ARCHETYPES)
NUM_VARIANTS = len(VARIANTS)
NUM_SPECS = NUM_ARCHETYPES * NUM_VARIANTS


def _make_task(**overrides) -> TaskNode:
    defaults = dict(
        task_id="T1",
        title="Fix broken test",
        task_type=TaskType.BUGFIX,
        requirement_level=TaskRequirementLevel.REQUIRED,
        success_criteria=SuccessCriteria(description="Tests pass"),
        allowed_tools=["read_file", "edit_file", "run_tests"],
        candidate_files=["calc.py", "tests/test_calc.py"],
        risk_level=0.3,
    )
    defaults.update(overrides)
    return TaskNode(**defaults)


def _make_snapshot(**overrides) -> RepoSnapshot:
    defaults = dict(
        repo_path="/tmp/repo",
        branch="main",
        head_commit="abc123",
        complexity_hotspots=["calc.py", "utils.py", "api.py"],
        capabilities=CapabilitySet(runtime="python"),
    )
    defaults.update(overrides)
    return RepoSnapshot(**defaults)


def _mock_lane_config(model_id: str = "claude-sonnet-4-5") -> SimpleNamespace:
    return SimpleNamespace(model_id=model_id)


def _mock_router_config() -> SimpleNamespace:
    """Build a mock router config with model_lanes for all archetype default lanes."""
    lane_config = _mock_lane_config()
    lanes = {}
    for arch in ARCHETYPES:
        lanes[arch.default_lane] = lane_config
        lanes[f"{arch.default_lane}.anthropic"] = lane_config
    return SimpleNamespace(model_lanes=lanes)


def _mock_router_invoke(lane: str, prompt: dict) -> ModelInvocationResult:
    return ModelInvocationResult(
        content=json.dumps({
            "strategy_summary": f"Mock strategy for lane {lane}",
            "exact_action": "Apply targeted fix",
            "utility": 0.8,
            "risk": 0.2,
            "confidence": 0.75,
            "estimated_runtime_seconds": 45,
            "touched_files": ["calc.py"],
        }),
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        lane=lane,
        generation_mode=BidGenerationMode.PROVIDER_MODEL,
        raw_usage={"input_tokens": 100, "output_tokens": 200},
        token_usage={"input_tokens": 100, "output_tokens": 200},
        cost_usage={"usd": 0.003},
        prompt_preview="Task: Fix broken test",
        response_preview='{"strategy_summary": "Mock strategy"}',
        started_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:01+00:00",
    )


def _make_mock_backend():
    """Create a mock backend with router config and invoke method."""
    mock_backend = MagicMock()
    mock_backend.router = MagicMock()
    mock_backend.router.config = _mock_router_config()
    mock_backend.router.invoke = _mock_router_invoke
    mock_backend.market_generation_mode.return_value = BidGenerationMode.PROVIDER_MODEL
    mock_backend.supports_provider_bid_generation.return_value = True
    return mock_backend


class TestBidGenerationBatch:
    def test_deterministic_fallback_when_no_provider(self):
        factory = SimulationFactory(backend=None, provider_pool=[])
        task = _make_task()
        snapshot = _make_snapshot()
        batch = factory.generate(task, snapshot, allow_fallback=True)

        assert isinstance(batch, BidGenerationBatch)
        assert batch.generation_mode == GenerationMode.DETERMINISTIC_FALLBACK
        assert batch.degraded_reason is not None
        assert len(batch.bids) == NUM_SPECS
        for bid in batch.bids:
            assert bid.generation_mode == GenerationMode.DETERMINISTIC_FALLBACK
            assert bid.invocation_id is None

    def test_provider_backed_bids_with_mock_router(self):
        mock_backend = _make_mock_backend()
        invocations = []

        factory = SimulationFactory(
            backend=mock_backend,
            provider_pool=["anthropic"],
            on_invocation=lambda payload: invocations.append(payload),
        )
        task = _make_task()
        snapshot = _make_snapshot()
        batch = factory.generate(task, snapshot)

        assert batch.generation_mode == GenerationMode.PROVIDER_MODEL
        assert batch.degraded_reason is None
        assert len(batch.bids) == NUM_SPECS

        for bid in batch.bids:
            assert bid.generation_mode == GenerationMode.PROVIDER_MODEL
            assert bid.provider == "anthropic"
            assert bid.model_id == "claude-sonnet-4-5"
            assert bid.invocation_id is not None
            assert bid.lane is not None

        for bid in batch.bids:
            assert bid.token_usage is not None
            assert bid.token_usage.get("input_tokens", 0) > 0

        started = [inv for inv in invocations if inv["status"] == "started"]
        completed = [inv for inv in invocations if inv["status"] == "completed"]
        assert len(started) == NUM_SPECS
        assert len(completed) == NUM_SPECS

    def test_provider_backed_bids_record_invocation_ids(self):
        mock_backend = _make_mock_backend()
        invocations = []

        factory = SimulationFactory(
            backend=mock_backend,
            provider_pool=["anthropic"],
            on_invocation=lambda payload: invocations.append(payload),
        )
        task = _make_task()
        snapshot = _make_snapshot()
        batch = factory.generate(task, snapshot)

        completed_invocations = [inv for inv in invocations if inv["status"] == "completed"]
        invocation_ids = {inv["invocation_id"] for inv in completed_invocations if "invocation_id" in inv}
        assert len(invocation_ids) == NUM_SPECS

        for bid in batch.bids:
            assert bid.invocation_id in invocation_ids

    def test_partial_provider_failure_with_fallback(self):
        lock = threading.Lock()
        call_count = 0

        def flaky_invoke(lane: str, prompt: dict) -> ModelInvocationResult:
            nonlocal call_count
            with lock:
                call_count += 1
                current = call_count
            if current <= 2:
                raise ConnectionError("Provider timeout")
            return _mock_router_invoke(lane, prompt)

        mock_backend = _make_mock_backend()
        mock_backend.router.invoke = flaky_invoke

        factory = SimulationFactory(
            backend=mock_backend,
            provider_pool=["anthropic"],
        )
        task = _make_task()
        snapshot = _make_snapshot()
        batch = factory.generate(task, snapshot, allow_fallback=True)

        provider_bids = [b for b in batch.bids if b.generation_mode == GenerationMode.PROVIDER_MODEL]
        fallback_bids = [b for b in batch.bids if b.generation_mode == GenerationMode.DETERMINISTIC_FALLBACK]
        assert len(provider_bids) > 0
        assert len(fallback_bids) > 0
        assert len(batch.provider_errors) == 2

    def test_total_provider_failure_without_fallback(self):
        mock_backend = _make_mock_backend()
        mock_backend.router.invoke = MagicMock(side_effect=ConnectionError("Provider down"))

        factory = SimulationFactory(
            backend=mock_backend,
            provider_pool=["anthropic"],
        )
        task = _make_task()
        snapshot = _make_snapshot()
        batch = factory.generate(task, snapshot, allow_fallback=False)

        assert batch.generation_mode == GenerationMode.DETERMINISTIC_FALLBACK
        assert len(batch.bids) == 0
        assert len(batch.provider_errors) == NUM_SPECS

    def test_total_provider_failure_with_fallback(self):
        mock_backend = _make_mock_backend()
        mock_backend.router.invoke = MagicMock(side_effect=ConnectionError("Provider down"))

        factory = SimulationFactory(
            backend=mock_backend,
            provider_pool=["anthropic"],
        )
        task = _make_task()
        snapshot = _make_snapshot()
        batch = factory.generate(task, snapshot, allow_fallback=True)

        assert batch.generation_mode == GenerationMode.DETERMINISTIC_FALLBACK
        assert len(batch.bids) == NUM_SPECS
        for bid in batch.bids:
            assert bid.generation_mode == GenerationMode.DETERMINISTIC_FALLBACK

    def test_deterministic_bids_have_usage_unavailable_reason(self):
        factory = SimulationFactory(backend=None, provider_pool=[])
        task = _make_task()
        snapshot = _make_snapshot()
        batch = factory.generate(task, snapshot, allow_fallback=True)

        for bid in batch.bids:
            assert bid.usage_unavailable_reason is not None

    def test_provider_bids_have_usage(self):
        mock_backend = _make_mock_backend()

        factory = SimulationFactory(
            backend=mock_backend,
            provider_pool=["anthropic"],
        )
        task = _make_task()
        snapshot = _make_snapshot()
        batch = factory.generate(task, snapshot)

        for bid in batch.bids:
            assert bid.token_usage is not None
            assert bid.cost_usage is not None
            assert bid.usage_unavailable_reason is None


class TestBidArchitectureInMissionRun:
    def test_scripted_backend_sets_fallback_policy(self, python_bug_repo: Path):
        backend = ScriptedStrategyBackend([
            EditProposal(
                summary="Apply fix.",
                files=[FileUpdate(path="calc.py", content="def add(a, b):\n    return a + b\n")],
            ),
        ])
        state = start_mission(
            repo=str(python_bug_repo),
            objective="Fix failing tests",
            strategy_backend=backend,
        )

        assert state.outcome is not None
        assert state.outcome.value == "success"

        mission_root = python_bug_repo / ".arbiter" / "missions" / state.mission.mission_id
        db_path = mission_root / "state.db"
        connection = sqlite3.connect(db_path)

        bids = connection.execute("SELECT payload_json FROM bids").fetchall()
        assert len(bids) >= 1
        for (payload_json,) in bids:
            bid_data = json.loads(payload_json)
            assert "generation_mode" in bid_data

        invocation_count = connection.execute("SELECT COUNT(*) FROM model_invocations").fetchone()[0]
        assert invocation_count >= 1

        connection.close()

    def test_bid_generation_mode_persisted_in_db(self, python_bug_repo: Path):
        backend = ScriptedStrategyBackend([
            EditProposal(
                summary="Apply fix.",
                files=[FileUpdate(path="calc.py", content="def add(a, b):\n    return a + b\n")],
            ),
        ])
        state = start_mission(
            repo=str(python_bug_repo),
            objective="Fix failing tests",
            strategy_backend=backend,
        )

        mission_root = python_bug_repo / ".arbiter" / "missions" / state.mission.mission_id
        db_path = mission_root / "state.db"
        connection = sqlite3.connect(db_path)

        bids = connection.execute("SELECT payload_json FROM bids").fetchall()
        for (payload_json,) in bids:
            bid_data = json.loads(payload_json)
            mode = bid_data.get("generation_mode")
            assert mode in ("provider_model", "deterministic_fallback", "mock", "replay"), (
                f"Unexpected generation_mode: {mode}"
            )

        view_row = connection.execute("SELECT payload_json FROM mission_view_cache LIMIT 1").fetchone()
        if view_row:
            view_data = json.loads(view_row[0])
            for bid_data in view_data.get("bids", []):
                assert "generation_mode" in bid_data

        connection.close()


class TestFactoryArchetypeLaneMapping:
    def test_each_archetype_uses_its_default_lane(self):
        lanes_invoked = []

        def capturing_invoke(lane: str, prompt: dict) -> ModelInvocationResult:
            lanes_invoked.append(lane)
            return _mock_router_invoke(lane, prompt)

        mock_backend = _make_mock_backend()
        mock_backend.router.invoke = capturing_invoke

        factory = SimulationFactory(
            backend=mock_backend,
            provider_pool=["anthropic"],
        )
        task = _make_task()
        snapshot = _make_snapshot()
        factory.generate(task, snapshot)

        expected_lanes = {f"{arch.default_lane}.anthropic" for arch in ARCHETYPES}
        actual_lanes = set(lanes_invoked)
        assert expected_lanes == actual_lanes

    def test_each_variant_invoked_per_archetype(self):
        invoked_specs = []

        def capturing_invoke(lane: str, prompt: dict) -> ModelInvocationResult:
            invoked_specs.append(lane)
            return _mock_router_invoke(lane, prompt)

        mock_backend = _make_mock_backend()
        mock_backend.router.invoke = capturing_invoke

        factory = SimulationFactory(
            backend=mock_backend,
            provider_pool=["anthropic"],
        )
        task = _make_task()
        snapshot = _make_snapshot()
        batch = factory.generate(task, snapshot)

        assert len(invoked_specs) == NUM_SPECS
        assert len(batch.bids) == NUM_SPECS

        variant_kinds = {bid.mutation_kind for bid in batch.bids}
        assert variant_kinds == {"base", "narrow", "broad"}

        for arch in ARCHETYPES:
            arch_bids = [b for b in batch.bids if b.role == arch.role]
            assert len(arch_bids) == NUM_VARIANTS
            arch_variants = {b.mutation_kind for b in arch_bids}
            assert arch_variants == {"base", "narrow", "broad"}


class TestProviderMissionPlanning:
    def test_provider_planner_can_outscore_heuristic_graph(self):
        decomposer = GoalDecomposer()
        tasks = decomposer.decompose(
            "Fix failing tests and improve reliability",
            _make_snapshot(),
            strategy_backend=make_provider_backend(),
        )

        assert tasks
        assert decomposer.last_plan_source == "provider_plan"
        assert any(task.search_depth >= 3 for task in tasks)
        assert any(task.monte_carlo_samples >= 32 for task in tasks)

    def test_provider_planner_normalizes_common_task_aliases(self):
        decomposer = GoalDecomposer()
        tasks, summary = decomposer._parse_provider_plan(
            json.dumps(
                {
                    "summary": "Provider plan",
                    "tasks": [
                        {
                            "title": "Analyze the failing path",
                            "task_type": "analysis",
                            "requirement_level": "required",
                            "dependencies": [],
                            "candidate_files": ["calc.py"],
                            "validator_requirements": [],
                            "strategy_families": ["Safe"],
                            "acceptance_criteria": ["candidate files identified"],
                            "risk_level": "low",
                            "runtime_class": "bounded",
                            "search_depth": 2,
                            "monte_carlo_samples": 20,
                        },
                        {
                            "title": "Design the minimal patch",
                            "task_type": "design",
                            "requirement_level": "required",
                            "dependencies": ["Analyze the failing path"],
                            "candidate_files": ["calc.py"],
                            "validator_requirements": ["tests"],
                            "strategy_families": ["Quality"],
                            "acceptance_criteria": ["tests pass"],
                            "risk_level": "medium",
                            "runtime_class": "balanced",
                            "search_depth": 3,
                            "monte_carlo_samples": 32,
                        },
                    ],
                }
            ),
            snapshot=_make_snapshot(),
            objective="Fix failing tests and improve reliability",
        )

        assert summary == "Provider plan"
        assert [task.task_type for task in tasks[:2]] == [TaskType.LOCALIZE, TaskType.REFACTOR]
        assert tasks[0].risk_level == 0.2
        assert tasks[0].runtime_class == "small"
        assert tasks[1].runtime_class == "medium"
