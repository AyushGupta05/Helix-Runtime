from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from arbiter.core.contracts import Bid, BidGenerationMode, ReplayRecord, TaskNode, utc_now
from arbiter.runtime.config import RuntimeConfig
from arbiter.runtime.model_payloads import extract_edit_payload
from arbiter.runtime.replay import ReplayManager


class FileUpdate(BaseModel):
    path: str
    content: str


class EditOperation(BaseModel):
    type: Literal["replace", "insert_after", "insert_before", "append", "prepend", "create_file"]
    path: str
    content: str
    target: str | None = None
    occurrence: int = 1


class EditProposal(BaseModel):
    summary: str
    files: list[FileUpdate] = Field(default_factory=list)
    operations: list[EditOperation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.files or self.operations)

    @property
    def affected_paths(self) -> list[str]:
        ordered: list[str] = []
        for path in [item.path for item in self.files] + [item.path for item in self.operations]:
            if path not in ordered:
                ordered.append(path)
        return ordered


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


@dataclass(frozen=True)
class ModelPriceCard:
    input_per_mtok: float
    cached_input_per_mtok: float
    output_per_mtok: float
    cache_write_5m_per_mtok: float | None = None
    cache_write_1h_per_mtok: float | None = None


_OPENAI_PRICE_CARDS: tuple[tuple[str, ModelPriceCard], ...] = (
    ("gpt-5.4-mini", ModelPriceCard(input_per_mtok=0.75, cached_input_per_mtok=0.075, output_per_mtok=4.5)),
    ("gpt-5.4", ModelPriceCard(input_per_mtok=2.5, cached_input_per_mtok=0.25, output_per_mtok=15.0)),
    ("gpt-5.2-codex", ModelPriceCard(input_per_mtok=1.75, cached_input_per_mtok=0.175, output_per_mtok=14.0)),
    ("gpt-5.2", ModelPriceCard(input_per_mtok=1.75, cached_input_per_mtok=0.175, output_per_mtok=14.0)),
    ("gpt-5.1-codex-mini", ModelPriceCard(input_per_mtok=0.25, cached_input_per_mtok=0.025, output_per_mtok=2.0)),
    ("gpt-5.1-codex-max", ModelPriceCard(input_per_mtok=1.25, cached_input_per_mtok=0.125, output_per_mtok=10.0)),
    ("gpt-5.1-codex", ModelPriceCard(input_per_mtok=1.25, cached_input_per_mtok=0.125, output_per_mtok=10.0)),
    ("gpt-5-mini", ModelPriceCard(input_per_mtok=0.25, cached_input_per_mtok=0.025, output_per_mtok=2.0)),
    ("gpt-5.1", ModelPriceCard(input_per_mtok=1.25, cached_input_per_mtok=0.125, output_per_mtok=10.0)),
    ("gpt-5", ModelPriceCard(input_per_mtok=1.25, cached_input_per_mtok=0.125, output_per_mtok=10.0)),
)

_ANTHROPIC_PRICE_CARDS: tuple[tuple[str, ModelPriceCard], ...] = (
    (
        "claude-sonnet-4",
        ModelPriceCard(
            input_per_mtok=3.0,
            cached_input_per_mtok=0.3,
            output_per_mtok=15.0,
            cache_write_5m_per_mtok=3.75,
            cache_write_1h_per_mtok=6.0,
        ),
    ),
)


def _preview_text(text: str, limit: int = 1200) -> str:
    cleaned = text.strip()
    return cleaned[:limit]


def _nested_mapping(payload: dict[str, Any] | None, *path: str) -> dict[str, Any]:
    current: Any = payload or {}
    for key in path:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _nested_number(payload: dict[str, Any] | None, *path: str) -> int | float | None:
    current: Any = payload or {}
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    if isinstance(current, (int, float)) and not isinstance(current, bool):
        return current
    return None


def _set_if_positive(target: dict[str, int], key: str, value: int) -> None:
    if value > 0:
        target[key] = value


