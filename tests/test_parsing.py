"""Tests for parsing API responses and features into models and flat features."""

from typing import Any

import pytest
from builders import load_fixture_features, load_fixture_json

from vi_api_client import JsonValue
from vi_api_client.exceptions import ViResponseError
from vi_api_client.models import (
    CommandParameter,
    Device,
    FeatureConstraints,
    ParameterWriteBinding,
    ScheduleConstraints,
)
from vi_api_client.parsing import (
    api_feature_to_flat_features,
    parse_api_features,
    parse_command_outcome,
    parse_device,
)


def test_command_mappings_with_non_string_keys_are_rejected():
    """Programmatic mappings with non-string command keys are rejected."""
    # Arrange: Build a feature entry whose command mapping uses an integer key.
    raw_feature = {
        "feature": "heating.curve",
        "properties": {"slope": {"value": 1.0}},
        "commands": {5: {"uri": "/commands/setCurve"}},
    }

    # Act and assert: The malformed command metadata raises a response error.
    with pytest.raises(ViResponseError, match="named objects"):
        api_feature_to_flat_features(raw_feature)


@pytest.mark.parametrize(
    "fixture_name",
    [
        pytest.param("parsing/simple_value.json", id="unit-on-value-property"),
        pytest.param("parsing/feature_level_unit.json", id="unit-on-feature"),
    ],
)
def test_value_property_becomes_the_base_feature_with_its_unit(fixture_name: str):
    """The value property keeps the API feature name and the declared unit."""
    # Arrange: The unit is declared either on the value or for the whole feature.
    data = load_fixture_json(fixture_name)

    # Act: Parse the sensor feature.
    features = api_feature_to_flat_features(data)

    # Assert: One feature carries the reading under the API feature name.
    assert len(features) == 1
    feature = features[0]
    assert feature.name == "heating.sensors.temperature.outside"
    assert feature.is_enabled is True
    assert feature.value == 5.5
    assert feature.unit == "celsius"


def test_control_requires_parameters_unless_marked_optional():
    """Command controls include true and unspecified parameters but exclude false."""
    # Arrange: The command has explicit required, explicit optional, and unspecified params.
    raw_feature = {
        "feature": "heating.mode",
        "properties": {"target": {"value": "old"}},
        "commands": {
            "setMode": {
                "uri": "/commands/setMode",
                "params": {
                    "target": {"required": False},
                    "requiredSibling": {"required": True},
                    "unspecifiedSibling": {},
                    "optionalSibling": {"required": False},
                },
            }
        },
    }

    # Act: Parse the API-shaped feature response.
    feature = api_feature_to_flat_features(raw_feature)[0]

    # Assert: Explicit false is optional while missing markers remain conservative.
    assert isinstance(feature.write_binding, ParameterWriteBinding)
    assert feature.write_binding.required_parameters == (
        "requiredSibling",
        "unspecifiedSibling",
    )


def test_status_property_becomes_a_suffixed_feature():
    """A status property is exposed under a .status feature name."""
    # Arrange: Load a circulation pump feature with only a status property.
    data = load_fixture_json("parsing/status_feature.json")

    # Act: Parse the pump feature.
    features = api_feature_to_flat_features(data)

    # Assert: The status keeps its text value under the suffixed name.
    assert len(features) == 1
    feature = features[0]
    assert feature.name == "heating.circuits.0.circulation.pump.status"
    assert feature.value == "off"


def test_scalar_properties_become_separate_features():
    """Several scalar properties become separate flat features."""
    # Arrange: Load a feature with two scalar properties, one with a unit.
    data = load_fixture_json("parsing/nested_expansion.json")

    # Act: Parse the feature.
    features = api_feature_to_flat_features(data)

    # Assert: Each property is its own feature with its own value and unit.
    assert len(features) == 2

    feature_a = next(feature for feature in features if feature.name.endswith(".propA"))
    assert feature_a.value == 10

    feature_b = next(feature for feature in features if feature.name.endswith(".propB"))
    assert feature_b.value == 20
    assert feature_b.unit == "C"


def test_active_property_becomes_a_boolean_feature():
    """An 'active' property keeps its JSON boolean instead of becoming text."""
    # Arrange: Load a feature whose only property is a boolean 'active' flag.
    data = load_fixture_json("parsing/active_feature.json")

    # Act: Parse the feature.
    features = api_feature_to_flat_features(data)

    # Assert: The flag is a real boolean under the suffixed name.
    assert len(features) == 1
    feature = features[0]
    assert feature.name == "heating.circuits.0.operating.modes.active.active"
    assert feature.value is True


