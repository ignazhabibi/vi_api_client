"""Parsing logic for Viessmann API responses and features."""

from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Any, NamedTuple, cast

from ._types import JsonValue
from .exceptions import ViResponseError
from .models import (
    CommandResponse,
    Device,
    EventHistoryPage,
    Feature,
    FeatureControl,
    Gateway,
    Installation,
    InstallationEvent,
    ScheduleConstraints,
)
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
    "currentDay": "day",
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
    features (e.g. '...active', '...name'). A schedule becomes one feature
    whose value is the weekly plan itself, the same shape its command writes;
    its other properties become flat features of their own. An API feature
    whose properties hold time series or other complex data stays one feature
    with its whole property object as value.

    Args:
        api_feature: The raw JSON object of one API feature.

    Returns:
        The flat features parsed from the API feature.

    Raises:
        ViResponseError: If a known feature response field violates the API contract.
    """
    entry = validate_feature_entry(api_feature)
    api_feature_name, properties = entry.name, entry.properties

    schedule_entries = properties.get("entries")
    if (
        isinstance(schedule_entries, dict)
        and schedule_entries.get("type") == "Schedule"
    ):
        schedule_feature = Feature(
            name=api_feature_name,
            value=schedule_entries.get("value"),
            unit=None,
            is_enabled=entry.is_enabled,
            is_ready=entry.is_ready,
            control=_find_control_for_complex_feature(api_feature_name, entry.commands),
        )
        other_properties = {
            key: value for key, value in properties.items() if key != "entries"
        }
        return [schedule_feature, *_flat_property_features(entry, other_properties)]

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

    return _flat_property_features(entry, properties)


def _flat_property_features(
    entry: ValidatedApiFeature, properties: dict[str, JsonValue]
) -> list[Feature]:
    """Return one flat feature per value-carrying property."""
    default_unit = properties.get("unit")
    if not isinstance(default_unit, str):
        default_unit = None

    features: list[Feature] = []
    for key in _feature_property_keys(properties):
        property_data = properties[key]
        # The "value" property carries the API feature's own value.
        feature_name = entry.name if key == "value" else f"{entry.name}.{key}"
        value, unit = _extract_value_and_unit(property_data, default_unit)
        control = _find_control(key, entry.commands, entry.name, property_data)
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
    _validate_schedule_constraint_values(constraints)
    enum = constraints.get("enum")
    if enum is not None:
        if not isinstance(enum, list):
            raise ViResponseError("Feature constraint enum must be a list")
        # Runtime-checked container; enum members follow the JSON contract.
        validate_json_value(cast("list[object]", enum), path="Feature constraint enum")


def _validate_schedule_constraint_values(constraints: dict[str, Any]) -> None:
    """Validate the known schedule rule shapes of one constraint object."""
    max_entries = constraints.get("maxEntries")
    resolution = constraints.get("resolution")
    for name, value in (("maxEntries", max_entries), ("resolution", resolution)):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 1
        ):
            raise ViResponseError(
                f"Feature constraint {name} must be a positive integer"
            )
    modes = constraints.get("modes")
    if modes is not None and (
        not isinstance(modes, list)
        or not all(isinstance(mode, str) for mode in cast("list[object]", modes))
    ):
        raise ViResponseError("Feature constraint modes must be a list of strings")
    overlap_allowed = constraints.get("overlapAllowed")
    if overlap_allowed is not None and not isinstance(overlap_allowed, bool):
        raise ViResponseError("Feature constraint overlapAllowed must be a boolean")
    default_mode = constraints.get("defaultMode")
    if default_mode is not None and not isinstance(default_mode, str):
        raise ViResponseError("Feature constraint defaultMode must be a string")


def _build_consumption_alias_features(
    api_feature_name: str,
    properties: dict[str, Any],
    is_enabled: bool,
    is_ready: bool,
) -> list[Feature]:
    """Build synthetic currentDay and currentYear aliases for consumption metrics.

    Each alias mirrors the first entry of its time series, so day and year
    counters come from the same source as the series itself.

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
    # A command whose matching parameter is optional only writes the value on
    # the side, e.g. 'activate(temperature)' switches a program on; a command
    # that requires the parameter, such as 'setTemperature', is preferred.
    optional_parameter_control: FeatureControl | None = None
    for command_name, command in commands.items():
        if not command.get("isExecutable", True):
            continue

        params = command.get("params", {})
        target_param = _match_parameter(property_key, params, command_name)
        if target_param:
            control = _build_control(
                command_name, command, target_param, api_feature_name, property_data
            )
            if params[target_param].get("required") is not False:
                return control
            optional_parameter_control = optional_parameter_control or control

    return optional_parameter_control


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
        parameter_name=target_param,
        required_parameters=_get_required_parameters(params),
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


