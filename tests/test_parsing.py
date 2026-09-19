"""Tests for feature parsing logic (Flat Architecture)."""

import pytest

from vi_api_client.exceptions import ViResponseError
from vi_api_client.parsing import parse_feature_flat


def test_feature_rejects_non_string_command_keys():
    """Programmatic mappings with non-string command keys are rejected."""
    # Arrange: Build a feature entry whose command mapping uses an integer key.
    raw_feature = {
        "feature": "heating.curve",
        "properties": {"slope": {"value": 1.0}},
        "commands": {5: {"uri": "/commands/setCurve"}},
    }

    # Act and assert: The malformed command metadata raises a response error.
    with pytest.raises(ViResponseError, match="named objects"):
        parse_feature_flat(raw_feature)


def test_feature_simple_value(load_fixture_json):
    # Arrange: Load fixture for simple temperature sensor value.
    data = load_fixture_json("parsing/simple_value.json")

    # Act: Parse the feature using flat architecture parser.
    features = parse_feature_flat(data)

    # Assert: Feature should have correct name, value (5.5°C) and unit.
    assert len(features) == 1
    feature = features[0]
    assert feature.name == "heating.sensors.temperature.outside"
    assert feature.is_enabled is True
    assert feature.value == 5.5
    assert feature.unit == "celsius"


def test_feature_control_parses_required_parameter_markers():
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
    feature = parse_feature_flat(raw_feature)[0]

    # Assert: Explicit false is optional while missing markers remain conservative.
    assert feature.control is not None
    assert feature.control.required_params == ("requiredSibling", "unspecifiedSibling")


def test_feature_status(load_fixture_json):
    # Arrange: Load fixture for circulation pump status feature.
    data = load_fixture_json("parsing/status_feature.json")

    # Act: Parse the feature using flat architecture parser.
    features = parse_feature_flat(data)

    # Assert: Feature should have status value "off".
    assert len(features) == 1
    feature = features[0]
    assert feature.name == "heating.circuits.0.circulation.pump.status"
    assert feature.value == "off"


def test_feature_complex_flat_expansion(load_fixture_json):
    """Test that complex features (multiple properties) are flattened."""
    # Arrange: Load fixture with nested properties (propA, propB).
    data = load_fixture_json("parsing/nested_expansion.json")

    # Act: Parse the feature - should flatten complex properties.
    features = parse_feature_flat(data)

    # Assert: Should create 2 separate features from nested properties.
    assert len(features) == 2

    feature_a = next(feature for feature in features if feature.name.endswith(".propA"))
    assert feature_a.value == 10

    feature_b = next(feature for feature in features if feature.name.endswith(".propB"))
    assert feature_b.value == 20
    assert feature_b.unit == "C"


def test_feature_boolean_active(load_fixture_json):
    """Test feature with 'active' property."""
    # Arrange: Load fixture with 'active' boolean property.
    data = load_fixture_json("parsing/active_feature.json")

    # Act: Parse the feature with boolean active property.
    features = parse_feature_flat(data)

    # Assert: Feature should have boolean value True.
    assert len(features) == 1
    feature = features[0]
    assert feature.name == "heating.circuits.0.operating.modes.active.active"
    assert feature.value is True


def test_feature_do_not_flatten_history(load_fixture_json):
    """Test that history/day arrays are NOT flattened."""
    # Arrange: Load fixture with history array property.
    data = load_fixture_json("parsing/history_array.json")

    # Act: Parse the feature with array value.
    features = parse_feature_flat(data)

    # Assert: History array should be kept as-is, not flattened.
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
        ("parsing/consumption_alias_cooling.json", "heating.power.consumption.cooling"),
        ("parsing/consumption_alias_dhw.json", "heating.power.consumption.dhw"),
        ("parsing/consumption_alias_heating.json", "heating.power.consumption.heating"),
        ("parsing/consumption_alias_total.json", "heating.power.consumption.total"),
    ],
)
def test_feature_adds_current_year_consumption_alias(
    load_fixture_json, fixture_name: str, base_name: str
):
    """Consumption series expose the first year value as a currentYear feature."""
    # Arrange: Load the consumption fixture with day, month, and year arrays.
    data = load_fixture_json(fixture_name)

    # Act: Parse the feature using the flat architecture parser.
    features = parse_feature_flat(data)

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


def test_feature_priority_value_over_status(load_fixture_json):
    """Test that flattening creates strict sub-features."""
    # Arrange: Load fixture with both 'value' and 'status' properties.
    data = load_fixture_json("parsing/mixed_feature.json")

    # Act: Parse feature with multiple property types.
    features = parse_feature_flat(data)

    # Assert: Should create 2 features - base name maps to 'value', status gets suffix.
    assert len(features) == 2

    feature_val = next(
        feature for feature in features if feature.name == "mixed.feature"
    )  # 'value' key maps to base name
    assert feature_val.value == 42

    feature_stat = next(
        feature for feature in features if feature.name.endswith(".status")
    )
    assert feature_stat.value == "error"