def test_history_series_stay_one_feature_with_their_whole_value():
    """History arrays stay one feature with their whole value."""
    # Arrange: Load a consumption feature with a daily history series.
    data = load_fixture_json("parsing/history_array.json")

    # Act: Parse the feature.
    features = api_feature_to_flat_features(data)

    # Assert: Consumers can read the series without reassembling it.
    assert len(features) == 1
    feature = features[0]
    assert feature.name == "heating.power.consumption"
    assert isinstance(feature.value, dict)
    day = feature.value["day"]
    assert isinstance(day, dict)
    values = day["value"]
    assert isinstance(values, list)
    assert values == [1.1, 2.2, 3.3]


@pytest.mark.parametrize(
    ("fixture_name", "base_name"),
    [
        pytest.param(
            "parsing/consumption_alias_cooling.json",
            "heating.power.consumption.cooling",
            id="cooling",
        ),
        pytest.param(
            "parsing/consumption_alias_dhw.json",
            "heating.power.consumption.dhw",
            id="dhw",
        ),
        pytest.param(
            "parsing/consumption_alias_heating.json",
            "heating.power.consumption.heating",
            id="heating",
        ),
        pytest.param(
            "parsing/consumption_alias_total.json",
            "heating.power.consumption.total",
            id="total",
        ),
    ],
)
def test_consumption_series_expose_current_day_and_year_aliases(
    fixture_name: str, base_name: str
):
    """Consumption series expose the first day and year values as aliases."""
    # Arrange: Load the consumption fixture with day, month, and year arrays.
    data = load_fixture_json(fixture_name)

    # Act: Parse the consumption feature.
    features = api_feature_to_flat_features(data)

    # Assert: The base series and only the currentDay and currentYear aliases
    # are available, each mirroring the first entry of its series.
    features_by_name = {feature.name: feature for feature in features}
    assert set(features_by_name) == {
        base_name,
        f"{base_name}.currentDay",
        f"{base_name}.currentYear",
    }

    series = features_by_name[base_name].value
    assert isinstance(series, dict)
    for alias_name, source_name in (("currentDay", "day"), ("currentYear", "year")):
        source = series[source_name]
        assert isinstance(source, dict)
        source_values = source["value"]
        assert isinstance(source_values, list)
        alias_feature = features_by_name[f"{base_name}.{alias_name}"]
        assert alias_feature.value == source_values[0]
        assert alias_feature.unit == "kilowattHour"
        assert alias_feature.write_binding is None


def test_bundled_fixture_exposes_current_day_aliases():
    """A real device export yields currentDay aliases for populated day series."""
    # Act: Parse every feature of the bundled Vitocal250A export.
    features = load_fixture_features("Vitocal250A")

    # Assert: Only consumption series with a day list get a currentDay alias.
    assert {
        feature.name: (feature.value, feature.unit)
        for feature in features
        if feature.name.endswith(".currentDay")
        and feature.name.startswith("heating.power.consumption.")
        and ".summary." not in feature.name
    } == {
        "heating.power.consumption.dhw.currentDay": (3.3, "kilowattHour"),
        "heating.power.consumption.heating.currentDay": (0, "kilowattHour"),
        "heating.power.consumption.total.currentDay": (2.3, "kilowattHour"),
    }


def test_value_property_keeps_the_base_name_beside_a_suffixed_status():
    """The value property keeps the base name; status gets a suffix."""
    # Arrange: Load a feature with both 'value' and 'status' properties.
    data = load_fixture_json("parsing/mixed_feature.json")

    # Act: Parse the feature.
    features = api_feature_to_flat_features(data)

    # Assert: Both properties stay addressable without a name collision.
    assert {feature.name: feature.value for feature in features} == {
        "mixed.feature": 42,
        "mixed.feature.status": "error",
    }


