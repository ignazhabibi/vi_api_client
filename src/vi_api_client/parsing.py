"""Parsing logic for Viessmann API responses and features."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from datetime import date
from math import isfinite
from typing import Any, NamedTuple, cast

from ._types import JsonValue
from .exceptions import ViResponseError
from .models import (
    CommandMap,
    CommandParameter,
    Device,
    DeviceCommand,
    EventHistoryPage,
    Feature,
    FeatureConstraints,
    FeatureDeprecation,
    Gateway,
    Installation,
    InstallationEvent,
    OnOffWriteCommand,
    ParameterWriteCommand,
    ScheduleConstraints,
    WriteCommand,
    command_map_of,
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


class _CommandPair(NamedTuple):
    """One command family that switches a boolean property on and off."""

    api_feature_pattern: re.Pattern[str]
    property_key: str
    on_command_name: str
    off_command_name: str


# Boolean properties some devices can only change through two commands
# without parameters. The table is explicit because command names alone are
# not reliable: 'heating.dhw.hygiene' reports 'activate' without a
# counterpart, and other heating programs report an 'activate'/'deactivate'
# pair that no device allows.
_COMMAND_PAIRS = (
    _CommandPair(
        re.compile(r"heating\.dhw\.oneTimeCharge"), "active", "activate", "deactivate"
    ),
    _CommandPair(re.compile(r"heating\.dhw\.hygiene"), "enabled", "enable", "disable"),
    _CommandPair(
        re.compile(r"heating\.circuits\.\d+\.operating\.programs\.comfort"),
        "active",
        "activate",
        "deactivate",
    ),
    _CommandPair(
        re.compile(r"heating\.circuits\.\d+\.operating\.programs\.eco"),
        "active",
        "activate",
        "deactivate",
    ),
    _CommandPair(
        re.compile(
            r"heating\.circuits\.\d+\.operating\.programs\.forcedLastFromSchedule"
        ),
        "active",
        "activate",
        "deactivate",
    ),
    _CommandPair(
        re.compile(r"ventilation\.quickmodes\.[^.]+"),
        "active",
        "activate",
        "deactivate",
    ),
)


class ValidatedApiFeature(NamedTuple):
    """The known fields of one API feature after validation."""

    name: str
    properties: dict[str, JsonValue]
    commands: dict[str, Any]
    is_enabled: bool
    is_ready: bool
    deprecation: FeatureDeprecation | None = None


class _CommandWrite(NamedTuple):
    """The internal write command and public rules of one writable feature."""

    write_command: WriteCommand
    constraints: FeatureConstraints


class CommandOutcome(NamedTuple):
    """The validated outcome of one command response, internal to the library."""

    success: bool
    message: str | None
    reason: str | None


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
    return _entry_to_flat_features(entry)


def _entry_to_flat_features(entry: ValidatedApiFeature) -> list[Feature]:
    """Return the flat features of one already validated API feature."""
    api_feature_name, properties = entry.name, entry.properties

    schedule_entries = properties.get("entries")
    if (
        isinstance(schedule_entries, dict)
        and schedule_entries.get("type") == "Schedule"
    ):
        schedule_write = _find_complex_write(api_feature_name, entry.commands)
        schedule_feature = Feature(
            name=api_feature_name,
            value=schedule_entries.get("value"),
            unit=None,
            is_enabled=entry.is_enabled,
            is_ready=entry.is_ready,
            constraints=schedule_write.constraints if schedule_write else None,
            _write_command=schedule_write.write_command if schedule_write else None,
            deprecation=entry.deprecation,
        )
        other_properties = {
            key: value for key, value in properties.items() if key != "entries"
        }
        return [schedule_feature, *_flat_property_features(entry, other_properties)]

    if not set(properties).isdisjoint(COMPLEX_DATA_INDICATORS):
        write = _find_complex_write(api_feature_name, entry.commands)
        features = [
            Feature(
                name=api_feature_name,
                value=properties,
                unit=None,
                is_enabled=entry.is_enabled,
                is_ready=entry.is_ready,
                constraints=write.constraints if write else None,
                _write_command=write.write_command if write else None,
                deprecation=entry.deprecation,
            )
        ]
        features.extend(
            _build_consumption_alias_features(
                api_feature_name=api_feature_name,
                properties=properties,
                is_enabled=entry.is_enabled,
                is_ready=entry.is_ready,
                deprecation=entry.deprecation,
            )
        )
        return features

    return _flat_property_features(entry, properties)


def parse_api_features(
    api_features: list[dict[str, Any]],
    *,
    only_available: bool,
    feature_names: Collection[str] | None = None,
) -> tuple[list[Feature], CommandMap]:
    """Parse one device feature response into flat features and commands.

    Every API feature is validated once. The command list keeps commands the
    parser binds to no feature and commands of API features without
    properties; when ``only_available`` is set, the commands keep only
    enabled and ready API features. The commands store neither
    ``isExecutable`` nor ``isDeprecated``.

    Args:
        api_features: The raw JSON objects of one device's API features.
        only_available: Whether the commands of unavailable API features are
            filtered out locally, mirroring the read's skip hints.
        feature_names: Optional names that select features by their own name
            or by the name of the API feature they were parsed from. Without
            names every feature is kept. The command list is not selected.

    Returns:
        The flat features and the reported commands keyed by API feature name.

    Raises:
        ViResponseError: If an API feature is malformed or the same command
            name is reported twice for one API feature.
    """
    features: list[Feature] = []
    command_map: dict[str, tuple[DeviceCommand, ...]] = {}
    available_api_features: set[str] = set()
    requested_names = set(feature_names or ())
    for api_feature in api_features:
        entry = validate_feature_entry(api_feature)
        entry_features = _entry_to_flat_features(entry)
        if requested_names and entry.name not in requested_names:
            entry_features = [
                feature for feature in entry_features if feature.name in requested_names
            ]
        features.extend(entry_features)
        device_commands = _entry_commands(entry)
        if entry.name in command_map:
            existing = command_map[entry.name]
            duplicates = sorted(
                {command.name for command in existing}
                & {command.name for command in device_commands}
            )
            if duplicates:
                raise ViResponseError(
                    f"Duplicate API feature command: {entry.name}.{duplicates[0]}"
                )
            device_commands = existing + device_commands
        command_map[entry.name] = device_commands
        if entry.is_enabled and entry.is_ready:
            available_api_features.add(entry.name)

    if only_available:
        command_map = {
            api_feature_name: commands
            for api_feature_name, commands in command_map.items()
            if api_feature_name in available_api_features
        }
    return features, command_map


def owning_api_feature_name(device: Device, feature_name: str) -> str | None:
    """Return the API feature of a device snapshot that one feature belongs to.

    The longest API feature name the feature name equals or starts with,
    followed by a dot, owns the feature. Only API features the snapshot's
    command list records are candidates, and a snapshot without one owns
    nothing.
    """
    return max(
        (
            name
            for name in command_map_of(device)
            if feature_name == name or feature_name.startswith(f"{name}.")
        ),
        key=len,
        default=None,
    )


def _entry_commands(entry: ValidatedApiFeature) -> tuple[DeviceCommand, ...]:
    """Return the internal metadata of every command of one API feature."""
    commands: list[DeviceCommand] = []
    for command_name, command in entry.commands.items():
        # Parameter containers were validated by validate_feature_entry.
        params = cast("dict[str, Any]", command.get("params", {}))
        parameters = tuple(
            CommandParameter(
                name=parameter_name,
                required=parameter.get("required"),
                constraints=_parameter_constraints(parameter),
            )
            for parameter_name, parameter in params.items()
        )
        commands.append(
            DeviceCommand(
                name=command_name, uri=command.get("uri", ""), parameters=parameters
            )
        )
    return tuple(commands)


def _parameter_constraints(parameter: dict[str, Any]) -> FeatureConstraints | None:
    """Return the type and constraint rules of one command parameter, if any."""
    # The constraint shapes were validated by validate_feature_entry.
    constraints = cast("dict[str, Any]", parameter.get("constraints", {}))
    built = FeatureConstraints(
        value_type=parameter.get("type"),
        min=constraints.get("min"),
        max=constraints.get("max"),
        step=constraints.get("step", constraints.get("stepping")),
        options=constraints.get("enum"),
        min_length=constraints.get("minLength"),
        max_length=constraints.get("maxLength"),
        pattern=constraints.get("pattern", constraints.get("regEx")),
        schedule=_build_schedule_constraints(constraints),
    )
    if built == FeatureConstraints():
        return None
    return built


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
        write = _find_write(
            key, entry.commands, entry.name, property_data
        ) or _find_pair_write(key, value, entry)
        features.append(
            Feature(
                name=feature_name,
                value=value,
                unit=unit,
                is_enabled=entry.is_enabled,
                is_ready=entry.is_ready,
                constraints=write.constraints if write else None,
                _write_command=write.write_command if write else None,
                deprecation=entry.deprecation,
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
            flags, unit, or deprecation notice violate the API contract.
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
        api_feature_name,
        properties,
        commands,
        is_enabled,
        is_ready,
        _parse_deprecation(api_feature.get("deprecated")),
    )


# The removal date of a deprecation notice: a plain calendar date.
_ISO_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse_deprecation(value: object) -> FeatureDeprecation | None:
    """Return the removal notice of one API feature, if it reports one.

    The info text is passed on verbatim; the library neither reads a
    replacement name out of it nor maps old feature names to new ones.

    Raises:
        ViResponseError: If the notice is not an object, a known field is not
            a string, or the removal date is not an ISO calendar date.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ViResponseError("Feature deprecated must be an object")
    # Runtime-checked container; the two known fields are validated below
    # and unknown additional keys stay allowed.
    notice = cast("dict[str, Any]", value)
    removal_date_text = notice.get("removalDate")
    info = notice.get("info")
    if removal_date_text is not None and not isinstance(removal_date_text, str):
        raise ViResponseError("Feature deprecated.removalDate must be a string")
    if info is not None and not isinstance(info, str):
        raise ViResponseError("Feature deprecated.info must be a string")
    removal_date: date | None = None
    if removal_date_text is not None:
        try:
            if not _ISO_DATE_PATTERN.fullmatch(removal_date_text):
                raise ValueError(removal_date_text)
            removal_date = date.fromisoformat(removal_date_text)
        except ValueError as error:
            raise ViResponseError(
                "Feature deprecated.removalDate must be an ISO date (YYYY-MM-DD)"
            ) from error
    return FeatureDeprecation(removal_date=removal_date, info=info)


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
        deprecated = command.get("isDeprecated")
        if deprecated is not None and not isinstance(deprecated, bool):
            raise ViResponseError("Feature command isDeprecated must be a boolean")
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
    """Validate known property constraint metadata used to build feature constraints."""
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
    deprecation: FeatureDeprecation | None,
) -> list[Feature]:
    """Build synthetic currentDay and currentYear aliases for consumption metrics.

    Each alias mirrors the first entry of its time series, so day and year
    counters come from the same source as the series itself.

    Args:
        api_feature_name: The name of the API feature.
        properties: The raw properties dictionary of the feature.
        is_enabled: Whether the feature is enabled.
        is_ready: Whether the feature is ready.
        deprecation: The removal notice of the source API feature, if any.

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
                deprecation=deprecation,
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


def _find_write(
    property_key: str,
    commands: dict[str, Any],
    api_feature_name: str,
    property_data: Any = None,
) -> _CommandWrite | None:
    """Find the executable setter whose parameter writes the given property.

    Args:
        property_key: The property key (e.g. 'slope').
        commands: The API feature's commands.
        api_feature_name: The name of the API feature owning the property.
        property_data: Optional property metadata for constraint fallback.

    Returns:
        The setter's internal write command and its constraint rules, if a
        matching setter is found, else None.
    """
    for command_name, command in commands.items():
        if not _is_setter(command_name) or not command.get("isExecutable", True):
            continue

        target_param = _match_parameter(
            property_key, command.get("params", {}), command_name
        )
        if target_param:
            return _build_write(
                command_name, command, target_param, api_feature_name, property_data
            )

    return None


def _is_setter(command_name: str) -> bool:
    """Return whether a command only stores a value.

    Only a command whose name starts with ``set`` binds a value to a
    feature. Other commands also take parameters that match properties but
    do more than store a value: ``triggerDaily`` turns a weekly hygiene run
    into a daily one, ``activate`` on the daylight saving feature switches
    it on, and ``schedule`` plans a holiday. They stay reachable through
    `ViClient.run_command`, where the caller names them. This prefix is the
    only place where a command name decides something.
    """
    return command_name.startswith("set")


def _find_pair_write(
    property_key: str, value: JsonValue, entry: ValidatedApiFeature
) -> _CommandWrite | None:
    """Find the on/off command pair that writes a boolean property.

    Only consulted when no parameter command writes the property, so a
    setter such as ``setEnabled`` always wins. The reported ``isExecutable``
    flags are not part of the rule: they follow the device state, and a
    feature must not change between writable and read-only from one refresh
    to the next. The API rejects a command the device does not allow.

    Args:
        property_key: The property key (e.g. 'active').
        value: The property's reported value.
        entry: The validated API feature owning the property.

    Returns:
        The pair's internal write command with boolean constraint rules, if
        a table entry applies, else None.
    """
    if not isinstance(value, bool):
        return None
    for pair in _COMMAND_PAIRS:
        if pair.property_key != property_key or not (
            pair.api_feature_pattern.fullmatch(entry.name)
        ):
            continue
        on_command = entry.commands.get(pair.on_command_name)
        off_command = entry.commands.get(pair.off_command_name)
        if on_command is None or off_command is None:
            return None
        if _get_required_parameters(
            on_command.get("params", {})
        ) or _get_required_parameters(off_command.get("params", {})):
            return None
        write_command = OnOffWriteCommand(
            api_feature_name=entry.name,
            on_command_name=pair.on_command_name,
            off_command_name=pair.off_command_name,
            on_uri=on_command.get("uri", ""),
            off_uri=off_command.get("uri", ""),
        )
        return _CommandWrite(write_command, FeatureConstraints(value_type="boolean"))
    return None


def _build_write(
    command_name: str,
    command: dict[str, Any],
    target_param: str,
    api_feature_name: str,
    property_data: Any,
) -> _CommandWrite:
    """Build the write command and constraint rules of one command."""
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

    write_command = ParameterWriteCommand(
        api_feature_name=api_feature_name,
        command_name=command_name,
        parameter_name=target_param,
        required_parameters=_get_required_parameters(params),
        uri=command.get("uri", ""),
    )
    constraints = FeatureConstraints(
        min=_resolve_constraint(["min"], sources),
        max=_resolve_constraint(["max"], sources),
        step=_resolve_constraint(["step", "stepping"], sources),
        value_type=parameter.get("type"),
        options=_resolve_constraint(["enum"], sources),
        min_length=_resolve_constraint(["minLength"], sources),
        max_length=_resolve_constraint(["maxLength"], sources),
        pattern=_resolve_constraint(["pattern", "regEx"], sources),
    )
    return _CommandWrite(write_command, constraints)


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


def _find_complex_write(
    api_feature_name: str, commands: dict[str, Any]
) -> _CommandWrite | None:
    """Find the executable setter that writes a complex feature (e.g. schedule).

    Args:
        api_feature_name: The name of the API feature.
        commands: The API feature's commands.

    Returns:
        The write command and constraint rules if a matching setter is found.
    """
    for command_name, command in commands.items():
        if not _is_setter(command_name) or not command.get("isExecutable", True):
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
            write_command = ParameterWriteCommand(
                api_feature_name=api_feature_name,
                command_name=command_name,
                parameter_name=target_param,
                required_parameters=_get_required_parameters(params),
                uri=command.get("uri", ""),
            )
            constraints = FeatureConstraints(
                value_type=parameter.get("type"),
                # A schedule is written as one object, so the scalar
                # constraints (min, max, step, options) do not apply.
                schedule=_build_schedule_constraints(parameter.get("constraints", {})),
            )
            return _CommandWrite(write_command, constraints)
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


def _optional_string_list(
    data: Mapping[str, object], field_name: str, resource: str
) -> tuple[str, ...]:
    """Return an optional list of strings unchanged and in order.

    An absent or null field is empty.

    Raises:
        ViResponseError: If a supplied value is not a list of strings.
    """
    value = data.get(field_name)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ViResponseError(f"{resource} {field_name} must be a list of strings")
    items = cast("list[object]", value)
    strings = tuple(item for item in items if isinstance(item, str))
    if len(strings) != len(items):
        raise ViResponseError(f"{resource} {field_name} must be a list of strings")
    return strings


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
        roles=_optional_string_list(data, "roles", "Device"),
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


def parse_command_outcome(data: dict[str, Any]) -> CommandOutcome:
    """Return the validated outcome of one command response.

    Args:
        data: The JSON response dictionary from the API, either as the
            root object or wrapped in a ``data`` object.

    Returns:
        The command outcome indicating success and its reported details.

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
    return CommandOutcome(
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
