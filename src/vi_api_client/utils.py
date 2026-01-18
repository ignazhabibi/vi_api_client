"""Utility functions for Viessmann API Client."""

import json
import re
from contextlib import suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import Feature


def parse_cli_params(params_list: list[str]) -> dict[str, Any]:
    """Parse a list of CLI parameter strings into a dictionary.

    Supports two formats:
    1. Single JSON string: '{"slope": 1.0, "shift": 0}'
    2. Key-Value pairs: 'slope=1.0' 'shift=0' 'mode=active'

    Performs basic type inference for numbers and booleans.

    Args:
        params_list: List of strings from the command line (e.g. argparse nargs='*').

    Returns:
        Dictionary of parsed parameters.
    """
    params = {}

    if not params_list:
        return params

    # Case 1: Single argument that looks like JSON
    if len(params_list) == 1 and params_list[0].strip().startswith("{"):
        try:
            return json.loads(params_list[0])
        except json.JSONDecodeError:
            raise ValueError(
                "Example appears to be JSON but could not be parsed."
            ) from None

    # Case 2: Key=Value pairs
    for item in params_list:
        if "=" not in item:
            raise ValueError(f"Invalid argument format '{item}'. Expected key=value.")

        key, val_str = item.split("=", 1)

        # Type inference
        value = val_str
        if val_str.lower() == "true":
            value = True
        elif val_str.lower() == "false":
            value = False
        else:
            try:
                value = int(val_str)
            except ValueError:
                try:
                    value = float(val_str)
                except ValueError:
                    # Try parsing as JSON (e.g. for nested objects or lists)
                    if val_str.startswith("[") or val_str.startswith("{"):
                        with suppress(json.JSONDecodeError):
                            value = json.loads(val_str)

        params[key] = value

    return params


def format_feature(feature: "Feature") -> str:
    """Format a feature's value for display (CLI/Logs)."""
    val = feature.value
    u = feature.unit

    if val is None:
        return _format_dump_properties(feature.properties)

    # Check if value is a schedule dict (has day keys like 'mon', 'tue', etc.)
    if isinstance(val, dict) and {"mon", "tue", "wed"}.issubset(val.keys()):
        return _format_schedule(val)

    # Special handling: if value is bool but we also have schedule entries
    if isinstance(val, bool) and "entries" in feature.properties:
        entries_prop = feature.properties["entries"]
        if isinstance(entries_prop, dict) and entries_prop.get("type") == "Schedule":
            schedule_val = entries_prop.get("value", {})
            schedule_str = _format_schedule(schedule_val)
            return f"{val} | {schedule_str}"

    # Formatting for Lists (History Data)
    if isinstance(val, list):
        content = str(val) if len(val) <= 10 else f"List[{len(val)} items]"
        return f"{content} {u}".strip() if u else content

    return f"{val} {u}".strip() if u else str(val)


def _format_dump_properties(properties: dict[str, Any]) -> str:
    """Fallback: dump all properties nicely."""
    parts = []
    for k, v in properties.items():
        if isinstance(v, dict) and "value" in v:
            # Check for Schedule type
            if v.get("type") == "Schedule" and isinstance(v.get("value"), dict):
                schedule_str = _format_schedule(v["value"])
                parts.append(f"{k}: {schedule_str}")
            else:
                parts.append(f"{k}: {v['value']} {v.get('unit', '')}".strip())
        else:
            parts.append(f"{k}: {v}")
    return ", ".join(parts)


def _format_schedule(schedule: dict[str, list]) -> str:
    """Format a schedule object (day -> list of time slots)."""
    day_abbr = {
        "mon": "Mo",
        "tue": "Tu",
        "wed": "We",
        "thu": "Th",
        "fri": "Fr",
        "sat": "Sa",
        "sun": "Su",
    }
    parts = []
    for day in ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]:
        slots = schedule.get(day, [])
        if slots:
            slot_strs = [f"{s.get('start', '?')}-{s.get('end', '?')}" for s in slots]
            parts.append(f"{day_abbr[day]}[{', '.join(slot_strs)}]")
    return " ".join(parts) if parts else "(empty)"


def mask_pii(text: str) -> str:
    """Mask sensitive data (Serials, IDs, Tokens) in a string."""
    if not text:
        return text

    # Mask Tokens (Bearer eyJ...)
    text = re.sub(r"Bearer\s+[a-zA-Z0-9\-_.]+", "Bearer ***", text)

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