def test_commands_become_controls_of_the_properties_they_write():
    """Commands become controls of the properties they write."""
    # Arrange: Load a curve feature whose one command writes slope and shift.
    data = load_fixture_json("parsing/feature_with_commands.json")

    # Act: Parse the feature.
    features = api_feature_to_flat_features(data)

    # Assert: Each property is written through its own command parameter, and
    # the sibling parameter is required because the command takes both.
    assert len(features) == 2

    feature_slope = next(
        feature for feature in features if feature.name.endswith(".slope")
    )
    assert isinstance(feature_slope.write_binding, ParameterWriteBinding)
    assert feature_slope.write_binding.command_name == "setCurve"
    assert feature_slope.write_binding.parameter_name == "slope"
    assert feature_slope.constraints is not None
    assert feature_slope.constraints.value_type == "number"
    assert "shift" in feature_slope.write_binding.required_parameters

    feature_shift = next(
        feature for feature in features if feature.name.endswith(".shift")
    )
    assert isinstance(feature_shift.write_binding, ParameterWriteBinding)
    assert feature_shift.write_binding.command_name == "setCurve"
    assert feature_shift.write_binding.parameter_name == "shift"


def test_hysteresis_commands_create_writable_switch_point_features():
    """Hysteresis value and switch points become separate writable features."""
    # Arrange: Load the fixture with hysteresis value and switch point commands.
    raw_feature = load_fixture_json("parsing/hysteresis_raw.json")

    # Act: Parse the hysteresis feature with its command-linked properties.
    features = api_feature_to_flat_features(raw_feature)

    # Assert: The base value and both switch points are writable with their commands.
    assert {
        feature.name: (
            feature.write_binding.command_name
            if isinstance(feature.write_binding, ParameterWriteBinding)
            else None
        )
        for feature in features
    } == {
        "heating.dhw.temperature.hysteresis": "setHysteresis",
        "heating.dhw.temperature.hysteresis.switchOnValue": (
            "setHysteresisSwitchOnValue"
        ),
        "heating.dhw.temperature.hysteresis.switchOffValue": (
            "setHysteresisSwitchOffValue"
        ),
    }


def test_scalar_min_max_are_metadata_not_features():
    """Scalar min/max properties should be skipped during flattening."""
    # Arrange: Build a feature with scalar min/max metadata around the value.
    raw_feature = {
        "feature": "heating.curve",
        "properties": {"min": 5, "max": 10, "value": 3},
    }

    # Act: Parse the feature.
    features = api_feature_to_flat_features(raw_feature)

    # Assert: Only the value flattens into a feature.
    assert len(features) == 1
    assert features[0].name == "heating.curve"
    assert features[0].value == 3


def test_object_shaped_min_max_become_features():
    """Object-shaped min/max properties should flatten into sub-features."""
    # Arrange: Build a feature with a nested min object beside the value.
    raw_feature = {
        "feature": "heating.curve",
        "properties": {"min": {"value": 1}, "value": 3},
    }

    # Act: Parse the feature.
    features = api_feature_to_flat_features(raw_feature)

    # Assert: The nested object flattens beside the base value.
    assert len(features) == 2
    assert {feature.name for feature in features} == {
        "heating.curve",
        "heating.curve.min",
    }


@pytest.mark.parametrize(
    ("alias_name", "source_name", "other_name"),
    [
        pytest.param("currentDay", "day", "year", id="current-day"),
        pytest.param("currentYear", "year", "day", id="current-year"),
    ],
)
@pytest.mark.parametrize(
    ("source_property", "expect_alias"),
    [
        pytest.param(None, False, id="source-absent"),
        pytest.param(
            {"type": "array", "unit": "kilowattHour", "value": []},
            False,
            id="source-empty",
        ),
        pytest.param(
            {"type": "array", "unit": "kilowattHour", "value": [5.5]},
            True,
            id="source-populated",
        ),
    ],
)
def test_consumption_alias_requires_a_populated_source_series(
    alias_name: str,
    source_name: str,
    other_name: str,
    source_property: dict | None,
    expect_alias: bool,
):
    """An alias should only mirror a populated source series."""
    # Arrange: Build a consumption feature with the given source property and
    # a populated sibling series.
    properties: dict = {
        other_name: {"type": "array", "unit": "kilowattHour", "value": [1.0]}
    }
    if source_property is not None:
        properties[source_name] = source_property
    raw_feature = {
        "feature": "heating.power.consumption.cooling",
        "properties": properties,
    }

    # Act: Parse the consumption feature.
    features = api_feature_to_flat_features(raw_feature)

    # Assert: The alias exists exactly when the source series is populated.
    alias_names = [
        feature.name
        for feature in features
        if feature.name == f"heating.power.consumption.cooling.{alias_name}"
    ]
    assert bool(alias_names) is expect_alias


