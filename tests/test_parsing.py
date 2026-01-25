"""Tests for feature parsing logic (Flat Architecture)."""

from vi_api_client.parsing import parse_feature_flat


def test_feature_simple_value(load_fixture_json):
    # --- ARRANGE ---
    data = load_fixture_json("parsing/simple_value.json")

    # --- ACT ---
    features = parse_feature_flat(data)

    # --- ASSERT ---
    assert len(features) == 1
    f = features[0]
    assert f.name == "heating.sensors.temperature.outside"
    assert f.is_enabled is True
    assert f.value == 5.5
    assert f.unit == "celsius"


def test_feature_status(load_fixture_json):
    # --- ARRANGE ---
    data = load_fixture_json("parsing/status_feature.json")

    # --- ACT ---
    features = parse_feature_flat(data)

    # --- ASSERT ---
    assert len(features) == 1
    f = features[0]
    # Updated to use real-world feature name
    assert f.name == "heating.circuits.0.circulation.pump.status"
    assert f.value == "off"


def test_feature_complex_flat_expansion(load_fixture_json):
    """Test that complex features (multiple properties) are flattened."""
    # --- ARRANGE ---
    data = load_fixture_json("parsing/nested_expansion.json")

    # --- ACT ---
    features = parse_feature_flat(data)

    # --- ASSERT ---
    assert len(features) == 2

    f_a = next(f for f in features if f.name.endswith(".propA"))
    assert f_a.value == 10

    f_b = next(f for f in features if f.name.endswith(".propB"))
    assert f_b.value == 20
    assert f_b.unit == "C"


def test_feature_boolean_active(load_fixture_json):
    """Test feature with 'active' property."""
    # --- ARRANGE ---
    data = load_fixture_json("parsing/active_feature.json")

    # --- ACT ---
    features = parse_feature_flat(data)

    # --- ASSERT ---
    assert len(features) == 1
    f = features[0]
    assert f.name == "heating.circuits.0.operating.modes.active.active"
    assert f.value is True


def test_feature_do_not_flatten_history(load_fixture_json):
    """Test that history/day arrays are NOT flattened."""
    # --- ARRANGE ---
    data = load_fixture_json("parsing/history_array.json")

    # --- ACT ---
    features = parse_feature_flat(data)

    # --- ASSERT ---
    assert len(features) == 1
    f = features[0]
    # Should NOT be .day, but the base name because it contains complex key directly?
    # Logic says: if COMPLEX_DATA_INDICATORS in keys, return SINGLE feature with dict properties.
    # "day" is in COMPLEX_DATA_INDICATORS.
    assert f.name == "heating.power.consumption"
    assert isinstance(f.value, dict)
    assert f.value["day"]["value"] == [1.1, 2.2, 3.3]


def test_feature_priority_value_over_status(load_fixture_json):
    """Test that flattening creates strict sub-features."""
    # --- ARRANGE ---
    data = load_fixture_json("parsing/mixed_feature.json")

    # --- ACT ---
    features = parse_feature_flat(data)

    # --- ASSERT ---
    assert len(features) == 2

    f_val = next(
        f for f in features if f.name == "mixed.feature"
    )  # 'value' key maps to base name
    assert f_val.value == 42

    f_stat = next(f for f in features if f.name.endswith(".status"))
    assert f_stat.value == "error"


def test_feature_control_association(load_fixture_json):
    """Test that commands are linked to properties."""
    # --- ARRANGE ---
    data = load_fixture_json("parsing/feature_with_commands.json")

    # --- ACT ---
    features = parse_feature_flat(data)

    # --- ASSERT ---
    assert len(features) == 2

    f_slope = next(f for f in features if f.name.endswith(".slope"))
    assert f_slope.control is not None
    assert f_slope.control.command_name == "setCurve"
    assert f_slope.control.param_name == "slope"
    assert "shift" in f_slope.control.required_params

    f_shift = next(f for f in features if f.name.endswith(".shift"))
    assert f_shift.control is not None
    assert f_shift.control.command_name == "setCurve"
    assert f_shift.control.param_name == "shift"
