"""Tests for data models (Flat Architecture)."""

from vi_api_client.models import Device, Feature, FeatureControl


class TestModels:
    def test_feature_dataclass(self):
        """Test Feature dataclass creation and properties."""
        f = Feature(
            name="test.feature", value=10, unit="C", is_enabled=True, is_ready=True
        )
        assert f.name == "test.feature"
        assert f.value == 10
        assert f.unit == "C"
        assert f.is_writable is False

    def test_feature_writable(self):
        """Test Feature with control."""
        ctrl = FeatureControl(
            command_name="set",
            param_name="target",
            required_params=["target"],
            parent_feature_name="parent",
            uri="url",
            min=0,
            max=100,
            step=1,
            options=[1, 2],
        )
        f = Feature(
            name="test.writable",
            value=50,
            unit="%",
            is_enabled=True,
            is_ready=True,
            control=ctrl,
        )
        assert f.is_writable is True
        assert f.control.min == 0
        assert f.control.max == 100
        assert f.control.options == [1, 2]

    def test_device_dataclass(self):
        """Test Device creation and feature cache."""
        f1 = Feature(name="f1", value=1, unit=None, is_enabled=True, is_ready=True)
        f2 = Feature(name="f2", value=2, unit=None, is_enabled=True, is_ready=True)

        d = Device(
            id="1",
            gateway_serial="gw",
            installation_id="inst",
            model_id="m",
            device_type="t",
            status="ok",
            features=[f1, f2],
        )

        assert len(d.features) == 2
        # Test O(1) cache access
        assert d.get_feature("f1") == f1
        assert d.get_feature("f2") == f2
        assert d.get_feature("missing") is None

    def test_device_from_api(self):
        """Test Device.from_api."""
        data = {
            "id": "dev1",
            "modelId": "complex_model",
            "deviceType": "heatpump",
            "status": "Online",
        }
        d = Device.from_api(data, "gw1", "inst1")
        assert d.id == "dev1"
        assert d.model_id == "complex_model"
