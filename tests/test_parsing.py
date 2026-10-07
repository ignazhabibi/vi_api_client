"""Tests for parsing API features into flat features and their controls."""

import pytest
from builders import load_fixture_json

from vi_api_client.exceptions import ViResponseError
from vi_api_client.models import ScheduleConstraints
from vi_api_client.parsing import api_feature_to_flat_features


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
    assert feature.control is not None
    assert feature.control.required_parameters == (
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
def test_consumption_series_expose_a_current_year_alias(
    fixture_name: str, base_name: str
):
    """Consumption series expose the first year value as a currentYear feature."""
    # Arrange: Load the consumption fixture with day, month, and year arrays.
    data = load_fixture_json(fixture_name)

    # Act: Parse the consumption feature.
    features = api_feature_to_flat_features(data)

    # Assert: The base series and only the currentYear alias are available.
    assert len(features) == 2

    base_feature = next(feature for feature in features if feature.name == base_name)
    assert isinstance(base_feature.value, dict)
    year = base_feature.value["year"]
    assert isinstance(year, dict)

    current_year_feature = next(
        feature for feature in features if feature.name == f"{base_name}.currentYear"
    )
    year_values = year["value"]
    assert isinstance(year_values, list)
    assert current_year_feature.value == year_values[0]
    assert current_year_feature.unit == "kilowattHour"
    assert not any(
        feature.name.endswith((".currentDay", ".currentMonth")) for feature in features
    )


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
    assert feature_slope.control is not None
    assert feature_slope.control.command_name == "setCurve"
    assert feature_slope.control.parameter_name == "slope"
    assert feature_slope.control.value_type == "number"
    assert "shift" in feature_slope.control.required_parameters

    feature_shift = next(
        feature for feature in features if feature.name.endswith(".shift")
    )
    assert feature_shift.control is not None
    assert feature_shift.control.command_name == "setCurve"
    assert feature_shift.control.parameter_name == "shift"


def test_hysteresis_commands_create_writable_switch_point_features():
    """Hysteresis value and switch points become separate writable features."""
    # Arrange: Load the fixture with hysteresis value and switch point commands.
    raw_feature = load_fixture_json("parsing/hysteresis_raw.json")

    # Act: Parse the hysteresis feature with its command-linked properties.
    features = api_feature_to_flat_features(raw_feature)

    # Assert: The base value and both switch points are writable with their commands.
    assert {
        feature.name: feature.control.command_name if feature.control else None
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
    ("year_property", "expect_alias"),
    [
        pytest.param(None, False, id="year-absent"),
        pytest.param(
            {"type": "array", "unit": "kilowattHour", "value": []},
            False,
            id="year-empty",
        ),
        pytest.param(
            {"type": "array", "unit": "kilowattHour", "value": [5.5]},
            True,
            id="year-populated",
        ),
    ],
)
def test_current_year_alias_requires_a_populated_year_series(
    year_property: dict | None, expect_alias: bool
):
    """The currentYear alias should only mirror a populated year series."""
    # Arrange: Build a consumption feature with the given year property.
    properties: dict = {
        "day": {"type": "array", "unit": "kilowattHour", "value": [1.0]}
    }
    if year_property is not None:
        properties["year"] = year_property
    raw_feature = {
        "feature": "heating.power.consumption.cooling",
        "properties": properties,
    }

    # Act: Parse the consumption feature.
    features = api_feature_to_flat_features(raw_feature)

    # Assert: The alias exists exactly when the year series is populated.
    alias_names = [
        feature.name
        for feature in features
        if feature.name == "heating.power.consumption.cooling.currentYear"
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
    assert feature.control is not None
    assert feature.control.command_name == "setTargetTemperature"
    assert feature.control.parameter_name == "targetTemperature"


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

    # Assert: The enum members become the control's allowed options.
    assert len(features) == 1
    control = features[0].control
    assert control is not None
    assert list(control.options or []) == ["auto", "eco"]


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
    assert feature.control is None
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
    control = api_feature_to_flat_features(raw_feature)[0].control

    # Assert: The control writes through the executable command.
    assert control is not None
    assert (control.command_name, control.parameter_name) == ("setCurve", "slope")
    assert control.uri == "/commands/setCurve"


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
    control = api_feature_to_flat_features(raw_feature)[0].control

    # Assert: The parameter constraints win, including the 'stepping' alias.
    assert control is not None
    assert (control.min, control.max, control.step) == (0.2, 3.5, 0.1)


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
    control = api_feature_to_flat_features(raw_feature)[0].control

    # Assert: The gaps are filled from the property, resolving the alias.
    assert control is not None
    assert (control.min_length, control.max_length) == (1, 20)
    assert control.pattern == "^[A-Z]"


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
    assert active.control is None
    control = schedule.control
    assert control is not None
    assert control.command_name == "setSchedule"
    assert control.parameter_name == "newSchedule"
    assert control.value_type == "Schedule"
    assert (control.min, control.max, control.step, control.options) == (
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
    control = api_feature_to_flat_features(raw_feature)[0].control

    # Assert: Every rule is available on the control.
    assert control is not None
    assert control.schedule == ScheduleConstraints(
        max_entries=4,
        modes=("on",),
        resolution=10,
        overlap_allowed=False,
        default_mode="off",
    )


def test_schedule_control_without_reported_rules_has_no_schedule_constraints():
    raw_feature = _schedule_feature(
        {"setSchedule": {"params": {"newSchedule": {"type": "Schedule"}}}}
    )

    control = api_feature_to_flat_features(raw_feature)[0].control

    assert control is not None
    assert control.schedule is None


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


def test_command_requiring_the_value_is_preferred_over_one_taking_it_optionally():
    """'activate(temperature)' switches a program on, so 'setTemperature' writes it."""
    # Arrange: 'activate' comes first and takes the temperature only optionally.
    raw_feature = {
        "feature": "heating.circuits.0.operating.programs.comfort",
        "properties": {"temperature": {"type": "number", "value": 20}},
        "commands": {
            "activate": {
                "uri": "/commands/activate",
                "params": {"temperature": {"type": "number", "required": False}},
            },
            "setTemperature": {
                "uri": "/commands/setTemperature",
                "params": {"targetTemperature": {"type": "number", "required": True}},
            },
        },
    }

    # Act: Parse the API feature.
    control = api_feature_to_flat_features(raw_feature)[0].control

    # Assert: The temperature is written through 'setTemperature'.
    assert control is not None
    assert (control.command_name, control.parameter_name) == (
        "setTemperature",
        "targetTemperature",
    )


def test_command_taking_the_value_optionally_is_used_when_it_is_the_only_one():
    raw_feature = {
        "feature": "heating.circuits.0.operating.programs.comfort",
        "properties": {"temperature": {"type": "number", "value": 20}},
        "commands": {
            "activate": {
                "uri": "/commands/activate",
                "params": {"temperature": {"type": "number", "required": False}},
            },
        },
    }

    control = api_feature_to_flat_features(raw_feature)[0].control

    assert control is not None
    assert (control.command_name, control.parameter_name) == ("activate", "temperature")


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
    assert feature.control is None


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
    control = api_feature_to_flat_features(raw_feature)[0].control

    # Assert: The schedule control writes through the executable command.
    assert control is not None
    assert (control.command_name, control.parameter_name) == (
        "setSchedule",
        "newSchedule",
    )
    assert control.uri == "/commands/setSchedule"
