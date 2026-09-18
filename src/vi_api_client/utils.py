"""Utility functions for Viessmann API Client."""

from __future__ import annotations

import json
import re
from contextlib import suppress
from typing import TYPE_CHECKING

from ._types import JsonValue

if TYPE_CHECKING:
    from .models import Feature


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

    # Case 1: Single argument that looks like JSON
    if len(params_list) == 1 and params_list[0].strip().startswith("{"):
        try:
            parsed: JsonValue = json.loads(params_list[0])
        except json.JSONDecodeError:
            raise ValueError(
                "Example appears to be JSON but could not be parsed."
            ) from None
        if not isinstance(parsed, dict):
            raise ValueError("JSON parameters must form a string-keyed object.")
        return parsed

    # Case 2: Key=Value pairs
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
    # Try parsing as JSON (e.g. for nested objects or lists)
    if value_string.startswith("[") or value_string.startswith("{"):
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
    unit = feature.unit

    if value is None:
        return "-"

    # Check if value is a schedule dict (has day keys like 'mon', 'tue', etc.)
    if isinstance(value, dict) and {"mon", "tue", "wed"}.issubset(value.keys()):
        return _format_schedule(value)

    # Formatting for Lists (History Data)
    if isinstance(value, list):
        content = str(value) if len(value) <= 10 else f"List[{len(value)} items]"
        return f"{content} {unit}".strip() if unit else content

    return f"{value} {unit}".strip() if unit else str(value)


def _format_schedule(schedule: dict[str, JsonValue]) -> str:
    """Format a schedule object (day -> list of time slots).

    Args:
        schedule: Dictionary mapping days ('mon', 'tue'...) to list of time slots.

    Returns:
        A concise string representation of the schedule.
    """
    day_abbr = {
        "mon": "Mo",
        "tue": "Tu",
        "wed": "We",
        "thu": "Th",
        "fri": "Fr",
        "sat": "Sa",
        "sun": "Su",
    }
    parts: list[str] = []
    for day in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]:
        slots = schedule.get(day, [])
        if not isinstance(slots, list) or not slots:
            continue
        slot_strs = [
            f"{slot.get('start', '?')}-{slot.get('end', '?')}"
            for slot in slots
            if isinstance(slot, dict)
        ]
        if slot_strs:
            parts.append(f"{day_abbr[day]}[{', '.join(slot_strs)}]")
    return " ".join(parts) if parts else "(empty)"


def mask_pii(text: str) -> str:
    """Mask sensitive data (Serials, IDs, Tokens) in a string.

    Args:
        text: The input string containing potential PII.

    Returns:
        The masked string.
    """
    if not text:
        return text

    # Mask Tokens (Bearer eyJ...)
    text = re.sub(r"Bearer\s+[a-zA-Z0-9\-_.]+", "Bearer ***", text, flags=re.IGNORECASE)

    # Mask Gateways in URLs or JSON (16 digit serials)
    # Pattern: gateway_serial, serial, or inside URL path
    text = re.sub(
        r'(gateways/|serial":\s"?|Serial: )([0-9]{16})', r"\1****************", text
    )

    # Mask Installation IDs (numeric, usually 5-8 digits)
    # Context: installations/12345/ or installation_id": 12345
    text = re.sub(
        r'(installations/|installationId":\s?|ID: )([0-9]{4,10})', r"\1****", text
    )

    return text
