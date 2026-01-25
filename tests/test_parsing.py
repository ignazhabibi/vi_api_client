"""Tests for feature parsing logic (Flat Architecture)."""

from vi_api_client.parsing import parse_feature_flat


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
    # Should NOT be .day, but the base name because it contains complex key directly?
    # Logic says: if COMPLEX_DATA_INDICATORS in keys, return SINGLE feature with dict properties.
    # "day" is in COMPLEX_DATA_INDICATORS.
    assert feature.name == "heating.power.consumption"
    assert isinstance(feature.value, dict)
    assert feature.value["day"]["value"] == [1.1, 2.2, 3.3]


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
    assert "shift" in feature_slope.control.required_params

    feature_shift = next(
        feature for feature in features if feature.name.endswith(".shift")
    )
    assert feature_shift.control is not None
    assert feature_shift.control.command_name == "setCurve"
    assert feature_shift.control.param_name == "shift"