def _get_required_parameters(params: dict[str, Any]) -> list[str]:
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
            parameter = params[target_param]
            return FeatureControl(
                command_name=command_name,
                parameter_name=target_param,
                required_parameters=_get_required_parameters(params),
                parent_feature_name=api_feature_name,
                uri=command.get("uri", ""),
                value_type=parameter.get("type"),
                # A schedule is written as one object, so the scalar
                # constraints (min, max, step, options) do not apply.
                schedule=_build_schedule_constraints(parameter.get("constraints", {})),
            )
    return None


def _build_schedule_constraints(
    constraints: dict[str, Any],
) -> ScheduleConstraints | None:
    """Return the schedule rules of a command parameter, if it reports any."""
    # The constraint shapes were validated by validate_feature_entry.
    schedule_constraints = ScheduleConstraints(
        max_entries=constraints.get("maxEntries"),
        modes=constraints.get("modes"),
        resolution=constraints.get("resolution"),
        overlap_allowed=constraints.get("overlapAllowed"),
        default_mode=constraints.get("defaultMode"),
    )
    if schedule_constraints == ScheduleConstraints():
        return None
    return schedule_constraints


# API response parsing: validated construction of the public models from raw
# discovery, command, and event responses. The models themselves stay plain
# data classes.


def _require_identifier(data: dict[str, Any], field_name: str, resource: str) -> str:
    """Return a required string or integer API identifier as text."""
    value = data.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (str, int)) or value == "":
        raise ViResponseError(f"{resource} {field_name} must be a string or integer")
    return str(value)


def _require_string(data: dict[str, Any], field_name: str, resource: str) -> str:
    """Return a required non-empty text API field."""
    value = data.get(field_name)
    if not isinstance(value, str) or not value:
        raise ViResponseError(f"{resource} {field_name} must be a non-empty string")
    return value


def _optional_string(data: dict[str, Any], field_name: str, resource: str) -> str:
    """Return an optional text API field while rejecting malformed values."""
    value = data.get(field_name, "")
    if not isinstance(value, str):
        raise ViResponseError(f"{resource} {field_name} must be a string")
    return value


def _optional_text(
    data: Mapping[str, object], field_name: str, resource: str
) -> str | None:
    """Return an optional text API field; an absent or null field is `None`.

    Raises:
        ViResponseError: If a supplied value is not a string.
    """
    value = data.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ViResponseError(f"{resource} {field_name} must be a string")
    return value


def _parse_address(data: dict[str, Any]) -> dict[str, JsonValue]:
    """Return an optional installation address as a validated JSON object."""
    address = validate_json_value(data.get("address", {}), path="Installation address")
    if not isinstance(address, dict):
        raise ViResponseError("Installation address must be an object")
    return address


def parse_installation(data: dict[str, Any]) -> Installation:
    """Create Installation from API data.

    Args:
        data: The JSON dictionary representing an installation.

    Returns:
        The Installation parsed from the API data.

    Raises:
        ViResponseError: If a known installation field or address is malformed.
    """
    return Installation(
        id=_require_identifier(data, "id", "Installation"),
        description=_optional_string(data, "description", "Installation"),
        alias=_optional_string(data, "alias", "Installation"),
        address=_parse_address(data),
    )


def parse_gateway(data: dict[str, Any]) -> Gateway:
    """Create Gateway from API data.

    Args:
        data: The JSON dictionary representing a gateway.

    Returns:
        The Gateway parsed from the API data.

    Raises:
        ViResponseError: If a known gateway identity or text field is malformed.
    """
    return Gateway(
        serial=_require_string(data, "serial", "Gateway"),
        version=_optional_string(data, "version", "Gateway"),
        status=_optional_string(data, "status", "Gateway"),
        installation_id=_require_identifier(data, "installationId", "Gateway"),
    )


def parse_device(
    data: dict[str, Any], gateway_serial: str, installation_id: str
) -> Device:
    """Create Device from API data.

    Args:
        data: The dictionary returned by the API for a device.
        gateway_serial: Serial number of the gateway this device belongs to.
        installation_id: ID of the installation this device belongs to.

    Returns:
        A new Device instance.

    Raises:
        ViResponseError: If a known device identity or text field is malformed.
    """
    return Device(
        id=_require_identifier(data, "id", "Device"),
        gateway_serial=gateway_serial,
        installation_id=installation_id,
        model_id=_require_string(data, "modelId", "Device"),
        device_type=_require_string(data, "deviceType", "Device"),
        status=_optional_string(data, "status", "Device"),
    )


