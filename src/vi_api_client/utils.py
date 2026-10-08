"""Helpers for CLI parameters, feature display, and identifier masking."""

from __future__ import annotations

import json
import re
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
_BEARER_TOKEN_PATTERN = re.compile(r"Bearer\s+[a-zA-Z0-9\-_.]+", re.IGNORECASE)
# Sixteen-digit gateway serials in URL paths, JSON, and CLI output.
_GATEWAY_SERIAL_PATTERN = re.compile(r'(gateways/|serial":\s?"?|Serial: )([0-9]{16})')
# Installation IDs in URL paths, JSON, and CLI output.
_INSTALLATION_ID_PATTERN = re.compile(
    r'(installations/|installationId":\s?|ID: )([0-9]{4,10})'
)
# Installation IDs, gateway serials, and device serials are numeric; shorter
# numbers such as the device ID "0" stay readable.
_IDENTIFIER_SEGMENT_PATTERN = re.compile(r"[0-9]{6,}")
_COORDINATE_KEYS = frozenset({"latitude", "longitude"})


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


def mask_pii(text: str) -> str:
    """Mask sensitive data (Serials, IDs, Tokens) in a string.

    Args:
        text: The input string containing potential PII.

    Returns:
        The masked string.
    """
    if not text:
        return text

    text = _BEARER_TOKEN_PATTERN.sub("Bearer ***", text)
    text = _GATEWAY_SERIAL_PATTERN.sub(r"\1****************", text)
    return _INSTALLATION_ID_PATTERN.sub(r"\1****", text)


def mask_identifiers(document: JsonValue) -> JsonValue:
    """Return a copy of an API document with identifying values masked.

    Masks the identifiers that API responses carry in values and URIs, so a
    response can be shared, for example as a fixture:

    - A string, or a ``/``-separated segment of a string, that consists of six
      or more digits is replaced by the same number of ``#`` characters. This
      covers installation IDs, gateway serials, and device serials, the rule
      PyViCare's ``dump_secure`` applies. Numbers, such as counters, are kept.
    - The numeric value of a ``latitude`` or ``longitude`` entry is set to 0.

    Free text, such as user-chosen names, is not masked.

    Args:
        document: A JSON-compatible API document.

    Returns:
        The masked copy with the same structure.
    """
    if isinstance(document, str):
        return "/".join(
            "#" * len(segment)
            if _IDENTIFIER_SEGMENT_PATTERN.fullmatch(segment)
            else segment
            for segment in document.split("/")
        )
    if isinstance(document, list):
        return [mask_identifiers(item) for item in document]
    if isinstance(document, dict):
        return {
            str(mask_identifiers(key)): (
                _zero_coordinate(mask_identifiers(value))
                if key in _COORDINATE_KEYS
                else mask_identifiers(value)
            )
            for key, value in document.items()
        }
    return document


def _zero_coordinate(coordinate: JsonValue) -> JsonValue:
    """Return a coordinate, or its property object, with the number set to 0."""
    if _is_number(coordinate):
        return 0
    if isinstance(coordinate, dict) and _is_number(coordinate.get("value")):
        return {**coordinate, "value": 0}
    return coordinate


def _is_number(value: JsonValue) -> bool:
    """Return whether a JSON value is a number rather than a boolean."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)
