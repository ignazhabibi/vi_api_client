"""Tests for feature parsing logic (Flat Architecture)."""

from vi_api_client.parsing import parse_feature_flat


class TestParsing:
    def test_feature_simple_value(self):
        data = {
            "feature": "heating.sensors.temperature.outside",
            "isEnabled": True,
            "properties": {
                "value": {"type": "number", "value": 5.5, "unit": "celsius"}
            },
        }
        features = parse_feature_flat(data)
        assert len(features) == 1
        f = features[0]
        assert f.name == "heating.sensors.temperature.outside"
        assert f.is_enabled is True
        assert f.value == 5.5
        assert f.unit == "celsius"

    def test_feature_status(self):
        data = {
            "feature": "heating.compressor",
            "isEnabled": True,
            "properties": {"status": {"type": "string", "value": "off"}},
        }
        features = parse_feature_flat(data)
        assert len(features) == 1
        f = features[0]
        assert f.name == "heating.compressor.status"
        assert f.value == "off"

    def test_feature_complex_flat_expansion(self):
        """Test that complex features (multiple properties) are flattened."""
        data = {
            "feature": "heating.nested",
            "isEnabled": True,
            "properties": {
                "propA": {"type": "number", "value": 10},
                "propB": {"type": "number", "value": 20, "unit": "C"},
            },
        }
        features = parse_feature_flat(data)
        assert len(features) == 2

        f_a = next(f for f in features if f.name.endswith(".propA"))
        assert f_a.value == 10

        f_b = next(f for f in features if f.name.endswith(".propB"))
        assert f_b.value == 20
        assert f_b.unit == "C"

    def test_feature_boolean_active(self):
        """Test feature with 'active' property."""
        data = {
            "feature": "heating.circuits.0.operating.modes.active",
            "isEnabled": True,
            "properties": {"active": {"type": "boolean", "value": True}},
        }
        features = parse_feature_flat(data)
        assert len(features) == 1
        f = features[0]
        assert f.name == "heating.circuits.0.operating.modes.active.active"
        assert f.value is True

    def test_feature_do_not_flatten_history(self):
        """Test that history/day arrays are NOT flattened."""
        data = {
            "feature": "heating.power.consumption",
            "isEnabled": True,
            "properties": {"day": {"type": "array", "value": [1.1, 2.2, 3.3]}},
        }
        features = parse_feature_flat(data)
        assert len(features) == 1
        f = features[0]
        # Should NOT be .day, but the base name because it contains complex key directly?
        # Logic says: if COMPLEX_DATA_INDICATORS in keys, return SINGLE feature with dict properties.
        # "day" is in COMPLEX_DATA_INDICATORS.
        assert f.name == "heating.power.consumption"
        assert isinstance(f.value, dict)
        assert f.value["day"]["value"] == [1.1, 2.2, 3.3]

    def test_feature_priority_value_over_status(self):
        """Test that flattening creates strict sub-features."""
        data = {
            "feature": "mixed.feature",
            "isEnabled": True,
            "properties": {
                "value": {"type": "number", "value": 42},
                "status": {"type": "string", "value": "error"},
            },
        }
        features = parse_feature_flat(data)
        assert len(features) == 2

        f_val = next(
            f for f in features if f.name == "mixed.feature"
        )  # 'value' key maps to base name
        assert f_val.value == 42

        f_stat = next(f for f in features if f.name.endswith(".status"))
        assert f_stat.value == "error"

    def test_feature_control_association(self):
        """Test that commands are linked to properties."""
        data = {
            "feature": "heating.curve",
            "isEnabled": True,
            "properties": {
                "slope": {"type": "number", "value": 1.4},
                "shift": {"type": "number", "value": 5},
            },
            "commands": {
                "setCurve": {
                    "isExecutable": True,
                    "uri": "foo",
                    "params": {
                        "slope": {"type": "number"},
                        "shift": {"type": "number"},
                    },
                }
            },
        }
        features = parse_feature_flat(data)
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