def test_temperature_property_binds_target_temperature_command():
    """A temperature property should bind to a targetTemperature command."""
    # Arrange: Build a feature whose command parameter uses the alias spelling.
    raw_feature = {
        "feature": "heating.dhw.temperature.main",
        "properties": {"temperature": {"value": 45, "unit": "celsius"}},
        "commands": {
            "setTargetTemperature": {
                "uri": "/commands/setTargetTemperature",
                "params": {"targetTemperature": {"type": "number", "required": True}},
            }
        },
    }

    # Act: Parse the feature.
    features = api_feature_to_flat_features(raw_feature)

    # Assert: The temperature property exposes the aliased command control.
    assert len(features) == 1
    feature = features[0]
    assert feature.name == "heating.dhw.temperature.main.temperature"
    assert isinstance(feature.write_binding, ParameterWriteBinding)
    assert feature.write_binding.command_name == "setTargetTemperature"
    assert feature.write_binding.parameter_name == "targetTemperature"


def test_command_enum_becomes_the_control_options():
    """Command enum metadata should surface as the control's value options."""
    # Arrange: Build a writable feature whose parameter declares an enum.
    raw_feature = {
        "feature": "heating.mode",
        "properties": {"mode": {"value": "auto"}},
        "commands": {
            "setMode": {
                "uri": "/commands/setMode",
                "params": {
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "eco"],
                        "required": True,
                    }
                },
            }
        },
    }

    # Act: Parse the feature.
    features = api_feature_to_flat_features(raw_feature)

    # Assert: The enum members become the constraints' allowed options.
    assert len(features) == 1
    constraints = features[0].constraints
    assert constraints is not None
    assert list(constraints.options or []) == ["auto", "eco"]


def _curve_feature(
    commands: dict[str, dict], slope_property: dict | None = None
) -> dict:
    """Build one heating curve API feature whose commands may write the slope."""
    return {
        "feature": "heating.curve",
        "properties": {
            "slope": slope_property or {"type": "number", "value": 1.4},
        },
        "commands": {
            name: {"uri": f"/commands/{name}", **command}
            for name, command in commands.items()
        },
    }


def _schedule_feature(commands: dict[str, dict]) -> dict:
    """Build one heating schedule API feature with the given commands."""
    return {
        "feature": "heating.circuits.0.heating.schedule",
        "properties": {
            "active": {"type": "boolean", "value": True},
            "entries": {"type": "Schedule", "value": {"mon": []}},
        },
        "commands": {
            name: {"uri": f"/commands/{name}", **command}
            for name, command in commands.items()
        },
    }


def test_non_executable_commands_do_not_make_features_writable():
    """A command the API marks as not executable must not create a control."""
    # Arrange: The only command writing the slope is marked not executable.
    raw_feature = _curve_feature(
        {"setCurve": {"isExecutable": False, "params": {"slope": {"type": "number"}}}}
    )

    # Act: Parse the API feature.
    feature = api_feature_to_flat_features(raw_feature)[0]

    # Assert: The feature stays read-only.
    assert feature.write_binding is None
    assert feature.is_writable is False


def test_executable_command_is_chosen_over_an_earlier_non_executable_one():
    """A blocked command must not hide a later command that writes the same value."""
    # Arrange: Two commands write the slope; only the second one may be executed.
    raw_feature = _curve_feature(
        {
            "setCurveLocked": {
                "isExecutable": False,
                "params": {"slope": {"type": "number"}},
            },
            "setCurve": {"isExecutable": True, "params": {"slope": {"type": "number"}}},
        }
    )

    # Act: Parse the API feature.
    binding = api_feature_to_flat_features(raw_feature)[0].write_binding

    # Assert: The control writes through the executable command.
    assert isinstance(binding, ParameterWriteBinding)
    assert (binding.command_name, binding.parameter_name) == ("setCurve", "slope")
    assert binding.uri == "/commands/setCurve"


def test_command_parameter_constraints_win_over_property_metadata():
    """The command parameter is validated on write, so its constraints win."""
    # Arrange: Parameter and property disagree on every numeric constraint.
    raw_feature = _curve_feature(
        {
            "setCurve": {
                "params": {
                    "slope": {
                        "type": "number",
                        "constraints": {"min": 0.2, "max": 3.5, "stepping": 0.1},
                    }
                }
            }
        },
        slope_property={
            "type": "number",
            "value": 1.4,
            "constraints": {"min": 0, "max": 10, "step": 1},
        },
    )

    # Act: Parse the API feature.
    constraints = api_feature_to_flat_features(raw_feature)[0].constraints

    # Assert: The parameter constraints win, including the 'stepping' alias.
    assert constraints is not None
    assert (constraints.min, constraints.max, constraints.step) == (0.2, 3.5, 0.1)


