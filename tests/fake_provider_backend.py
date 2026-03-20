from __future__ import annotations

import json
import re
from types import SimpleNamespace

from arbiter.agents.backend import DefaultStrategyBackend, ModelInvocationResult
from arbiter.core.contracts import BidGenerationMode, utc_now

_LANES = ("triage", "bid_fast", "bid_deep", "test_gen", "perf_reason")
_CALC_FIX = "def add(a, b):\n    return a + b\n"
_TEST_FIX = (
    "from calc import add\n\n\n"
    "def test_add():\n    assert add(2, 3) == 5\n\n\n"
    "def test_zero():\n    assert add(0, 0) == 0\n"
)


def _lane_config(provider: str, lane: str):
    return SimpleNamespace(
        name=lane,
        provider=provider,
        model_id=f"{provider}-{lane}",
        temperature=0.0,
        max_tokens=2048,
    )


def _candidate_paths(user_prompt: str) -> list[str]:
    match = re.search(r"Candidate files:\s*(.+)", user_prompt)
    if match:
        return [item.strip() for item in match.group(1).split(",") if item.strip() and item.strip() != "none identified"]
    return [item.strip() for item in re.findall(r"FILE: ([^\n]+)", user_prompt)]


class FakeProviderRouter:
    def __init__(
        self,
        *,
        providers: tuple[str, ...] = ("openai",),
        fail_bid_generation: bool = False,
        fail_proposal_generation: bool = False,
        include_usage: bool = True,
    ) -> None:
        self.fail_bid_generation = fail_bid_generation
        self.fail_proposal_generation = fail_proposal_generation
        self.include_usage = include_usage
        self.replay = SimpleNamespace(mode="off")
        model_lanes = {}
        for lane in _LANES:
            model_lanes[lane] = _lane_config(providers[0], lane)
            for provider in providers:
                model_lanes[f"{lane}.{provider}"] = _lane_config(provider, lane)
        self.config = SimpleNamespace(
            enabled_providers=list(providers),
            default_provider=providers[0],
            model_lanes=model_lanes,
        )

    def invoke(self, lane: str, prompt: dict[str, str]) -> ModelInvocationResult:
        provider = lane.split(".", 1)[1] if "." in lane else self.config.default_provider
        base_lane = lane.split(".", 1)[0]
        if "execution planner" in prompt["system"].lower():
            if self.fail_proposal_generation:
                raise RuntimeError("proposal generation unavailable")
            content = self._proposal_payload(prompt["user"])
        else:
            if self.fail_bid_generation:
                raise RuntimeError("bid generation unavailable")
            content = self._bid_payload(prompt["user"], provider, base_lane)
        token_usage = {"input_tokens": 210, "output_tokens": 84} if self.include_usage else None
        cost_usage = {"usd": 0.0123} if self.include_usage else None
        return ModelInvocationResult(
            content=content,
            provider=provider,
            model_id=f"{provider}-{base_lane}",
            lane=lane,
            generation_mode=BidGenerationMode.PROVIDER_MODEL,
            raw_usage={
                "usage_metadata": token_usage or {},
                "billing_metadata": cost_usage or {},
            },
            token_usage=token_usage,
            cost_usage=cost_usage,
            usage_unavailable_reason=(
                None
                if self.include_usage
                else "Provider response omitted token and billing metadata."
            ),
            prompt_preview=prompt["user"][:1200],
            response_preview=content[:1200],
            started_at=utc_now().isoformat(),
            completed_at=utc_now().isoformat(),
        )

    @staticmethod
    def _bid_payload(user_prompt: str, provider: str, lane: str) -> str:
        candidate_files = _candidate_paths(user_prompt) or ["calc.py"]
        return json.dumps(
            {
                "strategy_summary": f"{provider} {lane} strategy for {candidate_files[0]}",
                "exact_action": f"Update {', '.join(candidate_files)} with a provider-backed fix.",
                "utility": 0.81,
                "risk": 0.16,
                "confidence": 0.88,
                "estimated_runtime_seconds": 28,
                "touched_files": candidate_files,
            }
        )

    @staticmethod
    def _proposal_payload(user_prompt: str) -> str:
        files = []
        candidate_paths = _candidate_paths(user_prompt)
        if "calc.py" in candidate_paths:
            files.append({"path": "calc.py", "content": _CALC_FIX})
        if "tests/test_calc.py" in candidate_paths:
            files.append({"path": "tests/test_calc.py", "content": _TEST_FIX})
        if not files:
            files.append({"path": "calc.py", "content": _CALC_FIX})
        return json.dumps(
            {
                "summary": "Apply provider-backed calculator fixes.",
                "files": files,
                "notes": ["provider_generated"],
            }
        )


def make_provider_backend(
    *,
    providers: tuple[str, ...] = ("openai",),
    fail_bid_generation: bool = False,
    fail_proposal_generation: bool = False,
    include_usage: bool = True,
):
    return DefaultStrategyBackend(
        FakeProviderRouter(
            providers=providers,
            fail_bid_generation=fail_bid_generation,
            fail_proposal_generation=fail_proposal_generation,
            include_usage=include_usage,
        )
    )
