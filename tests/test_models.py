"""Tests for the immutable data models."""

from collections.abc import Mapping, Sequence

import pytest

from vi_api_client import JsonValue
from vi_api_client.exceptions import ViError
from vi_api_client.models import (
    Device,
    Feature,
    FeatureConstraints,
    GatewayDeviceRefreshResult,
    Installation,
    ParameterWriteCommand,
)


def test_built_feature_without_write_command_is_read_only():
    """A hand-built feature reports that it cannot be written."""
    # Act: Create a feature without a write command.
    feature = Feature(
        name="test.feature", value=10, unit="C", is_enabled=True, is_ready=True
    )

    # Assert: Without a write command the feature cannot be written and carries
    # no command knowledge on its public model.
    assert feature.constraints is None
    assert feature.is_writable is False
    assert feature._write_command is None


@pytest.mark.parametrize(
    (
        "is_enabled",
        "is_ready",
        "has_write_command",
        "expect_available",
        "expect_settable",
    ),
    [
        pytest.param(False, False, False, False, False, id="disabled-not-ready"),
        pytest.param(False, True, False, False, False, id="disabled-ready"),
        pytest.param(True, False, False, False, False, id="enabled-not-ready"),
        pytest.param(True, True, False, True, False, id="enabled-ready-readonly"),
        pytest.param(False, False, True, False, False, id="writable-unavailable"),
        pytest.param(False, True, True, False, False, id="writable-disabled"),
        pytest.param(True, False, True, False, False, id="writable-not-ready"),
        pytest.param(True, True, True, True, True, id="writable-available"),
    ],
)
def test_feature_reports_availability_and_settability(
    is_enabled: bool,
    is_ready: bool,
    has_write_command: bool,
    expect_available: bool,
    expect_settable: bool,
):
    """Availability needs enabled and ready; settability adds writability."""
    # Arrange: Build one feature for every flag combination.
    feature = Feature(
        name="test.feature",
        value=1,
        unit=None,
        is_enabled=is_enabled,
        is_ready=is_ready,
        _write_command=ParameterWriteCommand(
            api_feature_name="test.api.feature",
            command_name="setValue",
            parameter_name="value",
            required_parameters=[],
            uri="/features/test.api.feature/commands/setValue",
        )
        if has_write_command
        else None,
    )

    # Assert: The derived flags compose the input flags exactly.
    assert feature.is_available is expect_available
    assert feature.is_settable is expect_settable


def test_feature_value_is_not_recursively_frozen():
    """Feature values keep the caller's JSON objects instead of frozen copies."""
    # Arrange: Create a feature with a caller-owned arbitrary payload.
    value: dict[str, JsonValue] = {"entries": [1]}
    feature = Feature(
        name="test.feature", value=value, unit=None, is_enabled=True, is_ready=True
    )

    # Act: Mutate the arbitrary value after construction.
    entries = value["entries"]
    assert isinstance(entries, list)
    entries.append(2)

    # Assert: The model collection contract does not recursively freeze Any values.
    assert feature.value == {"entries": [1, 2]}


def test_device_features_are_immutable_and_keep_lookup_in_sync():
    """A device snapshot ignores later changes to the caller's feature list."""
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

    # Assert: The snapshot and its name lookup ignore the later mutation.
    assert isinstance(device.features, Sequence)
    assert not isinstance(device.features, list)
    assert device.features == (first_feature,)
    assert device.get_feature("f1") is first_feature
    assert device.get_feature("f2") is None


def test_device_rejects_duplicate_feature_names():
    """Feature names identify features, so a device cannot hold two alike."""
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
    """Write commands, refresh results, and installations copy caller collections."""
    # Arrange: Create models from caller-owned mutable collections.
    required_parameters = ["target"]
    options = ["eco"]
    binding = ParameterWriteCommand(
        api_feature_name="parent",
        command_name="set",
        parameter_name="target",
        required_parameters=required_parameters,
        uri="url",
    )
    constraints = FeatureConstraints(options=options)
    refreshed_devices = []
    errors_by_device_id = {"device-1": ViError("unavailable")}
    result = GatewayDeviceRefreshResult(refreshed_devices, errors_by_device_id)
    address = {"city": "Berlin"}
    installation = Installation("1", "Home", "Home", address)

    # Act: Mutate the original collections after construction.
    required_parameters.append("mode")
    options.append("comfort")
    refreshed_devices.append(Device("1", "gw", "inst", "model", "heating", "connected"))
    errors_by_device_id["device-2"] = ViError("offline")
    address["street"] = "Example Street"

    # Assert: Each public collection is an immutable defensive copy.
    assert binding.required_parameters == ("target",)
    assert constraints.options == ("eco",)
    assert result.refreshed_devices == ()
    assert list(result.errors_by_device_id) == ["device-1"]
    assert installation.address == {"city": "Berlin"}
    for collection in (result.errors_by_device_id, installation.address):
        assert isinstance(collection, Mapping)
        with pytest.raises(TypeError, match="does not support item assignment"):
            collection["changed"] = "value"  # type: ignore[index]


def test_gateway_device_refresh_result_reports_completeness():
    """A refresh result is complete only when no device reported an error."""
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
        refreshed_devices=[refreshed_device], errors_by_device_id={}
    )
    partial_result = GatewayDeviceRefreshResult(
        refreshed_devices=[refreshed_device],
        errors_by_device_id={"device-1": error},
    )

    # Assert: Completeness depends only on the device-specific error mapping.
    assert complete_result.is_complete
    assert not partial_result.is_complete