def test_property_metadata_fills_missing_parameter_constraints():
    """Property metadata supplies constraints the command parameter omits."""
    # Arrange: Only the property carries text constraints, under the regEx alias.
    raw_feature = {
        "feature": "heating.circuits.0.name",
        "properties": {
            "name": {
                "type": "string",
                "value": "Circuit",
                "constraints": {"minLength": 1, "maxLength": 20, "regEx": "^[A-Z]"},
            }
        },
        "commands": {
            "setName": {"uri": "/commands/setName", "params": {"name": {}}},
        },
    }

    # Act: Parse the API feature.
    constraints = api_feature_to_flat_features(raw_feature)[0].constraints

    # Assert: The gaps are filled from the property, resolving the alias.
    assert constraints is not None
    assert (constraints.min_length, constraints.max_length) == (1, 20)
    assert constraints.pattern == "^[A-Z]"


def test_schedule_value_is_the_plan_and_its_status_is_a_flat_feature():
    """A schedule reads in the shape its command writes: the plan itself."""
    # Arrange: A schedule feature with a command writing the 'newSchedule' parameter.
    raw_feature = _schedule_feature(
        {
            "setSchedule": {
                "params": {
                    "newSchedule": {
                        "type": "Schedule",
                        "constraints": {"maxEntries": 4, "min": 0},
                    }
                },
            }
        }
    )

    # Act: Parse the API feature.
    features = api_feature_to_flat_features(raw_feature)

    # Assert: The plan is the schedule value; 'active' is a read-only feature.
    assert [feature.name for feature in features] == [
        "heating.circuits.0.heating.schedule",
        "heating.circuits.0.heating.schedule.active",
    ]
    schedule, active = features
    assert schedule.value == {"mon": []}
    assert active.value is True
    assert active.write_binding is None
    binding = schedule.write_binding
    constraints = schedule.constraints
    assert isinstance(binding, ParameterWriteBinding)
    assert constraints is not None
    assert binding.command_name == "setSchedule"
    assert binding.parameter_name == "newSchedule"
    assert constraints.value_type == "Schedule"
    assert (
        constraints.min,
        constraints.max,
        constraints.step,
        constraints.options,
    ) == (
        None,
        None,
        None,
        None,
    )


def test_schedule_control_carries_the_reported_schedule_rules():
    """The rules of the schedule command parameter become schedule constraints."""
    # Arrange: The schedule command reports all known schedule rules.
    raw_feature = _schedule_feature(
        {
            "setSchedule": {
                "params": {
                    "newSchedule": {
                        "type": "Schedule",
                        "constraints": {
                            "defaultMode": "off",
                            "maxEntries": 4,
                            "modes": ["on"],
                            "overlapAllowed": False,
                            "resolution": 10,
                        },
                    }
                },
            }
        }
    )

    # Act: Parse the API feature.
    constraints = api_feature_to_flat_features(raw_feature)[0].constraints

    # Assert: Every rule is available on the constraints.
    assert constraints is not None
    assert constraints.schedule == ScheduleConstraints(
        max_entries=4,
        modes=("on",),
        resolution=10,
        overlap_allowed=False,
        default_mode="off",
    )


def test_schedule_constraints_without_reported_rules_have_no_schedule_rules():
    raw_feature = _schedule_feature(
        {"setSchedule": {"params": {"newSchedule": {"type": "Schedule"}}}}
    )

    constraints = api_feature_to_flat_features(raw_feature)[0].constraints

    assert constraints is not None
    assert constraints.schedule is None


@pytest.mark.parametrize(
    ("constraints", "error"),
    [
        pytest.param({"maxEntries": "4"}, "maxEntries", id="max-entries-text"),
        pytest.param({"maxEntries": 0}, "maxEntries", id="max-entries-zero"),
        pytest.param({"resolution": True}, "resolution", id="resolution-boolean"),
        pytest.param({"modes": "on"}, "modes", id="modes-not-a-list"),
        pytest.param({"modes": ["on", 1]}, "modes", id="modes-not-text"),
        pytest.param({"overlapAllowed": "no"}, "overlapAllowed", id="overlap-text"),
        pytest.param({"defaultMode": 0}, "defaultMode", id="default-mode-number"),
    ],
)
def test_malformed_schedule_rules_are_rejected(constraints, error):
    raw_feature = _schedule_feature(
        {
            "setSchedule": {
                "params": {
                    "newSchedule": {"type": "Schedule", "constraints": constraints}
                }
            }
        }
    )

    with pytest.raises(ViResponseError, match=error):
        api_feature_to_flat_features(raw_feature)