def _parse_command_success(value: object) -> bool:
    """Return a normalized command success flag.

    The API reports success as a JSON boolean or one of the documented
    case-insensitive boolean string representations.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise ViResponseError(
        "Command response success must be a boolean or a 'true'/'false' string"
    )


def parse_command_response(data: dict[str, Any]) -> CommandResponse:
    """Create CommandResponse from API data.

    Args:
        data: The JSON response dictionary from the API, either as the
            root object or wrapped in a ``data`` object.

    Returns:
        A CommandResponse instance indicating success/failure.

    Raises:
        ViResponseError: If a known command response field violates the
            API contract.
    """
    root = data.get("data", data)
    if not isinstance(root, dict):
        raise ViResponseError("Command response data must be an object")
    # The container shape was runtime-checked; every known field is
    # validated individually below.
    command_data = cast("dict[str, Any]", root)
    return CommandResponse(
        success=_parse_command_success(command_data.get("success")),
        message=_optional_text(command_data, "message", "Command response"),
        reason=_optional_text(command_data, "reason", "Command response"),
    )


def parse_installation_event(data: dict[str, Any]) -> InstallationEvent:
    """Create InstallationEvent from API data.

    The complete event is validated against the JSON value contract and
    unknown fields remain available through ``fields``. The ``body`` is
    kept exactly as reported because its structure depends on the event
    type; the provider's narrow schema examples must not reject valid
    event data.

    Args:
        data: The JSON dictionary representing one event.

    Returns:
        The InstallationEvent parsed from the API data.

    Raises:
        ViResponseError: If the event contains non-JSON data or a known
            event field is malformed.
    """
    fields = validate_json_value(data, path="Event")
    if not isinstance(fields, dict):
        raise ViResponseError("Event must be an object")
    # Documented contract fields without a model attribute are only
    # validated; their values stay available through ``fields``.
    for field_name in ("editedBy", "origin"):
        _optional_text(fields, field_name, "Event")
    audiences = fields.get("audiences")
    if audiences is not None and (
        not isinstance(audiences, list)
        or not all(isinstance(audience, str) for audience in audiences)
    ):
        raise ViResponseError("Event audiences must be a list of strings")
    return InstallationEvent(
        event_type=_require_string(fields, "eventType", "Event"),
        created_at=_require_string(fields, "createdAt", "Event"),
        event_timestamp=_require_string(fields, "eventTimestamp", "Event"),
        gateway_serial=_optional_text(fields, "gatewaySerial", "Event"),
        body=fields.get("body"),
        fields=fields,
    )


def _parse_next_cursor(data: dict[str, Any]) -> str | None:
    """Return the event history continuation cursor, if another page exists.

    Raises:
        ViResponseError: If the cursor object or its ``next`` value is malformed.
    """
    cursor = data.get("cursor")
    if cursor is None:
        return None
    if not isinstance(cursor, dict):
        raise ViResponseError("Event history cursor must be an object")
    next_cursor = cast("dict[str, Any]", cursor).get("next")
    if next_cursor is not None and not isinstance(next_cursor, str):
        raise ViResponseError("Event history cursor next must be a string")
    # The provider reports the final page with an empty string.
    return next_cursor or None


def parse_event_history_page(data: dict[str, Any]) -> EventHistoryPage:
    """Create EventHistoryPage from the event history API response.

    Args:
        data: The JSON dictionary returned by the event history endpoint,
            with a ``data`` list and an optional ``cursor`` object.

    Returns:
        The EventHistoryPage parsed from the API data.

    Raises:
        ViResponseError: If the response, an event, or the cursor violates
            the API contract.
    """
    events_data = data.get("data")
    if not isinstance(events_data, list):
        raise ViResponseError("Event history response data must be a list")
    # Runtime-checked containers from the transport boundary; every entry
    # is re-validated field by field by the event parser.
    events: list[InstallationEvent] = []
    for entry in cast("list[object]", events_data):
        if not isinstance(entry, dict):
            raise ViResponseError("Event history data entries must be objects")
        events.append(parse_installation_event(cast("dict[str, Any]", entry)))
    return EventHistoryPage(events=events, next_cursor=_parse_next_cursor(data))
