"""Parsing logic for Viessmann API features."""

from __future__ import annotations

from math import isfinite
from typing import Any, cast

from ._types import JsonValue
from .exceptions import ViResponseError
from .models import Feature, FeatureControl
from .validation import validate_json_value

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

CONSUMPTION_ALIAS_FEATURES = {
    "heating.power.consumption.cooling",
    "heating.power.consumption.dhw",
    "heating.power.consumption.heating",
    "heating.power.consumption.total",
}

CONSUMPTION_ALIAS_MAPPING = {
    "currentYear": "year",
}


def parse_feature_flat(data: dict[str, Any]) -> list[Feature]:
    """Parse a nested API feature object into a list of flat Feature objects.

    One API feature (e.g. 'heating.circuits.0') can result in multiple atomic
    Features (e.g. '...temperature', '...operating.modes.active').

    Args:
        data: The raw JSON dictionary for a single feature from the API.

    Returns:
        List of flattened Feature objects.

    Raises:
        ViResponseError: If a known feature response field violates the API contract.
    """
    base_name, properties, commands, is_enabled, is_ready = validate_feature_entry(data)

    # Check for complex data that should stay complex
    prop_keys = set(properties.keys())
    if not prop_keys.isdisjoint(COMPLEX_DATA_INDICATORS):
        # Return as single complex feature
        control = _find_control_for_complex_feature(base_name, commands)

        features_out = [
            Feature(
                name=base_name,
                value=properties,  # The whole dict
                unit=None,
                is_enabled=is_enabled,
                is_ready=is_ready,
                control=control,
            )
        ]
        features_out.extend(
            _build_consumption_alias_features(
                base_name=base_name,
                properties=properties,
                is_enabled=is_enabled,
                is_ready=is_ready,
            )
        )
        return features_out

    # 2. Flattening Logic
    features_out: list[Feature] = []

    ignore_keys = {"unit", "type", "components", "displayValue"}

    # "min" and "max" should only be ignored if they are metadata (scalars),
    # not if they are actual feature properties (nested dicts/values).
    data_keys: list[str] = []
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

    default_unit = properties.get("unit")
    if not isinstance(default_unit, str):
        default_unit = None

    for key in data_keys:
        # Determine strict name
        if key == "value":
            feat_name = base_name
            prop_data = properties["value"]
        else:
            feat_name = f"{base_name}.{key}"
            prop_data = properties[key]

        # Extract value and unit
        value, unit = _extract_value_and_unit(prop_data, default_unit)

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


def validate_feature_entry(
    data: dict[str, Any],
) -> tuple[str, dict[str, JsonValue], dict[str, Any], bool, bool]:
    """Validate the known API fields needed to parse one feature entry."""
    base_name = data.get("feature")
    if not isinstance(base_name, str) or not base_name:
        raise ViResponseError(
            "Feature name must be a non-empty string (no valid feature name)"
        )
    raw_properties = data.get("properties")
    if not isinstance(raw_properties, dict):
        raise ViResponseError("Feature properties must be an object")
    # Runtime-checked container; property fields are validated recursively.
    properties_container = cast("dict[str, Any]", raw_properties)
    properties = validate_json_value(properties_container, path="Feature properties")
    if not isinstance(properties, dict):
        raise ViResponseError("Feature properties must be an object")
    _validate_property_constraints(properties)
    raw_commands = data.get("commands", {})
    if not isinstance(raw_commands, dict):
        raise ViResponseError("Feature commands must be an object")
    # Runtime-checked container; known command fields are validated next.
    commands = cast("dict[str, Any]", raw_commands)
    _validate_commands(commands)
    is_enabled = data.get("isEnabled", True)
    is_ready = data.get("isReady", True)
    if not isinstance(is_enabled, bool) or not isinstance(is_ready, bool):
        raise ViResponseError("Feature enabled and ready fields must be booleans")
    unit = properties.get("unit")
    if unit is not None and not isinstance(unit, str):
        raise ViResponseError("Feature unit must be a string")
    return base_name, properties, commands, is_enabled, is_ready


def _validate_commands(commands: dict[str, Any]) -> None:  # noqa: PLR0912
    """Validate known command and parameter metadata without closing the schema."""
    # The container casts assert string keys; programmatic mappings may use
    # other key types, so every key is still runtime-checked here.
    named_commands = cast("dict[object, Any]", commands)
    for command_name, raw_command in named_commands.items():
        if not isinstance(command_name, str) or not isinstance(raw_command, dict):
            raise ViResponseError("Feature commands must contain named objects")
        # Runtime-checked container; known fields are validated individually.
        command = cast("dict[str, Any]", raw_command)
        uri = command.get("uri")
        if uri is not None and not isinstance(uri, str):
            raise ViResponseError("Feature command uri must be a string")
        executable = command.get("isExecutable")
        if executable is not None and not isinstance(executable, bool):
            raise ViResponseError("Feature command isExecutable must be a boolean")
        raw_params = command.get("params", {})
        if not isinstance(raw_params, dict):
            raise ViResponseError("Feature command params must be an object")
        named_params = cast("dict[object, Any]", raw_params)
        for parameter_name, raw_parameter in named_params.items():
            if not isinstance(parameter_name, str) or not isinstance(
                raw_parameter, dict
            ):
                raise ViResponseError(
                    "Feature command params must contain named objects"
                )
            parameter = cast("dict[str, Any]", raw_parameter)
            _validate_constraint_values(parameter)
            required = parameter.get("required")
            if required is not None and not isinstance(required, bool):
                raise ViResponseError("Feature command required must be a boolean")
            value_type = parameter.get("type")
            if value_type is not None and not isinstance(value_type, str):
                raise ViResponseError("Feature command type must be a string")
            enum = parameter.get("enum")
            if enum is not None and not isinstance(enum, list):
                raise ViResponseError("Feature command enum must be a list")
            if enum is not None:
                validate_json_value(
                    cast("list[object]", enum), path="Feature command enum"
                )
            raw_constraints = parameter.get("constraints")
            if raw_constraints is not None and not isinstance(raw_constraints, dict):
                raise ViResponseError("Feature command constraints must be an object")
            if isinstance(raw_constraints, dict):
                constraints = cast("dict[str, Any]", raw_constraints)
                _validate_constraint_values(constraints)