@pytest.mark.parametrize(
    "command_names",
    [
        pytest.param(("activate", "setActive"), id="other-command-first"),
        pytest.param(("setActive", "activate"), id="setter-first"),
    ],
)
def test_setter_binds_the_value_whatever_the_command_order(command_names):
    """Only a command whose name starts with 'set' binds a value to a feature."""
    # Arrange: Two commands take the same parameters; only one is a setter.
    params = {
        "begin": {"type": "string", "required": True},
        "end": {"type": "string", "required": True},
    }
    raw_feature = {
        "feature": "device.time.daylightSaving",
        "properties": {
            "begin": {"type": "string", "value": "25-03"},
            "end": {"type": "string", "value": "25-10"},
        },
        "commands": {
            command_name: {"uri": f"/commands/{command_name}", "params": params}
            for command_name in command_names
        },
    }

    # Act: Parse the API feature.
    features = api_feature_to_flat_features(raw_feature)

    # Assert: Both values are written through the setter.
    for feature in features:
        binding = feature.write_binding
        assert isinstance(binding, ParameterWriteBinding)
        assert binding.command_name == "setActive"
        assert binding.uri == "/commands/setActive"


@pytest.mark.parametrize(
    ("api_feature_name", "command_name", "params", "properties"),
    [
        pytest.param(
            "heating.operating.programs.holiday",
            "schedule",
            {"start": {"type": "string"}, "end": {"type": "string"}},
            {
                "start": {"type": "string", "value": ""},
                "end": {"type": "string", "value": ""},
            },
            id="holiday-schedule",
        ),
        pytest.param(
            "heating.dhw.hygiene.trigger",
            "triggerDaily",
            {"startHour": {"type": "number"}, "startMinute": {"type": "number"}},
            {
                "startHour": {"type": "number", "value": 23},
                "startMinute": {"type": "number", "value": 30},
            },
            id="hygiene-trigger",
        ),
        pytest.param(
            "heating.circuits.0.operating.programs.comfort",
            "activate",
            {"temperature": {"type": "number", "required": False}},
            {"temperature": {"type": "number", "value": 20}},
            id="optional-activate-parameter",
        ),
    ],
)
def test_commands_that_are_no_setters_bind_no_value(
    api_feature_name, command_name, params, properties
):
    """A command that does more than store a value leaves its features read-only."""
    # Arrange: The only command taking the properties' parameters is no setter.
    raw_feature = {
        "feature": api_feature_name,
        "properties": properties,
        "commands": {
            command_name: {"uri": f"/commands/{command_name}", "params": params}
        },
    }

    # Act: Parse the API feature.
    features = api_feature_to_flat_features(raw_feature)

    # Assert: No feature is writable and none carries constraint rules.
    assert features
    for feature in features:
        assert not feature.is_writable
        assert feature.constraints is None


def test_schedule_is_bound_only_through_a_setter():
    """A schedule parameter on a command that is no setter binds nothing."""
    # Arrange: The schedule parameter is offered by a command that is no setter.
    raw_feature = _schedule_feature(
        {
            # A setter without a schedule parameter writes something else.
            "setMode": {"params": {"mode": {"type": "string"}}},
            "applySchedule": {"params": {"newSchedule": {"type": "Schedule"}}},
        }
    )

    # Act: Parse the API feature.
    schedule = api_feature_to_flat_features(raw_feature)[0]

    # Assert: The schedule stays read-only.
    assert not schedule.is_writable
    assert schedule.constraints is None


def test_non_executable_schedule_commands_do_not_make_schedules_writable():
    """Schedules follow the same executable rule as scalar features."""
    # Arrange: The only schedule command is marked not executable.
    raw_feature = _schedule_feature(
        {
            "setSchedule": {
                "isExecutable": False,
                "params": {"newSchedule": {"type": "Schedule"}},
            }
        }
    )

    # Act: Parse the API feature.
    feature = api_feature_to_flat_features(raw_feature)[0]

    # Assert: The schedule stays read-only.
    assert feature.write_binding is None


