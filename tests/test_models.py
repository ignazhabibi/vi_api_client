"""Tests for data models (Flat Architecture)."""

from vi_api_client.models import Device, Feature, FeatureControl


def test_feature_dataclass():
    """Test Feature dataclass creation and properties."""
    # --- ARRANGE ---
    # N/A - simple constructor

    # --- ACT ---
    f = Feature(name="test.feature", value=10, unit="C", is_enabled=True, is_ready=True)

    # --- ASSERT ---
    assert f.name == "test.feature"
    assert f.value == 10
    assert f.unit == "C"
    assert f.is_writable is False


def test_feature_writable():
    """Test Feature with control."""
    # --- ARRANGE ---
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

    # --- ACT ---
    f = Feature(
        name="test.writable",
        value=50,
        unit="%",
        is_enabled=True,
        is_ready=True,
        control=ctrl,
    )

    # --- ASSERT ---
    assert f.is_writable is True
    assert f.control.min == 0
    assert f.control.max == 100
    assert f.control.options == [1, 2]


def test_device_dataclass():
    """Test Device creation and feature cache."""
    # --- ARRANGE ---
    f1 = Feature(name="f1", value=1, unit=None, is_enabled=True, is_ready=True)
    f2 = Feature(name="f2", value=2, unit=None, is_enabled=True, is_ready=True)

    # --- ACT ---
    d = Device(
        id="1",
        gateway_serial="gw",
        installation_id="inst",
        model_id="m",
        device_type="t",
        status="ok",
        features=[f1, f2],
    )

    # --- ASSERT ---
    assert len(d.features) == 2
    # Test O(1) cache access
    assert d.get_feature("f1") == f1
    assert d.get_feature("f2") == f2
    assert d.get_feature("missing") is None


def test_device_from_api():
    """Test Device.from_api."""
    # --- ARRANGE ---
    data = {
        "id": "dev1",
        "modelId": "complex_model",
        "deviceType": "heatpump",
        "status": "Online",
    }

    # --- ACT ---
    d = Device.from_api(data, "gw1", "inst1")

    # --- ASSERT ---
    assert d.id == "dev1"
    assert d.model_id == "complex_model"
