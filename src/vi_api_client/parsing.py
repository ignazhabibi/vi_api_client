"""Parsing logic for Viessmann API features."""

from __future__ import annotations

from typing import Any

from .models import Feature, FeatureControl

# Keys that indicate complex data structures which should NOT be flattened.
COMPLEX_DATA_INDICATORS = {
    "entries",  # History, Error lists
    "day",  # Time series
    "week",
    "month",
    "year",
    "schedule",  # Schedules
    "mon",
    "tue",
    "wed",
    "thu",
    "fri",
    "sat",
    "sun",  # Schedule days
}


def parse_feature_flat(data: dict[str, Any]) -> list[Feature]:
    """Parse a nested API feature object into a list of flat Feature objects.

    One API feature (e.g. 'heating.circuits.0') can result in multiple atomic
    Features (e.g. '...temperature', '...operating.modes.active').

    Args:
        data: The raw JSON dictionary for a single feature from the API.

    Returns:
        List of flattened Feature objects.
    """
    base_name = data.get("feature", "unknown")
    properties = data.get("properties", {})
    commands = data.get("commands", {})
    is_enabled = data.get("isEnabled", True)
    is_ready = data.get("isReady", True)

    # Check for complex data that should stay complex
    prop_keys = set(properties.keys())
    if not prop_keys.isdisjoint(COMPLEX_DATA_INDICATORS):
        # Return as single complex feature
        control = _find_control_for_complex_feature(base_name, commands)

        return [
            Feature(
                name=base_name,
                value=properties,  # The whole dict
                unit=None,
                is_enabled=is_enabled,
                is_ready=is_ready,
                control=control,
            )
        ]

    # 2. Flattening Logic
    features_out = []

    ignore_keys = {"unit", "type", "components", "displayValue"}

    # "min" and "max" should only be ignored if they are metadata (scalars),
    # not if they are actual feature properties (nested dicts/values).
    data_keys = []
    for key in properties:
        if key in ignore_keys:
            continue
        if key in ["min", "max"]:
            # Check if it's complex (dict) -> Treat as feature
            # If scalar -> Treat as metadata (ignore)
            value = properties[key]
            if not isinstance(value, dict):
                continue
        data_keys.append(key)

    # Fallback for simple features that might only have 'value'
    if not data_keys and "value" in properties:
        data_keys = ["value"]

    for key in data_keys:
        # Determine strict name
        if key == "value":
            feat_name = base_name
            prop_data = properties["value"]
        else:
            feat_name = f"{base_name}.{key}"
            prop_data = properties[key]

        # Extract value and unit
        value, unit = _extract_value_and_unit(prop_data, properties.get("unit"))

        # Find control logic
        control = _find_control(key, commands, base_name, prop_data)

        features_out.append(
            Feature(
                name=feat_name,
                value=value,
                unit=unit,
                is_enabled=is_enabled,
                is_ready=is_ready,
                control=control,
            )
        )

    return features_out


def _extract_value_and_unit(
    prop_data: Any, default_unit: str | None
) -> tuple[Any, str | None]:
    """Helper to safely extract value and unit from property data.

    Args:
        prop_data: The property value (dict with 'value'/'unit' or raw value).
        default_unit: Fallback unit if not present in prop_data.

    Returns:
        Tuple of (value, unit).
    """
    if isinstance(prop_data, dict):
        return prop_data.get("value"), prop_data.get("unit", default_unit)
    return prop_data, default_unit


def _find_control(
    prop_key: str,
    commands: dict[str, Any],
    parent_name: str,
    prop_data: Any = None,
) -> FeatureControl | None:
    """Find the command that controls the given property.

    Args:
        prop_key: The property key (e.g. 'slope').
        commands: Dictionary of available commands.
        parent_name: The Full feature name (for context).
        prop_data: Optional property metadata for constraint fallback.

    Returns:
        FeatureControl object if a matching command is found, else None.
    """
    # 1. Direct Command Search
    # Iterate all commands to see if any parameter matches this property
    for cmd_name, cmd_data in commands.items():
        if not cmd_data.get("isExecutable", True):
            continue

        params = cmd_data.get("params", {})
        target_param = _match_parameter(prop_key, params, cmd_name)

        if target_param:
            return _build_control(
                cmd_name, cmd_data, target_param, parent_name, prop_data
            )

    return None