def test_executable_schedule_command_is_chosen_over_an_earlier_blocked_one():
    """A blocked schedule command must not hide a later executable one."""
    # Arrange: Two commands write the schedule; only the second may be executed.
    raw_feature = _schedule_feature(
        {
            "setScheduleLocked": {
                "isExecutable": False,
                "params": {"newSchedule": {"type": "Schedule"}},
            },
            "setSchedule": {
                "isExecutable": True,
                "params": {"newSchedule": {"type": "Schedule"}},
            },
        }
    )

    # Act: Parse the API feature.
    binding = api_feature_to_flat_features(raw_feature)[0].write_binding

    # Assert: The schedule control writes through the executable command.
    assert isinstance(binding, ParameterWriteBinding)
    assert (binding.command_name, binding.parameter_name) == (
        "setSchedule",
        "newSchedule",
    )
    assert binding.uri == "/commands/setSchedule"


def test_parse_device_maps_fields_and_converts_integer_ids_to_text():
    """API devices map to model fields, with numeric IDs exposed as text."""
    # Arrange: The API may report a device ID as a JSON integer.
    data = {
        "id": 0,
        "modelId": "complex_model",
        "deviceType": "heatpump",
        "status": "Online",
        "roles": ["type:product;Vitocal_250A", "type:E3", "type:heatpump"],
    }

    # Act: Parse the API device within its gateway and installation.
    device = parse_device(data, "gw1", "inst1")

    # Assert: Every field is mapped, the identifier is consistently text, and
    # the roles keep their text and API order.
    assert device == Device(
        id="0",
        gateway_serial="gw1",
        installation_id="inst1",
        model_id="complex_model",
        device_type="heatpump",
        status="Online",
        roles=("type:product;Vitocal_250A", "type:E3", "type:heatpump"),
    )


@pytest.mark.parametrize(
    "roles",
    [pytest.param(None, id="null"), pytest.param([], id="empty")],
)
def test_parse_device_without_roles_has_no_roles(roles):
    data = {"id": "0", "modelId": "m", "deviceType": "heating", "roles": roles}

    assert parse_device(data, "gw1", "inst1").roles == ()


def test_parse_device_without_a_roles_field_has_no_roles():
    data = {"id": "0", "modelId": "m", "deviceType": "heating"}

    assert parse_device(data, "gw1", "inst1").roles == ()


@pytest.mark.parametrize(
    "roles",
    [
        pytest.param("type:heatpump", id="string"),
        pytest.param({"type": "heatpump"}, id="object"),
        pytest.param(["type:heatpump", 1], id="non-string-entry"),
        pytest.param(["type:heatpump", None], id="null-entry"),
    ],
)
def test_parse_device_rejects_malformed_roles(roles):
    data = {"id": "0", "modelId": "m", "deviceType": "heating", "roles": roles}

    with pytest.raises(ViResponseError, match="Device roles must be a list of strings"):
        parse_device(data, "gw1", "inst1")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param({"data": {"success": True}}, (True, None, None), id="bool-true"),
        pytest.param(
            {"data": {"success": False}}, (False, None, None), id="bool-false"
        ),
        pytest.param(
            {"data": {"success": "true"}}, (True, None, None), id="lower-true"
        ),
        pytest.param(
            {"data": {"success": "True"}}, (True, None, None), id="title-true"
        ),
        pytest.param(
            {"data": {"success": "TRUE"}}, (True, None, None), id="upper-true"
        ),
        pytest.param(
            {"data": {"success": "false"}}, (False, None, None), id="lower-false"
        ),
        pytest.param(
            {"data": {"success": "False"}}, (False, None, None), id="title-false"
        ),
        pytest.param({"success": "true"}, (True, None, None), id="root-payload"),
        pytest.param(
            {"data": {"success": True, "message": "accepted", "reason": "queued"}},
            (True, "accepted", "queued"),
            id="message-and-reason",
        ),
        pytest.param(
            {"data": {"success": True, "message": None, "reason": "queued"}},
            (True, None, "queued"),
            id="null-message",
        ),
        pytest.param(
            {"data": {"success": True, "message": "accepted", "reason": None}},
            (True, "accepted", None),
            id="null-reason",
        ),
        pytest.param(
            {"data": {"success": True, "unknown": {"kept": ["field"]}}},
            (True, None, None),
            id="unknown-field",
        ),
    ],
)
def test_parse_command_outcome_accepts_documented_payloads(
    payload: dict[str, JsonValue], expected: tuple[bool, str | None, str | None]
):
    """Documented success, message, and reason shapes parse to one outcome."""
    outcome = parse_command_outcome(payload)

    assert (outcome.success, outcome.message, outcome.reason) == expected


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        *[
            pytest.param({"data": {"success": raw}}, "success", id=f"success-{raw!r}")
            for raw in ["yes", "1", 1, 0, 1.5, None, [], {}, ["true"]]
        ],
        pytest.param({"data": {}}, "success", id="missing-success"),
        pytest.param({}, "success", id="empty-root"),
        *[
            pytest.param(
                {"data": {"success": True, field: value}},
                field,
                id=f"{field}-{type(value).__name__}",
            )
            for field in ["message", "reason"]
            for value in [True, 42, [], {}]
        ],
        pytest.param({"data": "unexpected"}, "object", id="data-not-object"),
    ],
)
def test_parse_command_outcome_rejects_malformed_payloads(
    payload: dict[str, JsonValue], message: str
):
    """Malformed known fields fail the command response contract."""
    with pytest.raises(ViResponseError, match=message):
        parse_command_outcome(payload)


