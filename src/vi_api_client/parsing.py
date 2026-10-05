"""Parsing logic for Viessmann API features."""

from __future__ import annotations

from math import isfinite
from typing import Any, NamedTuple, cast

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

# Property keys that describe a feature's value instead of being a value
# themselves, so they never become flat features of their own.
_METADATA_PROPERTY_KEYS = frozenset({"unit", "type", "components", "displayValue"})

# "min" and "max" are metadata when scalar (bounds of the value) but feature
# properties when they hold their own value object.
_BOUND_PROPERTY_KEYS = frozenset({"min", "max"})

# Parameter names through which commands write a whole schedule object.
_SCHEDULE_PARAMETER_NAMES = frozenset({"schedule", "entries", "newSchedule"})


class ValidatedApiFeature(NamedTuple):
    """The known fields of one API feature after validation."""

    name: str
    properties: dict[str, JsonValue]
    commands: dict[str, Any]
    is_enabled: bool
    is_ready: bool


def api_feature_to_flat_features(api_feature: dict[str, Any]) -> list[Feature]:
    """Parse one API feature into its flat features.

    One API feature (e.g. 'heating.circuits.0') can produce several flat
    features (e.g. '...active', '...name'). An API feature whose properties
    hold schedules or time series stays one feature with its whole property
    object as value.

    Args:
        api_feature: The raw JSON object of one API feature.

    Returns:
        The flat features parsed from the API feature.

    Raises:
        ViResponseError: If a known feature response field violates the API contract.
    """
    entry = validate_feature_entry(api_feature)
    api_feature_name, properties = entry.name, entry.properties

    if not set(properties).isdisjoint(COMPLEX_DATA_INDICATORS):
        control = _find_control_for_complex_feature(api_feature_name, entry.commands)
        features = [
            Feature(
                name=api_feature_name,
                value=properties,
                unit=None,
                is_enabled=entry.is_enabled,
                is_ready=entry.is_ready,
                control=control,
            )
        ]
        features.extend(
            _build_consumption_alias_features(
                api_feature_name=api_feature_name,
                properties=properties,
                is_enabled=entry.is_enabled,
                is_ready=entry.is_ready,
            )
        )
        return features

    default_unit = properties.get("unit")
    if not isinstance(default_unit, str):
        default_unit = None

    features: list[Feature] = []
    for key in _feature_property_keys(properties):
        property_data = properties[key]
        # The "value" property carries the API feature's own value.
        feature_name = (
            api_feature_name if key == "value" else f"{api_feature_name}.{key}"
        )
        value, unit = _extract_value_and_unit(property_data, default_unit)
        control = _find_control(key, entry.commands, api_feature_name, property_data)
        features.append(
            Feature(
                name=feature_name,
                value=value,
                unit=unit,
                is_enabled=entry.is_enabled,
                is_ready=entry.is_ready,
                control=control,
            )
        )

    return features


def _feature_property_keys(properties: dict[str, JsonValue]) -> list[str]:
    """Return the property keys that become flat features of their own."""
    return [
        key
        for key, property_data in properties.items()
        if key not in _METADATA_PROPERTY_KEYS
        and (key not in _BOUND_PROPERTY_KEYS or isinstance(property_data, dict))
    ]


def validate_feature_entry(api_feature: dict[str, Any]) -> ValidatedApiFeature:
    """Validate the known API fields needed to parse one API feature.

    Raises:
        ViResponseError: If the name, properties, commands, enabled and ready
            flags, or unit violate the API contract.
    """
    api_feature_name = api_feature.get("feature")
    if not isinstance(api_feature_name, str) or not api_feature_name:
        raise ViResponseError(
            "Feature name must be a non-empty string (no valid feature name)"
        )
    raw_properties = api_feature.get("properties")
    if not isinstance(raw_properties, dict):
        raise ViResponseError("Feature properties must be an object")
    # Runtime-checked container; property fields are validated recursively.
    properties_container = cast("dict[str, Any]", raw_properties)
    properties = validate_json_value(properties_container, path="Feature properties")
    # Defensive double-check: validate_json_value above only returns dicts
    # for the dict container that was already confirmed here.
    if not isinstance(properties, dict):  # pragma: no cover
        raise ViResponseError("Feature properties must be an object")
    _validate_property_constraints(properties)
    raw_commands = api_feature.get("commands", {})
    if not isinstance(raw_commands, dict):
        raise ViResponseError("Feature commands must be an object")
    # Runtime-checked container; known command fields are validated next.
    commands = cast("dict[str, Any]", raw_commands)
    _validate_commands(commands)
    is_enabled = api_feature.get("isEnabled", True)
    is_ready = api_feature.get("isReady", True)
    if not isinstance(is_enabled, bool) or not isinstance(is_ready, bool):
        raise ViResponseError("Feature enabled and ready fields must be booleans")
    unit = properties.get("unit")
    if unit is not None and not isinstance(unit, str):
        raise ViResponseError("Feature unit must be a string")
    return ValidatedApiFeature(
        api_feature_name, properties, commands, is_enabled, is_ready
    )