def _normalize_usage_metadata(usage: dict[str, Any] | None) -> dict[str, int]:
    if not usage:
        return {}

    openai_usage = _nested_mapping(usage, "response_metadata", "token_usage")
    if openai_usage:
        input_tokens = int(
            _nested_number(usage, "response_metadata", "token_usage", "prompt_tokens")
            or _nested_number(usage, "usage_metadata", "input_tokens")
            or 0
        )
        cached_input_tokens = int(
            _nested_number(usage, "response_metadata", "token_usage", "prompt_tokens_details", "cached_tokens")
            or _nested_number(usage, "usage_metadata", "input_token_details", "cache_read")
            or 0
        )
        output_tokens = int(
            _nested_number(usage, "response_metadata", "token_usage", "completion_tokens")
            or _nested_number(usage, "usage_metadata", "output_tokens")
            or 0
        )
        total_tokens = int(
            _nested_number(usage, "response_metadata", "token_usage", "total_tokens")
            or _nested_number(usage, "usage_metadata", "total_tokens")
            or (input_tokens + output_tokens)
        )
        reasoning_tokens = int(
            _nested_number(usage, "response_metadata", "token_usage", "completion_tokens_details", "reasoning_tokens")
            or _nested_number(usage, "usage_metadata", "output_token_details", "reasoning")
            or 0
        )
        normalized: dict[str, int] = {}
        _set_if_positive(normalized, "input_tokens", input_tokens)
        _set_if_positive(normalized, "cached_input_tokens", cached_input_tokens)
        _set_if_positive(normalized, "output_tokens", output_tokens)
        _set_if_positive(normalized, "reasoning_tokens", reasoning_tokens)
        _set_if_positive(normalized, "total_tokens", total_tokens)
        return normalized

    anthropic_usage = _nested_mapping(usage, "response_metadata", "usage")
    usage_metadata = _nested_mapping(usage, "usage_metadata")
    if anthropic_usage or usage_metadata:
        input_tokens = int(
            _nested_number(usage, "response_metadata", "usage", "input_tokens")
            or _nested_number(usage, "usage_metadata", "input_tokens")
            or 0
        )
        output_tokens = int(
            _nested_number(usage, "response_metadata", "usage", "output_tokens")
            or _nested_number(usage, "usage_metadata", "output_tokens")
            or 0
        )
        cache_read_input_tokens = int(
            _nested_number(usage, "response_metadata", "usage", "cache_read_input_tokens")
            or _nested_number(usage, "usage_metadata", "input_token_details", "cache_read")
            or 0
        )
        cache_creation_input_tokens = int(
            _nested_number(usage, "response_metadata", "usage", "cache_creation_input_tokens")
            or _nested_number(usage, "usage_metadata", "input_token_details", "cache_creation")
            or 0
        )
        cache_write_5m_input_tokens = int(
            _nested_number(usage, "response_metadata", "usage", "cache_creation", "ephemeral_5m_input_tokens")
            or 0
        )
        cache_write_1h_input_tokens = int(
            _nested_number(usage, "response_metadata", "usage", "cache_creation", "ephemeral_1h_input_tokens")
            or 0
        )
        if cache_creation_input_tokens > 0 and (cache_write_5m_input_tokens + cache_write_1h_input_tokens == 0):
            # Anthropic's default prompt cache duration is 5 minutes when the breakdown is omitted.
            cache_write_5m_input_tokens = cache_creation_input_tokens
        total_tokens = _nested_number(usage, "usage_metadata", "total_tokens")
        if total_tokens is None:
            total_tokens = input_tokens + output_tokens + cache_read_input_tokens + cache_creation_input_tokens
        normalized = {}
        _set_if_positive(normalized, "input_tokens", input_tokens)
        _set_if_positive(normalized, "output_tokens", output_tokens)
        _set_if_positive(normalized, "cache_read_input_tokens", cache_read_input_tokens)
        _set_if_positive(normalized, "cache_creation_input_tokens", cache_creation_input_tokens)
        _set_if_positive(normalized, "cache_write_5m_input_tokens", cache_write_5m_input_tokens)
        _set_if_positive(normalized, "cache_write_1h_input_tokens", cache_write_1h_input_tokens)
        _set_if_positive(normalized, "total_tokens", int(total_tokens))
        return normalized

    normalized: dict[str, int] = {}
    for key, value in usage.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            normalized[key] = int(value)
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


