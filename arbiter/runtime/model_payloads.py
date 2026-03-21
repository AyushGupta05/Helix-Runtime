from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


PayloadPredicate = Callable[[dict[str, Any]], bool]


def extract_edit_payload(text: str) -> dict[str, Any]:
    return _normalize_edit_payload(_extract_payload_dict(text, _looks_like_edit_payload))


def extract_strategy_payload(text: str) -> dict[str, Any]:
    return _extract_payload_dict(text, _looks_like_strategy_payload)


def extract_plan_payload(text: str) -> dict[str, Any]:
    return _extract_payload_dict(text, _looks_like_plan_payload)


def _looks_like_edit_payload(value: dict[str, Any]) -> bool:
    return (
        isinstance(value.get("summary"), str)
        and isinstance(value.get("files", []), (list, dict))
        and isinstance(value.get("notes", []), (list, str))
    )


def _normalize_edit_payload(value: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(value)
    files = normalized.get("files", [])
    if isinstance(files, dict):
        normalized["files"] = [files]
    elif isinstance(files, list):
        normalized["files"] = files
    else:
        normalized["files"] = []

    notes = normalized.get("notes", [])
    if isinstance(notes, str):
        normalized["notes"] = [notes]
    elif isinstance(notes, list):
        normalized["notes"] = [note if isinstance(note, str) else json.dumps(note, sort_keys=True) for note in notes]
    else:
        normalized["notes"] = []
    return normalized


def _looks_like_strategy_payload(value: dict[str, Any]) -> bool:
    return any(
        key in value
        for key in (
            "strategy_summary",
            "exact_action",
            "proposed_task_title",
            "proposed_task_type",
            "utility",
            "risk",
            "confidence",
            "estimated_runtime_seconds",
            "touched_files",
        )
    )


def _looks_like_plan_payload(value: dict[str, Any]) -> bool:
    return isinstance(value.get("tasks"), list)


def _extract_payload_dict(text: str, predicate: PayloadPredicate) -> dict[str, Any]:
    payload = _extract_from_value(text, predicate)
    if payload is None:
        raise ValueError("Provider response did not contain a usable JSON payload.")
    return payload


def _extract_from_value(value: Any, predicate: PayloadPredicate) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if predicate(value):
            return value
        for key in ("text", "output_text", "content", "message", "output", "data", "value"):
            nested = value.get(key)
            if nested is None:
                continue
            payload = _extract_from_value(nested, predicate)
            if payload is not None:
                return payload
        for nested in value.values():
            if isinstance(nested, (dict, list, str)):
                payload = _extract_from_value(nested, predicate)
                if payload is not None:
                    return payload
        return None
    if isinstance(value, list):
        for item in value:
            payload = _extract_from_value(item, predicate)
            if payload is not None:
                return payload
        return None
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    for candidate in _candidate_json_documents(cleaned):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        payload = _extract_from_value(parsed, predicate)
        if payload is not None:
            return payload
    return None


def _candidate_json_documents(text: str) -> list[str]:
    candidates: list[str] = []

    def add(candidate: str | None) -> None:
        if candidate is None:
            return
        stripped = candidate.strip()
        if stripped and stripped not in candidates:
            candidates.append(stripped)

    add(text)
    add(_extract_fenced_block(text))
    add(_extract_balanced_fragment(text))
    return candidates


def _extract_fenced_block(text: str) -> str | None:
    if "```" not in text:
        return None
    chunks = text.split("```")
    if len(chunks) < 3:
        return None
    for chunk in chunks[1::2]:
        stripped = chunk.strip()
        if stripped.startswith("json"):
            stripped = stripped[4:].strip()
        if stripped:
            return stripped
    return None


def _extract_balanced_fragment(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None