def _validate_commands(commands: dict[str, Any]) -> None:
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
    api_feature_name: str,
    properties: dict[str, Any],
    is_enabled: bool,
    is_ready: bool,
) -> list[Feature]:
    """Build synthetic currentYear alias features for selected consumption metrics.

    Args:
        api_feature_name: The name of the API feature.
        properties: The raw properties dictionary of the feature.
        is_enabled: Whether the feature is enabled.
        is_ready: Whether the feature is ready.

    Returns:
        List of synthetic alias features.
    """
    if api_feature_name not in CONSUMPTION_ALIAS_FEATURES:
        return []

    features: list[Feature] = []

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

        features.append(
            Feature(
                name=f"{api_feature_name}.{alias_name}",
                value=values[0],
                unit=source.get("unit"),
                is_enabled=is_enabled,
                is_ready=is_ready,
                control=None,
            )
        )

    return features


def _extract_value_and_unit(
    property_data: Any, default_unit: str | None
) -> tuple[Any, str | None]:
    """Return the value and unit of one property.

    Args:
        property_data: The property value (dict with 'value'/'unit' or raw value).
        default_unit: Fallback unit if the property names none.

    Returns:
        Tuple of (value, unit).
    """
    if isinstance(property_data, dict):
        # Runtime-checked container; raw property shapes stay dynamic until
        # the flattening contracts validate their known fields.
        data = cast("dict[str, Any]", property_data)
        return data.get("value"), data.get("unit", default_unit)
    return property_data, default_unit


def _find_control(
    property_key: str,
    commands: dict[str, Any],
    api_feature_name: str,
    property_data: Any = None,
) -> FeatureControl | None:
    """Find the executable command whose parameter writes the given property.

    Args:
        property_key: The property key (e.g. 'slope').
        commands: The API feature's commands.
        api_feature_name: The name of the API feature owning the property.
        property_data: Optional property metadata for constraint fallback.

    Returns:
        FeatureControl command metadata if a matching command is found, else None.
    """
    for command_name, command in commands.items():
        if not command.get("isExecutable", True):
            continue

        params = command.get("params", {})
        target_param = _match_parameter(property_key, params, command_name)
        if target_param:
            return _build_control(
                command_name, command, target_param, api_feature_name, property_data
            )

    return None


def _build_control(
    command_name: str,
    command: dict[str, Any],
    target_param: str,
    api_feature_name: str,
    property_data: Any,
) -> FeatureControl:
    """Construct FeatureControl command metadata from one command."""
    # Command and parameter containers were validated by
    # validate_feature_entry before flattening.
    params = cast("dict[str, Any]", command.get("params", {}))
    parameter = cast("dict[str, Any]", params[target_param])
    parameter_constraints = cast("dict[str, Any]", parameter.get("constraints", {}))
    property_metadata = (
        cast("dict[str, Any]", property_data) if isinstance(property_data, dict) else {}
    )
    property_constraints = cast(
        "dict[str, Any]", property_metadata.get("constraints", {})
    )

    # The command parameter is what the API validates on write, so its
    # constraints win; the property's metadata only fills gaps.
    sources = [
        parameter,
        parameter_constraints,
        property_metadata,
        property_constraints,
    ]

    return FeatureControl(
        command_name=command_name,
        param_name=target_param,
        required_params=_get_required_params(params),
        parent_feature_name=api_feature_name,
        uri=command.get("uri", ""),
        min=_resolve_constraint(["min"], sources),
        max=_resolve_constraint(["max"], sources),
        step=_resolve_constraint(["step", "stepping"], sources),
        value_type=parameter.get("type"),
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
    property_key: str, params: dict[str, Any], command_name: str
) -> str | None:
    """Return the command parameter that writes the given property, if any.

    Args:
        property_key: The property name to match.
        params: The command's parameters.
        command_name: The command name, used by the single-parameter heuristic.

    Returns:
        The matched parameter name or None.
    """
    if property_key in params:
        return property_key

    # The API names the writable temperature differently from the property.
    if property_key == "temperature" and "targetTemperature" in params:
        return "targetTemperature"

    # A single-parameter command writes the property its name mentions, e.g.
    # setHysteresisSwitchOnValue(hysteresis) writes 'switchOnValue'; a plain
    # 'value' property maps to the only parameter as well.
    if len(params) == 1 and (
        property_key.lower() in command_name.lower() or property_key == "value"
    ):
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
    api_feature_name: str, commands: dict[str, Any]
) -> FeatureControl | None:
    """Heuristic to find control for a complex feature (e.g. schedule).

    Args:
        api_feature_name: The name of the API feature.
        commands: The API feature's commands.

    Returns:
        FeatureControl if a likely command is found.
    """
    for command_name, command in commands.items():
        if not command.get("isExecutable", True):
            continue

        params = command.get("params", {})
        # Pick the first schedule-like parameter found.
        target_param = next(
            (
                parameter_name
                for parameter_name in params
                if parameter_name in _SCHEDULE_PARAMETER_NAMES
            ),
            None,
        )
        if target_param:
            return FeatureControl(
                command_name=command_name,
                param_name=target_param,
                required_params=_get_required_params(params),
                parent_feature_name=api_feature_name,
                uri=command.get("uri", ""),
                value_type=params[target_param].get("type"),
                # A schedule is written as one object, so the scalar
                # constraints (min, max, step, options) do not apply.
            )
    return None
