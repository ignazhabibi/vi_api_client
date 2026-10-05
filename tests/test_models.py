"""Tests for the immutable data models and their API conversions."""

from collections.abc import Mapping, Sequence

import pytest

from vi_api_client import JsonValue
from vi_api_client.exceptions import ViError, ViResponseError
from vi_api_client.models import (
    CommandResponse,
    Device,
    Feature,
    FeatureControl,
    GatewayDeviceRefreshResult,
    Installation,
)


def test_feature_without_control_is_read_only():
    """A feature without command metadata reports that it cannot be written."""
    # Act: Create a feature without command metadata.
    feature = Feature(
        name="test.feature", value=10, unit="C", is_enabled=True, is_ready=True
    )

    # Assert: Without a control the feature cannot be written.
    assert feature.control is None
    assert feature.is_writable is False


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
    """Controls, refresh results, and installations copy caller collections."""
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
        with pytest.raises(TypeError, match="does not support item assignment"):
            collection["changed"] = "value"  # type: ignore[index]


def test_device_from_api_maps_fields_and_converts_integer_ids_to_text():
    """API devices map to model fields, with numeric IDs exposed as text."""
    # Arrange: The API may report a device ID as a JSON integer.
    data = {
        "id": 0,
        "modelId": "complex_model",
        "deviceType": "heatpump",
        "status": "Online",
    }

    # Act: Parse the API device within its gateway and installation.
    device = Device.from_api(data, "gw1", "inst1")

    # Assert: Every field is mapped and the identifier is consistently text.
    assert device == Device(
        id="0",
        gateway_serial="gw1",
        installation_id="inst1",
        model_id="complex_model",
        device_type="heatpump",
        status="Online",
    )


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
        updated_devices=[refreshed_device], errors_by_device_id={}
    )
    partial_result = GatewayDeviceRefreshResult(
        updated_devices=[refreshed_device],
        errors_by_device_id={"device-1": error},
    )

    # Assert: Completeness depends only on the device-specific error mapping.
    assert complete_result.is_complete
    assert not partial_result.is_complete


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        pytest.param({"data": {"success": True}}, (True, None, None), id="bool-true"),
        pytest.param(
            {"data": {"success": False}}, (False, None, None), id="bool-false"
        ),
        pytest.param(
            {"data": {"success": "true"}}, (True, None, None), id="lower-true"
        ),
        pytest.param(
            {"data": {"success": "True"}}, (True, None, None), id="title-true"
        ),
        pytest.param(
            {"data": {"success": "TRUE"}}, (True, None, None), id="upper-true"
        ),
        pytest.param(
            {"data": {"success": "false"}}, (False, None, None), id="lower-false"
        ),
        pytest.param(
            {"data": {"success": "False"}}, (False, None, None), id="title-false"
        ),
        pytest.param({"success": "true"}, (True, None, None), id="root-payload"),
        pytest.param(
            {"data": {"success": True, "message": "accepted", "reason": "queued"}},
            (True, "accepted", "queued"),
            id="message-and-reason",
        ),
        pytest.param(
            {"data": {"success": True, "message": None, "reason": "queued"}},
            (True, None, "queued"),
            id="null-message",
        ),
        pytest.param(
            {"data": {"success": True, "message": "accepted", "reason": None}},
            (True, "accepted", None),
            id="null-reason",
        ),
        pytest.param(
            {"data": {"success": True, "unknown": {"kept": ["field"]}}},
            (True, None, None),
            id="unknown-field",
        ),
    ],
)
def test_command_response_accepts_documented_payloads(
    payload: dict[str, JsonValue], expected: tuple[bool, str | None, str | None]
):
    """Documented success, message, and reason shapes parse to one response."""
    response = CommandResponse.from_api(payload)

    assert (response.success, response.message, response.reason) == expected


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        *[
            pytest.param({"data": {"success": raw}}, "success", id=f"success-{raw!r}")
            for raw in ["yes", "1", 1, 0, 1.5, None, [], {}, ["true"]]
        ],
        pytest.param({"data": {}}, "success", id="missing-success"),
        pytest.param({}, "success", id="empty-root"),
        *[
            pytest.param(
                {"data": {"success": True, field: value}},
                field,
                id=f"{field}-{type(value).__name__}",
            )
            for field in ["message", "reason"]
            for value in [True, 42, [], {}]
        ],
        pytest.param({"data": "unexpected"}, "object", id="data-not-object"),
    ],
)
def test_command_response_rejects_malformed_payloads(
    payload: dict[str, JsonValue], message: str
):
    """Malformed known fields fail the command response contract."""
    with pytest.raises(ViResponseError, match=message):
        CommandResponse.from_api(payload)
