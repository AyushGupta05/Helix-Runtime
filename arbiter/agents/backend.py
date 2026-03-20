from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from arbiter.core.contracts import Bid, BidGenerationMode, ReplayRecord, TaskNode, utc_now
from arbiter.runtime.config import RuntimeConfig
from arbiter.runtime.replay import ReplayManager


class FileUpdate(BaseModel):
    path: str
    content: str


class EditProposal(BaseModel):
    summary: str
    files: list[FileUpdate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ModelInvocationResult(BaseModel):
    content: str
    invocation_id: str | None = None
    provider: str | None = None
    model_id: str | None = None
    lane: str | None = None
    generation_mode: BidGenerationMode = BidGenerationMode.PROVIDER_MODEL
    raw_usage: dict[str, Any] = Field(default_factory=dict)
    token_usage: dict[str, int] | None = None
    cost_usage: dict[str, float] | None = None
    usage_unavailable_reason: str | None = None
    prompt_preview: str | None = None
    response_preview: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    status: str = "completed"
    error: str | None = None


class ProposalCandidate(BaseModel):
    candidate_id: str
    task_id: str
    bid_id: str
    provider: str
    lane: str
    model_id: str | None = None
    proposal: EditProposal
    invocation: ModelInvocationResult
    score: float = 0.0
    selected: bool = False
    rejection_reason: str | None = None


def _preview_text(text: str, limit: int = 1200) -> str:
    cleaned = text.strip()
    return cleaned[:limit]


def _normalize_usage_metadata(usage: dict[str, Any] | None) -> dict[str, int]:
    normalized: dict[str, int] = {}
    if not usage:
        return normalized
    for key, value in usage.items():
        if isinstance(value, bool):
            normalized[key] = int(value)
        elif isinstance(value, int):
            normalized[key] = value
        elif isinstance(value, float):
            normalized[key] = int(value)
        elif isinstance(value, dict):
            for nested_key, nested_value in value.items():
                if isinstance(nested_value, (int, float)) and not isinstance(nested_value, bool):
                    normalized[f"{key}.{nested_key}"] = int(nested_value)
        else:
            continue
    return normalized


def _extract_cost_usage(value: Any, prefix: str = "") -> dict[str, float]:
    normalized: dict[str, float] = {}
    if isinstance(value, dict):
        for key, nested in value.items():
            nested_prefix = f"{prefix}.{key}" if prefix else key
            lowered = key.lower()
            if isinstance(nested, (int, float)) and not isinstance(nested, bool):
                if any(token in lowered for token in ("cost", "price", "billing", "usd", "amount")):
                    normalized[nested_prefix] = float(nested)
            else:
                normalized.update(_extract_cost_usage(nested, nested_prefix))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            nested_prefix = f"{prefix}.{index}" if prefix else str(index)
            normalized.update(_extract_cost_usage(item, nested_prefix))
    return normalized


def _usage_reason(token_usage: dict[str, int] | None, cost_usage: dict[str, float] | None, generation_mode: BidGenerationMode) -> str | None:
    if generation_mode == BidGenerationMode.REPLAY:
        return None
    reasons: list[str] = []
    if token_usage is None:
        reasons.append("token usage unavailable: provider response did not include token metadata")
    if cost_usage is None:
        reasons.append("cost usage unavailable: provider response did not include billing metadata")
    return "; ".join(reasons) or None


class BedrockModelRouter:
    def __init__(self, config: RuntimeConfig, replay: ReplayManager) -> None:
        self.config = config
        self.replay = replay
        self._models: dict[str, Any] = {}

    def _resolve_lane(self, lane: str) -> tuple[str, str]:
        if lane in self.config.model_lanes:
            if "." in lane:
                base_lane, provider = lane.split(".", 1)
                return base_lane, provider
            return lane, self.config.default_provider
        if "." in lane:
            base_lane, provider = lane.split(".", 1)
            return base_lane, provider
        return lane, self.config.default_provider

    def _get_model(self, lane: str):
        base_lane, provider = self._resolve_lane(lane)
        lane_key = f"{base_lane}.{provider}"
        lane_config = self.config.model_lanes.get(lane_key) or self.config.model_lanes[base_lane]
        if lane_key not in self._models:
            if lane_config.provider == "bedrock":
                if self.config.bedrock_profile:
                    os.environ["AWS_PROFILE"] = self.config.bedrock_profile
                from langchain_aws import ChatBedrockConverse

                kwargs = {"model_id": lane_config.model_id, "region_name": self.config.bedrock_region, "temperature": lane_config.temperature}
                if self.config.bedrock_access_key_id:
                    kwargs["aws_access_key_id"] = self.config.bedrock_access_key_id
                if self.config.bedrock_secret_access_key:
                    kwargs["aws_secret_access_key"] = self.config.bedrock_secret_access_key
                if self.config.bedrock_session_token:
                    kwargs["aws_session_token"] = self.config.bedrock_session_token
                self._models[lane_key] = ChatBedrockConverse(**kwargs)
            elif lane_config.provider == "openai":
                if not self.config.openai_api_key:
                    raise ValueError("OPENAI_API_KEY is required when MODEL_PROVIDER=openai.")
                from langchain_openai import ChatOpenAI

                self._models[lane_key] = ChatOpenAI(
                    model=lane_config.model_id,
                    temperature=lane_config.temperature,
                    api_key=self.config.openai_api_key,
                )
            elif lane_config.provider == "anthropic":
                if not self.config.anthropic_api_key:
                    raise ValueError("ANTHROPIC_API_KEY is required when MODEL_PROVIDER=anthropic.")
                from langchain_anthropic import ChatAnthropic

                self._models[lane_key] = ChatAnthropic(
                    model=lane_config.model_id,
                    temperature=lane_config.temperature,
                    api_key=self.config.anthropic_api_key,
                )
            else:
                raise ValueError(f"Unsupported provider: {lane_config.provider}")
        return self._models[lane_key], lane_config

    def invoke(self, lane: str, prompt: dict[str, Any]) -> ModelInvocationResult:
        if self.replay.mode == "replay":
            recorded = self.replay.load(prompt)
            if recorded:
                return ModelInvocationResult.model_validate({**recorded, "generation_mode": BidGenerationMode.REPLAY})
            raise RuntimeError("Replay mode is active but no recorded response exists for this prompt.")
        started_at = utc_now().isoformat()
        model, lane_config = self._get_model(lane)
        messages = [
            ("system", prompt["system"]),
            ("human", prompt["user"]),
        ]
        response = model.invoke(messages)
        content = response.content if isinstance(response.content, str) else json.dumps(response.content)
        raw_usage = getattr(response, "usage_metadata", {}) or {}
        response_metadata = getattr(response, "response_metadata", {}) or {}
        if response_metadata:
            raw_usage = {
                "usage_metadata": raw_usage,
                "response_metadata": response_metadata,
            }
        token_usage = _normalize_usage_metadata(raw_usage) or None
        cost_usage = _extract_cost_usage(raw_usage) or None
        result = ModelInvocationResult(
            content=content,
            provider=lane_config.provider,
            model_id=lane_config.model_id,
            lane=lane,
            generation_mode=BidGenerationMode.PROVIDER_MODEL,
            raw_usage=raw_usage,
            token_usage=token_usage,
            cost_usage=cost_usage,
            usage_unavailable_reason=_usage_reason(token_usage, cost_usage, BidGenerationMode.PROVIDER_MODEL),
            prompt_preview=_preview_text(prompt["user"]),
            response_preview=_preview_text(content),
            started_at=started_at,
            completed_at=utc_now().isoformat(),
        )
        if self.replay.mode in {"record", "off"}:
            self.replay.record(lane=lane, prompt=prompt, response=result.model_dump(mode="json"))
        return result


class DefaultStrategyBackend:
    def __init__(self, router: BedrockModelRouter) -> None:
        self.router = router

    def market_generation_mode(self) -> BidGenerationMode:
        if self.router.replay.mode == "replay":
            return BidGenerationMode.REPLAY
        return BidGenerationMode.PROVIDER_MODEL

    def supports_provider_bid_generation(self) -> bool:
        return True

    @staticmethod
    def lane_for_task(task: TaskNode) -> str:
        if task.task_type.value == "test":
            return "test_gen"
        if "perf" in task.task_type.value:
            return "perf_reason"
        return "bid_deep"

    def generate_edit_proposals(
        self,
        task: TaskNode,
        bid: Bid,
        mission_objective: str,
        candidate_files: dict[str, str],
        failure_context: str | None = None,
        preview: bool = False,
        providers: list[str] | None = None,
        on_invocation=None,
    ) -> list[ProposalCandidate]:
        del preview
        if not candidate_files:
            fallback = ModelInvocationResult(
                content="{}",
                generation_mode=BidGenerationMode.DETERMINISTIC_FALLBACK,
                token_usage=None,
                cost_usage=None,
                usage_unavailable_reason="No provider call was made because there were no candidate files to plan against.",
                prompt_preview="",
                response_preview="",
            )
            return [
                ProposalCandidate(
                    candidate_id=uuid4().hex,
                    task_id=task.task_id,
                    bid_id=bid.bid_id,
                    provider="system",
                    lane="none",
                    proposal=EditProposal(summary="No candidate files available.", files=[], notes=["no_candidate_files"]),
                    invocation=fallback,
                    rejection_reason="no_candidate_files",
                )
            ]
        lane = self.lane_for_task(task)
        system = (
            "You are Arbiter's execution planner. Return only valid JSON with fields: summary, files, notes. "
            "Each file entry must contain path and full replacement content. Keep edits minimal and respect the bid strategy."
        )
        file_blob = "\n\n".join(
            f"FILE: {path}\n```\n{content[:16000]}\n```"
            for path, content in candidate_files.items()
        )
        user = (
            f"Objective: {mission_objective}\n"
            f"Task: {task.title}\n"
            f"Strategy: {bid.strategy_summary}\n"
            f"Exact action: {bid.exact_action}\n"
            f"Failure context: {failure_context or 'none'}\n"
            f"Candidate files:\n{file_blob}\n"
            "Return JSON only."
        )
        provider_pool = providers or self.router.config.enabled_providers

        def run_provider(provider: str) -> ProposalCandidate | None:
            started_at = utc_now().isoformat()
            lane_key = f"{lane}.{provider}"
            invocation_id = uuid4().hex
            lane_config = self.router.config.model_lanes.get(lane_key) or self.router.config.model_lanes[lane]
            if on_invocation:
                on_invocation(
                    {
                        "invocation_id": invocation_id,
                        "provider": provider,
                        "lane": lane_key,
                        "model_id": lane_config.model_id,
                        "invocation_kind": "proposal_generation",
                        "generation_mode": self.market_generation_mode(),
                        "status": "started",
                        "task_id": task.task_id,
                        "bid_id": bid.bid_id,
                        "started_at": started_at,
                        "prompt_preview": _preview_text(user),
                    }
                )
            try:
                result = self.router.invoke(lane=lane_key, prompt={"system": system, "user": user})
                proposal = self._parse_edit_proposal(result.content)
                result.invocation_id = invocation_id
                if on_invocation:
                    on_invocation(
                        {
                            "invocation_id": invocation_id,
                            "provider": result.provider or provider,
                            "lane": result.lane or lane_key,
                            "model_id": result.model_id,
                            "invocation_kind": "proposal_generation",
                            "generation_mode": result.generation_mode,
                            "status": "completed",
                            "task_id": task.task_id,
                            "bid_id": bid.bid_id,
                            "started_at": result.started_at or started_at,
                            "completed_at": result.completed_at,
                            "prompt_preview": result.prompt_preview,
                            "response_preview": result.response_preview,
                            "raw_usage": result.raw_usage,
                            "token_usage": result.token_usage,
                            "cost_usage": result.cost_usage,
                            "usage_unavailable_reason": result.usage_unavailable_reason,
                        }
                    )
                return ProposalCandidate(
                    candidate_id=uuid4().hex,
                    task_id=task.task_id,
                    bid_id=bid.bid_id,
                    provider=result.provider or provider,
                    lane=result.lane or lane_key,
                    model_id=result.model_id,
                    proposal=proposal,
                    invocation=result,
                )
            except Exception as exc:
                if on_invocation:
                    on_invocation(
                        {
                            "invocation_id": invocation_id,
                            "provider": provider,
                            "lane": lane_key,
                            "model_id": lane_config.model_id,
                            "invocation_kind": "proposal_generation",
                            "generation_mode": self.market_generation_mode(),
                            "status": "failed",
                            "task_id": task.task_id,
                            "bid_id": bid.bid_id,
                            "started_at": started_at,
                            "completed_at": utc_now().isoformat(),
                            "prompt_preview": _preview_text(user),
                            "error": str(exc),
                        }
                    )
                return None

        with ThreadPoolExecutor(max_workers=max(1, len(provider_pool))) as executor:
            candidates = [future.result() for future in [executor.submit(run_provider, provider) for provider in provider_pool]]
        return [candidate for candidate in candidates if candidate is not None]

    def generate_edit_proposal(
        self,
        task: TaskNode,
        bid: Bid,
        mission_objective: str,
        candidate_files: dict[str, str],
        failure_context: str | None = None,
        preview: bool = False,
    ) -> tuple[EditProposal, ModelInvocationResult]:
        candidates = self.generate_edit_proposals(
            task=task,
            bid=bid,
            mission_objective=mission_objective,
            candidate_files=candidate_files,
            failure_context=failure_context,
            preview=preview,
            providers=[self.router.config.default_provider],
        )
        candidate = candidates[0]
        return candidate.proposal, candidate.invocation

    @staticmethod
    def _parse_edit_proposal(text: str) -> EditProposal:
        cleaned = text.strip()
        if "```" in cleaned:
            cleaned = cleaned.split("```")[-2].replace("json", "").strip()
        data = json.loads(cleaned)
        return EditProposal.model_validate(data)


class ScriptedStrategyBackend(DefaultStrategyBackend):
    def __init__(self, scripted: list[EditProposal]) -> None:
        self.scripted = scripted
        self.index = 0

    def market_generation_mode(self) -> BidGenerationMode:
        return BidGenerationMode.MOCK

    def supports_provider_bid_generation(self) -> bool:
        return False

    def generate_edit_proposal(
        self,
        task: TaskNode,
        bid: Bid,
        mission_objective: str,
        candidate_files: dict[str, str],
        failure_context: str | None = None,
        preview: bool = False,
    ) -> tuple[EditProposal, ModelInvocationResult]:
        proposal = self.scripted[self.index]
        if not preview:
            self.index = min(self.index + 1, len(self.scripted) - 1)
        return proposal, ModelInvocationResult(
            content=proposal.model_dump_json(),
            provider="scripted",
            model_id="scripted",
            lane="scripted",
            generation_mode=BidGenerationMode.MOCK,
            token_usage=None,
            cost_usage=None,
            usage_unavailable_reason="Mock strategy backend generated this proposal without a provider call.",
            prompt_preview="",
            response_preview=proposal.summary,
        )

    def generate_edit_proposals(
        self,
        task: TaskNode,
        bid: Bid,
        mission_objective: str,
        candidate_files: dict[str, str],
        failure_context: str | None = None,
        preview: bool = False,
        providers: list[str] | None = None,
        on_invocation=None,
    ) -> list[ProposalCandidate]:
        del mission_objective, candidate_files, failure_context, providers
        proposal, invocation = self.generate_edit_proposal(task, bid, "scripted", {}, preview=preview)
        if on_invocation:
            invocation_id = uuid4().hex
            invocation.invocation_id = invocation_id
            on_invocation(
                {
                    "invocation_id": invocation_id,
                    "provider": "scripted",
                    "lane": "scripted",
                    "model_id": "scripted",
                    "invocation_kind": "proposal_generation",
                    "generation_mode": BidGenerationMode.MOCK,
                    "status": "completed",
                    "task_id": task.task_id,
                    "bid_id": bid.bid_id,
                    "started_at": utc_now().isoformat(),
                    "completed_at": utc_now().isoformat(),
                    "prompt_preview": "",
                    "response_preview": proposal.summary,
                    "raw_usage": {},
                    "token_usage": invocation.token_usage,
                    "cost_usage": invocation.cost_usage,
                    "usage_unavailable_reason": invocation.usage_unavailable_reason,
                }
            )
        return [
            ProposalCandidate(
                candidate_id=uuid4().hex,
                task_id=task.task_id,
                bid_id=bid.bid_id,
                provider="scripted",
                lane="scripted",
                model_id="scripted",
                proposal=proposal,
                invocation=invocation,
            )
        ]


def load_candidate_files(repo_path: str, files: list[str]) -> dict[str, str]:
    repo = Path(repo_path)
    loaded: dict[str, str] = {}
    for relative in files:
        path = repo / relative
        if path.exists() and path.is_file():
            loaded[relative] = path.read_text(encoding="utf-8", errors="ignore")
    return loaded
