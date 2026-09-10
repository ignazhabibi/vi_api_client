"""Tests for data models (Flat Architecture)."""

import vi_api_client
from vi_api_client.exceptions import ViError, ViResponseError
from vi_api_client.models import (
    Device,
    Feature,
    FeatureControl,
    GatewayDeviceRefreshResult,
)


def test_feature_dataclass():
    """Test Feature dataclass creation and properties."""
    # Arrange: No setup needed for simple constructor test.
    # N/A - simple constructor

    # Act: Create Feature instance with basic properties.
    feature = Feature(
        name="test.feature", value=10, unit="C", is_enabled=True, is_ready=True
    )

    # Assert: Feature should have all properties set and is_writable=False by default.
    assert feature.name == "test.feature"
    assert feature.value == 10
    assert feature.unit == "C"
    assert feature.is_writable is False


def test_feature_writable():
    """Test Feature with control."""
    # Arrange: Create FeatureControl with constraints and options.
    ctrl = FeatureControl(
        command_name="set",
        param_name="target",
        required_params=["target"],
        parent_feature_name="parent",
        uri="url",
        min=0,
        max=100,
        step=1,
        value_type="number",
        options=[1, 2],
    )

    # Act: Create Feature with control metadata attached.
    feature = Feature(
        name="test.writable",
        value=50,
        unit="%",
        is_enabled=True,
        is_ready=True,
        control=ctrl,
    )

    # Assert: Feature should be writable and have constraint values from control.
    assert feature.is_writable is True
    assert feature.control.min == 0
    assert feature.control.max == 100
    assert feature.control.value_type == "number"
    assert feature.control.options == [1, 2]


def test_device_dataclass():
    """Test Device creation and feature cache."""
    # Arrange: Create two test features (f1, f2).
    f1 = Feature(name="f1", value=1, unit=None, is_enabled=True, is_ready=True)
    f2 = Feature(name="f2", value=2, unit=None, is_enabled=True, is_ready=True)

    # Act: Create Device with features parameter to populate cache.
    dev = Device(
        id="123",
        gateway_serial="gw",
        installation_id="inst",
        model_id="TestModel",
        device_type="test",
        status="Online",
        features=[f1, f2],
    )

    # Assert: Device should have 2 cached features accessible via get_feature.
    assert len(dev.features) == 2
    # Test O(1) cache access
    assert dev.get_feature("f1") == f1
    assert dev.get_feature("f2") == f2
    assert dev.get_feature("missing") is None


def test_device_from_api():
    """Test Device.from_api."""
    # Arrange: Prepare API response data dictionary.
    data = {
        "id": "dev1",
        "modelId": "complex_model",
        "deviceType": "heatpump",
        "status": "Online",
    }

    # Act: Parse API response into Device model.
    d = Device.from_api(data, "gw1", "inst1")

    # Assert: Device should have API data correctly mapped to model fields.
    assert d.id == "dev1"
    assert d.model_id == "complex_model"


def test_gateway_device_refresh_result_reports_completeness():
    # Arrange: Create one refreshed device and one device-specific error.
    refreshed_device = Device(
        id="device-0",
        gateway_serial="gateway-1",
        installation_id="installation-1",
        model_id="Vitocal250A",
        device_type="heating",
        status="connected",
    )
    error = ViError("device unavailable")

    # Act: Build complete and partial gateway device refresh results.
    complete_result = GatewayDeviceRefreshResult(
        updated_devices=[refreshed_device], errors_by_device_id={}
    )
    partial_result = GatewayDeviceRefreshResult(
        updated_devices=[refreshed_device],
        errors_by_device_id={"device-1": error},
    )

    # Assert: Completeness depends only on the device-specific error mapping.
    assert complete_result.is_complete
    assert not partial_result.is_complete


def test_gateway_refresh_public_types_are_exported():
    # Act and assert: New public contracts are available from the package root.
    assert vi_api_client.GatewayDeviceRefreshResult is GatewayDeviceRefreshResult
    assert vi_api_client.ViResponseError is ViResponseError
