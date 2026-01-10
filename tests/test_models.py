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