def _price_card_for(provider: str | None, model_id: str | None, raw_usage: dict[str, Any] | None) -> ModelPriceCard | None:
    response_metadata = _nested_mapping(raw_usage, "response_metadata")
    candidates: list[str] = []
    for candidate in (
        model_id,
        response_metadata.get("model_name"),
        response_metadata.get("model"),
    ):
        if isinstance(candidate, str):
            lowered = candidate.lower()
            if lowered not in candidates:
                candidates.append(lowered)
    if (provider or "").lower() == "anthropic":
        for candidate in candidates:
            for prefix, card in _ANTHROPIC_PRICE_CARDS:
                if candidate.startswith(prefix):
                    return card
        return None
    for candidate in candidates:
        for prefix, card in _OPENAI_PRICE_CARDS:
            if candidate.startswith(prefix):
                return card
    return None


def _estimate_cost_usage(
    *,
    raw_usage: dict[str, Any] | None,
    token_usage: dict[str, int] | None,
    provider: str | None,
    model_id: str | None,
) -> dict[str, float] | None:
    if not token_usage:
        return None
    card = _price_card_for(provider, model_id, raw_usage)
    if card is None:
        return None

    provider_name = (provider or "").lower()
    total_cost = 0.0

    if provider_name == "anthropic":
        input_tokens = max(int(token_usage.get("input_tokens", 0)), 0)
        output_tokens = max(int(token_usage.get("output_tokens", 0)), 0)
        cache_read_input_tokens = max(int(token_usage.get("cache_read_input_tokens", 0)), 0)
        cache_write_5m_input_tokens = max(int(token_usage.get("cache_write_5m_input_tokens", 0)), 0)
        cache_write_1h_input_tokens = max(int(token_usage.get("cache_write_1h_input_tokens", 0)), 0)
        cache_creation_input_tokens = max(int(token_usage.get("cache_creation_input_tokens", 0)), 0)
        if cache_creation_input_tokens > 0 and (cache_write_5m_input_tokens + cache_write_1h_input_tokens == 0):
            cache_write_5m_input_tokens = cache_creation_input_tokens
        total_cost += input_tokens * card.input_per_mtok / 1_000_000
        total_cost += cache_read_input_tokens * card.cached_input_per_mtok / 1_000_000
        total_cost += output_tokens * card.output_per_mtok / 1_000_000
        total_cost += cache_write_5m_input_tokens * (card.cache_write_5m_per_mtok or card.input_per_mtok) / 1_000_000
        total_cost += cache_write_1h_input_tokens * (card.cache_write_1h_per_mtok or card.input_per_mtok) / 1_000_000
    else:
        input_tokens = max(int(token_usage.get("input_tokens", 0)), 0)
        cached_input_tokens = max(int(token_usage.get("cached_input_tokens", 0)), 0)
        output_tokens = max(int(token_usage.get("output_tokens", 0)), 0)
        uncached_input_tokens = max(input_tokens - cached_input_tokens, 0)
        total_cost += uncached_input_tokens * card.input_per_mtok / 1_000_000
        total_cost += cached_input_tokens * card.cached_input_per_mtok / 1_000_000
        total_cost += output_tokens * card.output_per_mtok / 1_000_000

    if total_cost <= 0:
        return None
    return {"usd": round(total_cost, 8)}


def _usage_reason(token_usage: dict[str, int] | None, cost_usage: dict[str, float] | None, generation_mode: BidGenerationMode) -> str | None:
    if generation_mode == BidGenerationMode.REPLAY:
        return None
    reasons: list[str] = []
    if token_usage is None:
        reasons.append("token usage unavailable: provider response did not include token metadata")
    if cost_usage is None:
        reasons.append("cost usage unavailable: provider response did not include billing metadata and no pricing estimate was available")
    return "; ".join(reasons) or None


