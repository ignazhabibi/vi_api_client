"""Tests for data models (Flat Architecture)."""

from collections.abc import Mapping, Sequence

import pytest

import vi_api_client
from vi_api_client.exceptions import ViError, ViResponseError
from vi_api_client.models import (
    Device,
    Feature,
    FeatureControl,
    GatewayDeviceRefreshResult,
    Installation,
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


def test_feature_value_is_not_recursively_frozen():
    # Arrange: Create a feature with a caller-owned arbitrary payload.
    value = {"entries": [1]}
    feature = Feature(
        name="test.feature", value=value, unit=None, is_enabled=True, is_ready=True
    )

    # Act: Mutate the arbitrary value after construction.
    value["entries"].append(2)

    # Assert: The model collection contract does not recursively freeze Any values.
    assert feature.value == {"entries": [1, 2]}


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
    assert feature.control is not None
    assert feature.control.min == 0
    assert feature.control.max == 100
    assert feature.control.value_type == "number"
    assert feature.control.options == (1, 2)


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


def test_device_features_are_immutable_and_keep_lookup_in_sync():
    # Arrange: Build a device from a caller-owned mutable feature list.
    first_feature = Feature(
        name="f1", value=1, unit=None, is_enabled=True, is_ready=True
    )
    second_feature = Feature(
        name="f2", value=2, unit=None, is_enabled=True, is_ready=True
    )
    input_features = [first_feature]
    device = Device(
        id="123",
        gateway_serial="gw",
        installation_id="inst",
        model_id="TestModel",
        device_type="test",
        status="Online",
        features=input_features,
    )

    # Act: Mutate the caller-owned collection after construction.
    input_features.append(second_feature)

    # Assert: The snapshot and its O(1) lookup remain consistent and read-only.
    assert isinstance(device.features, Sequence)
    assert not isinstance(device.features, list)
    assert device.features == (first_feature,)
    assert device.get_feature("f1") is first_feature
    assert device.get_feature("f2") is None
    assert isinstance(device._features_by_name, Mapping)
    cache_attribute = "_features_by_name"
    with pytest.raises(TypeError):
        getattr(device, cache_attribute)["f2"] = second_feature


def test_device_rejects_duplicate_feature_names():
    # Arrange: Build two distinct features with the same documented identity.
    duplicate_features = [
        Feature(name="f1", value=1, unit=None, is_enabled=True, is_ready=True),
        Feature(name="f1", value=2, unit=None, is_enabled=True, is_ready=True),
    ]

    # Act and assert: Direct invalid model construction is rejected.
    with pytest.raises(ValueError, match="Duplicate feature name: f1"):
        Device(
            id="123",
            gateway_serial="gw",
            installation_id="inst",
            model_id="TestModel",
            device_type="test",
            status="Online",
            features=duplicate_features,
        )


def test_other_model_collections_are_immutable_snapshots():
    # Arrange: Create models from caller-owned mutable collections.
    required_params = ["target"]
    options = ["eco"]
    control = FeatureControl(
        command_name="set",
        param_name="target",
        required_params=required_params,
        parent_feature_name="parent",
        uri="url",
        options=options,
    )
    refreshed_devices = []
    errors_by_device_id = {"device-1": ViError("unavailable")}
    result = GatewayDeviceRefreshResult(refreshed_devices, errors_by_device_id)
    address = {"city": "Berlin"}
    installation = Installation("1", "Home", "Home", address)

    # Act: Mutate the original collections after construction.
    required_params.append("mode")
    options.append("comfort")
    refreshed_devices.append(Device("1", "gw", "inst", "model", "heating", "connected"))
    errors_by_device_id["device-2"] = ViError("offline")
    address["street"] = "Example Street"

    # Assert: Each public collection is an immutable defensive copy.
    assert control.required_params == ("target",)
    assert control.options == ("eco",)
    assert result.updated_devices == ()
    assert list(result.errors_by_device_id) == ["device-1"]
    assert installation.address == {"city": "Berlin"}
    for collection in (result.errors_by_device_id, installation.address):
        assert isinstance(collection, Mapping)
        setitem_method = "__setitem__"
        with pytest.raises((AttributeError, TypeError)):
            getattr(collection, setitem_method)("changed", "value")


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