def test_feature_control_association(load_fixture_json):
    """Test that commands are linked to properties."""
    # Arrange: Load fixture with commands (setCurve) linked to properties.
    data = load_fixture_json("parsing/feature_with_commands.json")

    # Act: Parse feature with writable command associations.
    features = parse_feature_flat(data)

    # Assert: Features should have control metadata with command details.
    assert len(features) == 2

    feature_slope = next(
        feature for feature in features if feature.name.endswith(".slope")
    )
    assert feature_slope.control is not None
    assert feature_slope.control.command_name == "setCurve"
    assert feature_slope.control.param_name == "slope"
    assert feature_slope.control.value_type == "number"
    assert "shift" in feature_slope.control.required_params

    feature_shift = next(
        feature for feature in features if feature.name.endswith(".shift")
    )
    assert feature_shift.control is not None
    assert feature_shift.control.command_name == "setCurve"
    assert feature_shift.control.param_name == "shift"


def test_hysteresis_commands_create_writable_switch_point_features(load_fixture_json):
    """Hysteresis value and switch points become separate writable features."""
    # Arrange: Load the fixture with hysteresis value and switch point commands.
    raw_feature = load_fixture_json("parsing/hysteresis_raw.json")

    # Act: Parse the hysteresis feature with its command-linked properties.
    features = parse_feature_flat(raw_feature)

    # Assert: The base value and both switch points are writable with their commands.
    assert len(features) == 3
    expected_commands = {
        "heating.dhw.temperature.hysteresis": "setHysteresis",
        "heating.dhw.temperature.hysteresis.switchOnValue": (
            "setHysteresisSwitchOnValue"
        ),
        "heating.dhw.temperature.hysteresis.switchOffValue": (
            "setHysteresisSwitchOffValue"
        ),
    }
    for feature in features:
        assert feature.name in expected_commands
        assert feature.is_writable
        assert feature.control is not None
        assert feature.control.command_name == expected_commands[feature.name]


def test_feature_treats_scalar_min_max_as_metadata():
    """Scalar min/max properties should be skipped during flattening."""
    # Arrange: Build a feature with scalar min/max metadata around the value.
    raw_feature = {
        "feature": "heating.curve",
        "properties": {"min": 5, "max": 10, "value": 3},
    }

    # Act: Parse the feature using the flat architecture parser.
    features = parse_feature_flat(raw_feature)

    # Assert: Only the value flattens into a feature.
    assert len(features) == 1
    assert features[0].name == "heating.curve"
    assert features[0].value == 3


def test_feature_treats_nested_min_max_as_properties():
    """Object-shaped min/max properties should flatten into sub-features."""
    # Arrange: Build a feature with a nested min object beside the value.
    raw_feature = {
        "feature": "heating.curve",
        "properties": {"min": {"value": 1}, "value": 3},
    }

    # Act: Parse the feature using the flat architecture parser.
    features = parse_feature_flat(raw_feature)

    # Assert: The nested object flattens beside the base value.
    assert len(features) == 2
    assert {feature.name for feature in features} == {
        "heating.curve",
        "heating.curve.min",
    }


def test_feature_value_only_property_keeps_default_unit():
    """A value-only feature should flatten with its declared unit."""
    # Arrange: Build a feature whose only data property is its value.
    raw_feature = {
        "feature": "heating.sensors.temperature.outside",
        "properties": {"unit": "celsius", "type": "number", "value": 5},
    }

    # Act: Parse the feature using the flat architecture parser.
    features = parse_feature_flat(raw_feature)

    # Assert: The value flattens with the declared default unit.
    assert len(features) == 1
    assert features[0].name == "heating.sensors.temperature.outside"
    assert features[0].value == 5
    assert features[0].unit == "celsius"


@pytest.mark.parametrize(
    ("year_property", "expect_alias"),
    [
        (None, False),
        ({"type": "array", "unit": "kilowattHour", "value": []}, False),
        ({"type": "array", "unit": "kilowattHour", "value": [5.5]}, True),
    ],
    ids=["year-absent", "year-empty", "year-populated"],
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
    features = parse_feature_flat(raw_feature)

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

    # Act: Parse the feature using the flat architecture parser.
    features = parse_feature_flat(raw_feature)

    # Assert: The temperature property exposes the aliased command control.
    assert len(features) == 1
    feature = features[0]
    assert feature.name == "heating.dhw.temperature.main.temperature"
    assert feature.control is not None
    assert feature.control.command_name == "setTargetTemperature"
    assert feature.control.param_name == "targetTemperature"


def test_command_enum_maps_to_control_options():
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

    # Act: Parse the feature using the flat architecture parser.
    features = parse_feature_flat(raw_feature)

    # Assert: The enum members become the control's allowed options.
    assert len(features) == 1
    control = features[0].control
    assert control is not None
    assert list(control.options or []) == ["auto", "eco"]