class ProviderModelRouter:
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

    def _get_model(self, lane: str, request_timeout_seconds: float | None = None):
        base_lane, provider = self._resolve_lane(lane)
        lane_key = f"{base_lane}.{provider}"
        lane_config = self.config.model_lanes.get(lane_key) or self.config.model_lanes[base_lane]
        timeout_seconds = (
            float(request_timeout_seconds)
            if request_timeout_seconds is not None
            else float(self.config.provider_request_timeout_seconds)
        )
        cache_key = f"{lane_key}@{timeout_seconds}"
        if cache_key not in self._models:
            if lane_config.provider == "openai":
                if not self.config.openai_api_key:
                    raise ValueError("OPENAI_API_KEY is required when MODEL_PROVIDER=openai.")
                from langchain_openai import ChatOpenAI

                self._models[cache_key] = ChatOpenAI(
                    model=lane_config.model_id,
                    temperature=lane_config.temperature,
                    api_key=self.config.openai_api_key,
                    max_tokens=lane_config.max_tokens,
                    request_timeout=timeout_seconds,
                )
            elif lane_config.provider == "anthropic":
                if not self.config.anthropic_api_key:
                    raise ValueError("ANTHROPIC_API_KEY is required when MODEL_PROVIDER=anthropic.")
                from langchain_anthropic import ChatAnthropic

                self._models[cache_key] = ChatAnthropic(
                    model=lane_config.model_id,
                    temperature=lane_config.temperature,
                    api_key=self.config.anthropic_api_key,
                    max_tokens=lane_config.max_tokens,
                    default_request_timeout=timeout_seconds,
                )
            else:
                raise ValueError(f"Unsupported provider: {lane_config.provider}")
        return self._models[cache_key], lane_config

    def invoke(
        self,
        lane: str,
        prompt: dict[str, Any],
        *,
        request_timeout_seconds: float | None = None,
    ) -> ModelInvocationResult:
        if self.replay.mode == "replay":
            recorded = self.replay.load(prompt)
            if recorded:
                return ModelInvocationResult.model_validate({**recorded, "generation_mode": BidGenerationMode.REPLAY})
            raise RuntimeError("Replay mode is active but no recorded response exists for this prompt.")
        started_at = utc_now().isoformat()
        model, lane_config = self._get_model(lane, request_timeout_seconds=request_timeout_seconds)
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
        if cost_usage is None:
            cost_usage = _estimate_cost_usage(
                raw_usage=raw_usage,
                token_usage=token_usage,
                provider=lane_config.provider,
                model_id=lane_config.model_id,
            )
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
    def __init__(self, router: ProviderModelRouter) -> None:
        self.router = router

    def market_generation_mode(self) -> BidGenerationMode:
        if self.router.replay.mode == "replay":
            return BidGenerationMode.REPLAY
        return BidGenerationMode.PROVIDER_MODEL

    def supports_provider_bid_generation(self) -> bool:
        return True

    @staticmethod
    def lane_for_task(task: TaskNode) -> str:
        if task.task_type.value == "validate":
            return "test_gen"
        if task.task_type.value == "perf_diagnosis":
            return "perf_reason"
        return "proposal_gen"

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
        request_timeout = (
            getattr(self.router.config, "preview_request_timeout_seconds", None)
            if preview
            else getattr(self.router.config, "proposal_request_timeout_seconds", None)
        )
        preferred_files = [path for path in (bid.touched_files or []) if path in candidate_files]
        remaining_files = [path for path in candidate_files if path not in preferred_files]
        ordered_files = preferred_files + remaining_files
        if preview:
            file_limit = 2
        else:
            file_limit = max(1, min(len(preferred_files) if preferred_files else len(ordered_files), 3))
        char_limit = 6000 if preview else 12000
        scoped_files = ordered_files[: max(1, file_limit)]
        scoped_candidate_files = {path: candidate_files[path] for path in scoped_files}
        system = (
            "You are Arbiter's execution planner. Return only valid JSON with fields: summary, operations, files, notes. "
            "Prefer compact operations for existing files. Each operation must contain type, path, and content, and "
            "replace/insert_before/insert_after operations must also include target. Supported operation types are: "
            "replace, insert_after, insert_before, append, prepend, create_file. "
            "Use files only when a full replacement is genuinely required. Keep edits minimal and respect the bid strategy. "
            "Do not include markdown fences or analysis."
        )
        if preview:
            system += (
                " This is simulation preview mode. Prefer the smallest viable patch, touch as few files as possible, "
                "and return quickly if the change cannot be expressed cleanly."
            )
        file_blob = "\n\n".join(
            f"FILE: {path}\n```\n{content[:char_limit]}\n```"
            for path, content in scoped_candidate_files.items()
        )
        user = (
            f"Objective: {mission_objective}\n"
            f"Task: {task.title}\n"
            f"Task type: {task.task_type.value}\n"
            f"Strategy: {bid.strategy_summary}\n"
            f"Exact action: {bid.exact_action}\n"
            f"Failure context: {failure_context or 'none'}\n"
            f"Candidate files:\n{file_blob}\n"
            "This is an executable bounded work unit chosen by Arbiter. "
            "Do not change the task type or return analysis-only output. "
            "For bugfix, test, refactor, and perf_optimize tasks, return at least one operation or file update. "
            "For existing files, prefer minimal operations over full replacement content. "
            "Return JSON only."
        )
        if preview:
            user += "\nPreview goal: prove the bid is viable with a minimal bounded patch, not a broad rewrite."
        provider_pool = providers or self.router.config.enabled_providers

        def run_provider(provider: str) -> ProposalCandidate | None:
            started_at = utc_now().isoformat()
            lane_key = f"{lane}.{provider}"
            invocation_id = uuid4().hex
            lane_config = self.router.config.model_lanes.get(lane_key) or self.router.config.model_lanes[lane]
            result: ModelInvocationResult | None = None
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
                result = self.router.invoke(
                    lane=lane_key,
                    prompt={"system": system, "user": user},
                    request_timeout_seconds=request_timeout,
                )
                proposal = self._parse_edit_proposal(result.content)
                if task.task_type.value not in {"localize", "perf_diagnosis", "validate"} and not proposal.has_changes:
                    raise ValueError("Provider returned no executable edits for this task.")
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
                    completed_at = utc_now().isoformat()
                    on_invocation(
                        {
                            "invocation_id": invocation_id,
                            "provider": result.provider if result is not None else provider,
                            "lane": result.lane if result is not None else lane_key,
                            "model_id": result.model_id if result is not None else lane_config.model_id,
                            "invocation_kind": "proposal_generation",
                            "generation_mode": result.generation_mode if result is not None else self.market_generation_mode(),
                            "status": "failed",
                            "task_id": task.task_id,
                            "bid_id": bid.bid_id,
                            "started_at": result.started_at if result is not None else started_at,
                            "completed_at": result.completed_at if result is not None else completed_at,
                            "prompt_preview": result.prompt_preview if result is not None else _preview_text(user),
                            "response_preview": result.response_preview if result is not None else None,
                            "raw_usage": result.raw_usage if result is not None else {},
                            "token_usage": result.token_usage if result is not None else None,
                            "cost_usage": result.cost_usage if result is not None else None,
                            "usage_unavailable_reason": result.usage_unavailable_reason if result is not None else None,
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
        on_invocation=None,
    ) -> tuple[EditProposal, ModelInvocationResult]:
        provider_pool = [bid.provider] if bid.provider else [self.router.config.default_provider]
        candidates = self.generate_edit_proposals(
            task=task,
            bid=bid,
            mission_objective=mission_objective,
            candidate_files=candidate_files,
            failure_context=failure_context,
            preview=preview,
            providers=provider_pool,
            on_invocation=on_invocation,
        )
        if candidates:
            candidate = candidates[0]
            return candidate.proposal, candidate.invocation
        fallback_provider = provider_pool[0] if provider_pool else "system"
        fallback_invocation = ModelInvocationResult(
            content="{}",
            provider=fallback_provider,
            model_id=bid.model_id,
            lane=bid.lane or f"preview.{fallback_provider}",
            generation_mode=self.market_generation_mode(),
            token_usage=None,
            cost_usage=None,
            usage_unavailable_reason="No provider proposal was generated for this bid.",
            prompt_preview="",
            response_preview="",
            status="failed",
            error="Provider proposal generation produced no viable candidate.",
        )
        return (
            EditProposal(summary="No provider proposal available.", files=[], notes=["provider_generation_failed"]),
            fallback_invocation,
        )

    @staticmethod
    def _parse_edit_proposal(text: str) -> EditProposal:
        data = extract_edit_payload(text)
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
        on_invocation=None,
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
