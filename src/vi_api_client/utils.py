"""Helpers for CLI parameters and feature display."""

from __future__ import annotations

import json
from contextlib import suppress
from typing import TYPE_CHECKING

from ._types import JsonValue

if TYPE_CHECKING:
    from .models import Feature

_DAY_ABBREVIATIONS = {
    "mon": "Mo",
    "tue": "Tu",
    "wed": "We",
    "thu": "Th",
    "fri": "Fr",
    "sat": "Sa",
    "sun": "Su",
}


def parse_cli_params(params_list: list[str]) -> dict[str, JsonValue]:
    """Parse a list of CLI parameter strings into a dictionary.

    Supports two formats:
    1. Single JSON string: '{"slope": 1.0, "shift": 0}'
    2. Key-Value pairs: 'slope=1.0' 'shift=0' 'mode=active'

    Performs basic type inference for numbers and booleans.

    Args:
        params_list: List of strings from the command line (e.g. argparse nargs='*').

    Returns:
        Dictionary of parsed JSON-compatible parameters.

    Raises:
        ValueError: If JSON parsing fails or format is invalid.
    """
    if not params_list:
        return {}

    if len(params_list) == 1 and params_list[0].strip().startswith("{"):
        try:
            parsed: JsonValue = json.loads(params_list[0])
        except json.JSONDecodeError:
            raise ValueError(
                "Parameters appear to be JSON but could not be parsed."
            ) from None
        # Defensive: JSON starting with "{" parses to an object or fails.
        if not isinstance(parsed, dict):  # pragma: no cover
            raise ValueError("JSON parameters must form a string-keyed object.")
        return parsed

    params: dict[str, JsonValue] = {}

    for item in params_list:
        if "=" not in item:
            raise ValueError(f"Invalid argument format '{item}'. Expected key=value.")

        key, value_string = item.split("=", 1)
        params[key] = _parse_cli_value(value_string)

    return params


def _parse_cli_value(value_string: str) -> JsonValue:
    """Infer one JSON-compatible parameter value from its command line text."""
    if value_string.lower() == "true":
        return True
    if value_string.lower() == "false":
        return False
    try:
        return int(value_string)
    except ValueError:
        pass
    try:
        return float(value_string)
    except ValueError:
        pass
    if value_string.startswith(("[", "{")):
        with suppress(json.JSONDecodeError):
            parsed: JsonValue = json.loads(value_string)
            return parsed

    return value_string


def format_feature(feature: Feature) -> str:
    """Format a feature's value for display (CLI/Logs).

    Args:
        feature: The feature object to format.

    Returns:
        A formatted string representation of the value and unit.
    """
    value = feature.value
    if value is None:
        return "-"

    # Schedules map weekday keys to time slots.
    if isinstance(value, dict) and {"mon", "tue", "wed"}.issubset(value.keys()):
        return _format_schedule(value)

    # Long lists, such as history data, are summarized to stay readable.
    if isinstance(value, list) and len(value) > 10:
        content = f"List[{len(value)} items]"
    else:
        content = str(value)
    return f"{content} {feature.unit}".strip() if feature.unit else content


def _format_schedule(schedule: dict[str, JsonValue]) -> str:
    """Format a schedule object (day -> list of time slots).

    Args:
        schedule: Dictionary mapping days ('mon', 'tue'...) to list of time slots.

    Returns:
        A concise string representation of the schedule.
    """
    parts: list[str] = []
    for day, abbreviation in _DAY_ABBREVIATIONS.items():
        slots = schedule.get(day, [])
        if not isinstance(slots, list) or not slots:
            continue
        slot_strs = [
            f"{slot.get('start', '?')}-{slot.get('end', '?')}"
            for slot in slots
            if isinstance(slot, dict)
        ]
        if slot_strs:
            parts.append(f"{abbreviation}[{', '.join(slot_strs)}]")
    return " ".join(parts) if parts else "(empty)"
