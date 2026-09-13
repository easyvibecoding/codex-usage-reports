from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def request_usage(value: Any) -> dict[str, int] | None:
    """Validate native per-request usage, shared by offline and inline reports."""
    if not isinstance(value, dict):
        return None
    required_keys = ("input_tokens", "cached_input_tokens", "output_tokens", "total_tokens")
    optional_keys = ("cache_write_input_tokens", "reasoning_output_tokens")
    if any(key not in value for key in required_keys):
        return None
    values: dict[str, int] = {}
    for key in required_keys + optional_keys:
        raw = value.get(key, 0)
        if type(raw) is not int or raw < 0:
            return None
        values[key] = raw
    if values["cached_input_tokens"] > values["input_tokens"]:
        return None
    if values["cache_write_input_tokens"] > values["input_tokens"]:
        return None
    if values["reasoning_output_tokens"] > values["output_tokens"]:
        return None
    if values["total_tokens"] != values["input_tokens"] + values["output_tokens"]:
        return None
    return values


@dataclass(frozen=True)
class Usage:
    total: int = 0
    input: int = 0
    cached_input: int = 0
    output: int = 0
    reasoning_output: int = 0


def _usage_from_line(record: Any) -> Usage | None:
    if not isinstance(record, dict) or record.get("type") != "event_msg":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    info = payload.get("info")
    totals = info.get("total_token_usage") if isinstance(info, dict) else None
    if not isinstance(totals, dict) or "total_tokens" not in totals:
        return None
    keys = (
        "total_tokens",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    values = {key: totals.get(key, 0) for key in keys}
    if any(type(value) is not int or not 0 <= value <= 2**63 - 1 for value in values.values()):
        return None
    if values["cached_input_tokens"] > values["input_tokens"]:
        return None
    if values["reasoning_output_tokens"] > values["output_tokens"]:
        return None
    if ("input_tokens" in totals or "output_tokens" in totals) and (
        values["total_tokens"] != values["input_tokens"] + values["output_tokens"]
    ):
        return None
    return Usage(
        total=values["total_tokens"],
        input=values["input_tokens"],
        cached_input=values["cached_input_tokens"],
        output=values["output_tokens"],
        reasoning_output=values["reasoning_output_tokens"],
    )