def _build_control(
    cmd_name: str, cmd_data: dict, target_param: str, parent_name: str, prop_data: Any
) -> FeatureControl:
    """Construct FeatureControl object from command data."""
    params = cmd_data.get("params", {})
    p_data = params[target_param]
    constraints_dict = p_data.get("constraints", {})
    prop_data_dict = prop_data if isinstance(prop_data, dict) else {}
    prop_constraints = prop_data_dict.get("constraints", {})

    # Priority list for finding constraints
    sources = [p_data, constraints_dict, prop_data_dict, prop_constraints]

    return FeatureControl(
        command_name=cmd_name,
        param_name=target_param,
        required_params=list(params.keys()),
        parent_feature_name=parent_name,
        uri=cmd_data.get("uri", ""),
        min=_resolve_constraint(["min"], sources),
        max=_resolve_constraint(["max"], sources),
        step=_resolve_constraint(["step", "stepping"], sources),
        options=_resolve_constraint(["enum"], sources),
        min_length=_resolve_constraint(["minLength"], sources),
        max_length=_resolve_constraint(["maxLength"], sources),
        pattern=_resolve_constraint(["pattern", "regEx"], sources),
    )


def _match_parameter(
    prop_key: str, params: dict[str, Any], cmd_name: str
) -> str | None:
    """Determine which parameter matches the property key.

    Args:
        prop_key: The property name we are looking for.
        params: The command parameters dictionary.
        cmd_name: The name of the command (for heuristics).

    Returns:
        The matched parameter name or None.
    """
    # 1. Direct Match: Parameter name matches property key
    if prop_key in params:
        return prop_key

    # 2. Logic Match: Known Aliases
    # Temperature alias
    if prop_key == "temperature" and "targetTemperature" in params:
        return "targetTemperature"

    # 3. Orphan Property Heuristic
    # If property is 'switchOnValue' and command is 'set...SwitchOnValue'
    # and there is exactly 1 parameter -> assume that parameter is correct.
    # This handles "hysteresis" param in "setHysteresisSwitchOnValue".
    if len(params) == 1:
        # Check if property name is contained in command name (case-insensitive)
        # e.g. prop="switchOnValue", cmd="setHysteresisSwitchOnValue" -> Match
        if prop_key.lower() in cmd_name.lower():
            return next(iter(params))

        # Check specific 'value' fallback for short properties
        if prop_key == "value":
            return next(iter(params))

    return None


def _resolve_constraint(keys: list[str], sources: list[dict[str, Any]]) -> Any | None:
    """Try to find a constraint value in multiple sources (prioritized).

    Args:
        keys: List of keys to look for (e.g. ['step', 'stepping']).
        sources: List of dictionaries to search in order.

    Returns:
        The found value or None.
    """
    for source in sources:
        for key in keys:
            if key in source:
                return source[key]
    return None


def _find_control_for_complex_feature(
    base_name: str, commands: dict[str, Any]
) -> FeatureControl | None:
    """Heuristic to find control for a complex feature (e.g. schedule).

    Args:
        base_name: The base name of the feature.
        commands: Dictionary of commands.

    Returns:
        FeatureControl if a likely command is found.
    """
    for cmd_name, cmd_data in commands.items():
        params = cmd_data.get("params", {})
        if "schedule" in params or "entries" in params or "newSchedule" in params:
            # Just pick the first param found
            allowed = {"schedule", "entries", "newSchedule"}
            target_param = next((k for k in params if k in allowed), None)
            if target_param:
                return FeatureControl(
                    command_name=cmd_name,
                    param_name=target_param,
                    required_params=list(params.keys()),
                    parent_feature_name=base_name,
                    uri=cmd_data.get("uri", ""),
                    # Complex controls rarely have simple min/max
                )
    return None