def _commanded_api_feature() -> dict[str, Any]:
    """Return one valid API feature with one executable command."""
    return {
        "feature": "heating.mode",
        "properties": {"mode": {"value": "auto"}},
        "commands": {
            "setMode": {
                "uri": "/commands/setMode",
                "params": {"mode": {"type": "string", "required": True}},
            }
        },
    }


def test_duplicate_api_feature_commands_reject_the_response():
    """One API feature may not report the same command name twice."""
    # Arrange: The same API feature appears twice with the same command.
    duplicate_entries = [
        _commanded_api_feature(),
        _commanded_api_feature(),
    ]

    # Act and assert: The duplicate command is rejected, keeping the command
    # list unambiguous.
    with pytest.raises(ViResponseError, match="Duplicate API feature command"):
        parse_api_features(duplicate_entries, only_available=False)


def test_same_command_across_distinct_api_features_is_accepted():
    """Different API features may report commands with the same name."""
    # Arrange: Two API features share only the command name.
    second = _commanded_api_feature()
    second["feature"] = "heating.level"
    second["properties"] = {"level": {"value": 1}}

    # Act: Parse both API features.
    _, commands = parse_api_features(
        [_commanded_api_feature(), second], only_available=False
    )

    # Assert: Both command lists carry their own setMode-like command.
    assert sorted(commands) == ["heating.level", "heating.mode"]
    assert commands["heating.mode"][0].name == "setMode"
    assert commands["heating.level"][0].name == "setMode"


@pytest.mark.parametrize(
    "deprecated",
    [
        pytest.param("yes", id="text"),
        pytest.param(1, id="number"),
        pytest.param([], id="list"),
    ],
)
def test_non_boolean_deprecation_marker_rejects_the_command(deprecated: Any):
    """isDeprecated must be a boolean when the API reports it."""
    # Arrange: The command carries a malformed deprecation marker.
    api_feature = _commanded_api_feature()
    api_feature["commands"]["setMode"]["isDeprecated"] = deprecated

    # Act and assert: The malformed marker fails the feature response.
    with pytest.raises(ViResponseError, match="isDeprecated must be a boolean"):
        parse_api_features([api_feature], only_available=False)


def test_device_commands_carry_parameter_metadata_without_availability_flags():
    """The command list keeps URI and parameter details, never flags."""
    # Arrange: One API feature whose command has optional and required params.
    extra = {
        "feature": "heating.curve",
        "properties": {"slope": {"value": 1.4}},
        "commands": {
            "setCurve": {
                "isExecutable": False,
                "isDeprecated": True,
                "uri": "/commands/setCurve",
                "params": {
                    "slope": {"type": "number", "required": True},
                    "shift": {"type": "number", "required": False},
                },
            }
        },
    }

    # Act: Parse the feature response into its flat features and command list.
    features, commands = parse_api_features([extra], only_available=False)
    (command,) = commands["heating.curve"]

    # Assert: The command list keeps every reported command, even a blocked
    # one, with URIs and parameter metadata but neither flag.
    assert command.name == "setCurve"
    assert command.uri == "/commands/setCurve"
    assert command.parameters == (
        CommandParameter(
            name="slope",
            required=True,
            constraints=FeatureConstraints(value_type="number"),
        ),
        CommandParameter(
            name="shift",
            required=False,
            constraints=FeatureConstraints(value_type="number"),
        ),
    )
    assert "{isExecutable" not in str(command)
    assert "isDeprecated" not in str(command)
    assert all(feature.write_binding is None for feature in features)