def _validate_property_constraints(properties: dict[str, JsonValue]) -> None:
    """Validate known property constraint metadata used for control construction."""
    for property_data in properties.values():
        if not isinstance(property_data, dict):
            continue
        _validate_constraint_values(property_data)
        constraints = property_data.get("constraints")
        if constraints is not None and not isinstance(constraints, dict):
            raise ViResponseError("Feature property constraints must be an object")
        if isinstance(constraints, dict):
            _validate_constraint_values(constraints)


def _validate_constraint_values(constraints: dict[str, Any]) -> None:
    """Validate known constraint value shapes while retaining unknown fields."""
    for name in ("min", "max", "step", "stepping"):
        value = constraints.get(name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise ViResponseError(f"Feature constraint {name} must be a number")
        if isinstance(value, float) and not isfinite(value):
            raise ViResponseError(f"Feature constraint {name} must be a finite number")
    for name in ("minLength", "maxLength"):
        value = constraints.get(name)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise ViResponseError(f"Feature constraint {name} must be an integer")
    for name in ("pattern", "regEx"):
        value = constraints.get(name)
        if value is not None and not isinstance(value, str):
            raise ViResponseError(f"Feature constraint {name} must be a string")
    enum = constraints.get("enum")
    if enum is not None:
        if not isinstance(enum, list):
            raise ViResponseError("Feature constraint enum must be a list")
        # Runtime-checked container; enum members follow the JSON contract.
        validate_json_value(cast("list[object]", enum), path="Feature constraint enum")


def _build_consumption_alias_features(
    base_name: str,
    properties: dict[str, Any],
    is_enabled: bool,
    is_ready: bool,
) -> list[Feature]:
    """Build synthetic currentYear alias features for selected consumption metrics.

    Args:
        base_name: The raw API feature name.
        properties: The raw properties dictionary of the feature.
        is_enabled: Whether the feature is enabled.
        is_ready: Whether the feature is ready.

    Returns:
        List of synthetic alias features.
    """
    if base_name not in CONSUMPTION_ALIAS_FEATURES:
        return []

    features_out: list[Feature] = []

    for alias_name, source_name in CONSUMPTION_ALIAS_MAPPING.items():
        source_data = properties.get(source_name)
        if not isinstance(source_data, dict):
            continue
        # Runtime-checked container; alias sources follow the validated
        # feature property contract.
        source = cast("dict[str, Any]", source_data)

        raw_values = source.get("value")
        if not isinstance(raw_values, list) or not raw_values:
            continue
        values = cast("list[JsonValue]", raw_values)

        features_out.append(
            Feature(
                name=f"{base_name}.{alias_name}",
                value=values[0],
                unit=source.get("unit"),
                is_enabled=is_enabled,
                is_ready=is_ready,
                control=None,
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
        # Runtime-checked container; raw property shapes stay dynamic until
        # the flattening contracts validate their known fields.
        data = cast("dict[str, Any]", prop_data)
        return data.get("value"), data.get("unit", default_unit)
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
        FeatureControl command metadata if a matching command is found, else None.
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
    cmd_name: str,
    cmd_data: dict[str, Any],
    target_param: str,
    parent_name: str,
    prop_data: Any,
) -> FeatureControl:
    """Construct FeatureControl command metadata from command data."""
    # Command and parameter containers were validated by
    # validate_feature_entry before flattening.
    params = cast("dict[str, Any]", cmd_data.get("params", {}))
    p_data = cast("dict[str, Any]", params[target_param])
    constraints_dict = cast("dict[str, Any]", p_data.get("constraints", {}))
    prop_data_dict = (
        cast("dict[str, Any]", prop_data) if isinstance(prop_data, dict) else {}
    )
    prop_constraints = cast("dict[str, Any]", prop_data_dict.get("constraints", {}))

    # Priority list for finding constraints
    sources = [p_data, constraints_dict, prop_data_dict, prop_constraints]

    return FeatureControl(
        command_name=cmd_name,
        param_name=target_param,
        required_params=_get_required_params(params),
        parent_feature_name=parent_name,
        uri=cmd_data.get("uri", ""),
        min=_resolve_constraint(["min"], sources),
        max=_resolve_constraint(["max"], sources),
        step=_resolve_constraint(["step", "stepping"], sources),
        value_type=p_data.get("type"),
        options=_resolve_constraint(["enum"], sources),
        min_length=_resolve_constraint(["minLength"], sources),
        max_length=_resolve_constraint(["maxLength"], sources),
        pattern=_resolve_constraint(["pattern", "regEx"], sources),
    )


def _get_required_params(params: dict[str, Any]) -> list[str]:
    """Return parameters required by the API command metadata.

    An omitted marker is conservatively required; only an explicit ``false``
    declares a parameter optional.
    """
    return [
        name
        for name, metadata in params.items()
        if metadata.get("required") is not False
    ]


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
                    required_params=_get_required_params(params),
                    parent_feature_name=base_name,
                    uri=cmd_data.get("uri", ""),
                    value_type=params[target_param].get("type"),
                    # Complex controls rarely have simple min/max
                )
    return None
