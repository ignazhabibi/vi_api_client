"""Tests for data models."""

import pytest
from vi_api_client.models import Feature, Device

class TestModels:
    
    def test_feature_simple_value(self):
        data = {
            "feature": "heating.sensors.temperature.outside",
            "isEnabled": True,
            "properties": {
                "value": {"type": "number", "value": 5.5, "unit": "celsius"}
            }
        }
        f = Feature.from_api(data)
        assert f.name == "heating.sensors.temperature.outside"
        assert f.is_enabled is True
        assert f.value == 5.5
        assert f.unit == "celsius"
        assert f.formatted_value == "5.5 celsius"

    def test_feature_status(self):
        data = {
            "feature": "heating.compressor",
            "isEnabled": True,
            "properties": {
                "status": {"type": "string", "value": "off"}
            }
        }
        f = Feature.from_api(data)
        assert f.value == "off"
        assert f.formatted_value == "off"

    def test_feature_complex(self):
        data = {
            "feature": "heating.nested",
            "isEnabled": True,
            "properties": {
                "min": {"type": "number", "value": 10},
                "max": {"type": "number", "value": 20, "unit": "C"}
            }
        }
        f = Feature.from_api(data)
        # Main value/unit should be None as there is no "value" key
        assert f.value is None
        assert f.unit is None
        
        # Formatted value should show all props
        fv = f.formatted_value
        assert "min: 10" in fv
        assert "max: 20 C" in fv

    def test_device_creation(self):
        data = {"id": "0", "modelId": "vitocal", "deviceType": "heating", "status": "Online"}
        d = Device.from_api(data, "gw1", 123)
        assert d.id == "0"
        assert d.gateway_serial == "gw1"
        assert d.installation_id == 123
        assert d.model_id == "vitocal"

    def test_feature_boolean_active(self):
        """Test feature with 'active' property (common for switches)."""
        data = {
            "feature": "heating.circuits.0.operating.modes.active",
            "isEnabled": True,
            "properties": {
                "active": {"type": "boolean", "value": True}
            }
        }
        f = Feature.from_api(data)
        assert f.value is True
        assert f.formatted_value == "True"

    def test_feature_history_list(self):
        """Test feature with history list (e.g. day)."""
        data = {
            "feature": "heating.power.consumption",
            "isEnabled": True,
            "properties": {
                "day": {"type": "array", "value": [1.1, 2.2, 3.3]}
            }
        }
        f = Feature.from_api(data)
        assert f.value == [1.1, 2.2, 3.3]
        # formatted_value should indicate contents for short lists
        assert "[1.1, 2.2, 3.3]" in f.formatted_value

    def test_feature_priority_value_over_status(self):
        """Test that 'value' takes precedence over 'status'."""
        data = {
            "feature": "mixed.feature",
            "isEnabled": True,
            "properties": {
                "value": {"type": "number", "value": 42},
                "status": {"type": "string", "value": "error"}
            }
        }
        f = Feature.from_api(data)
    def test_feature_expand_curve(self):
        """Test expanding a heating curve feature."""
        data = {
            "feature": "heating.circuits.0.heating.curve",
            "isEnabled": True,
            "properties": {
                "slope": {"type": "number", "value": 1.2},
                "shift": {"type": "number", "value": 5}
            }
        }
        f = Feature.from_api(data)
        expanded = f.expand()
        
        assert len(expanded) == 2
        
        f_slope = next(sub for sub in expanded if sub.name.endswith(".slope"))
        assert f_slope.name == "heating.circuits.0.heating.curve.slope"
        assert f_slope.value == 1.2
        
        f_shift = next(sub for sub in expanded if sub.name.endswith(".shift"))
        assert f_shift.name == "heating.circuits.0.heating.curve.shift"
        assert f_shift.value == 5

    def test_feature_expand_summary(self):
        """Test expanding a summary feature."""
        data = {
            "feature": "heating.power.consumption.summary.dhw",
            "isEnabled": True,
            "properties": {
                "currentDay": {"type": "number", "value": 5.5, "unit": "kWh"},
                "lastMonth": {"type": "number", "value": 100, "unit": "kWh"}
            }
        }
        f = Feature.from_api(data)
        expanded = f.expand()
        
        assert len(expanded) == 2
        f_day = next(sub for sub in expanded if sub.name.endswith(".currentDay"))
        assert f_day.value == 5.5
        assert f_day.unit == "kWh"

    def test_device_flat_features(self):
        """Test device flattening."""
        data = {"id": "0", "modelId": "vitocal", "deviceType": "heating", "status": "Online"}
        d = Device.from_api(data, "gw1", 123)
        
        # Add a mix of simple and complex features
        f_simple = Feature.from_api({"feature": "simple", "isEnabled": True, "properties": {"value": {"value": 1}}})
        f_complex = Feature.from_api({
            "feature": "complex", 
            "isEnabled": True, 
            "properties": {"slope": {"value": 1}, "shift": {"value": 2}}
        })
        d.features = [f_simple, f_complex]
        
        flat = d.features_flat
        assert len(flat) == 3 # 1 simple + 2 from complex
        assert any(f.name == "simple" for f in flat)
        assert any(f.name == "complex.shift" for f in flat)

    def test_feature_expand_metrics(self):
        """Test expanding generic metrics (e.g. statistics)."""
        data = {
            "feature": "heating.heatingRod.statistics",
            "isEnabled": True,
            "properties": {
                "starts": {"type": "number", "value": 347},
                "hours": {"type": "number", "value": 43, "unit": "hour"}
            }
        }
        f = Feature.from_api(data)
        expanded = f.expand()
        
        assert len(expanded) == 2
        f_starts = next(sub for sub in expanded if sub.name.endswith(".starts"))
        assert f_starts.value == 347
        
        f_hours = next(sub for sub in expanded if sub.name.endswith(".hours"))
        assert f_hours.value == 43
        assert f_hours.unit == "hour"

    def test_feature_expand_levels(self):
        """Test expanding levels key-value pairs."""
        data = {
            "feature": "heating.circuits.0.temperature.levels",
            "isEnabled": True,
            "properties": {
                "min": {"type": "number", "value": 20},
                "max": {"type": "number", "value": 60}
            }
        }
        f = Feature.from_api(data)
        expanded = f.expand()
        assert len(expanded) == 2
        assert any(sub.value == 20 for sub in expanded)
        assert any(sub.value == 60 for sub in expanded)
