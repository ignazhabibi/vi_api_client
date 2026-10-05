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
    ("raw_success", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
    ],
    ids=[
        "bool-true",
        "bool-false",
        "lower-true",
        "title-true",
        "upper-true",
        "lower-false",
        "title-false",
    ],
)
def test_command_response_normalizes_supported_success_representations(
    raw_success: JsonValue, expected: bool
):
    """Supported API success representations normalize to booleans."""
    # Arrange: The API reports success in a documented representation.
    data: dict[str, JsonValue] = {"data": {"success": raw_success}}

    # Act: Parse the command response.
    response = CommandResponse.from_api(data)

    # Assert: The success flag is a normalized boolean.
    assert response.success is expected


@pytest.mark.parametrize(
    "raw_success",
    ["yes", "1", 1, 0, 1.5, None, [], {}, ["true"]],
    ids=[
        "word-yes",
        "digit-text",
        "integer-one",
        "integer-zero",
        "float",
        "null",
        "empty-list",
        "empty-object",
        "list-of-text",
    ],
)
def test_command_response_rejects_malformed_success_representations(
    raw_success: JsonValue,
):
    """Unsupported success representations fail the response contract."""
    # Arrange: The API reports success in an unsupported representation.
    data: dict[str, JsonValue] = {"data": {"success": raw_success}}

    # Act and assert: The malformed known field raises a response error.
    with pytest.raises(ViResponseError, match="success"):
        CommandResponse.from_api(data)


@pytest.mark.parametrize(
    "data", [{"data": {}}, {}], ids=["empty-data-object", "empty-root"]
)
def test_command_response_requires_success_field(data: dict[str, JsonValue]):
    """A command response without a success flag violates the contract."""
    # Act and assert: The missing known field raises a response error.
    with pytest.raises(ViResponseError, match="success"):
        CommandResponse.from_api(data)


def test_command_response_validates_optional_text_fields():
    """Optional message and reason fields pass through as strings."""
    # Arrange: A successful response carries message and reason text.
    data: dict[str, JsonValue] = {
        "data": {"success": True, "message": "Command accepted", "reason": "queued"}
    }

    # Act: Parse the command response.
    response = CommandResponse.from_api(data)

    # Assert: The known text fields are exposed unchanged.
    assert response.success
    assert response.message == "Command accepted"
    assert response.reason == "queued"


@pytest.mark.parametrize(
    ("field_name", "expected_message", "expected_reason"),
    [
        ("message", None, "queued"),
        ("reason", "Command accepted", None),
    ],
    ids=["null-message", "null-reason"],
)
def test_command_response_allows_null_optional_text_fields(
    field_name: str, expected_message: str | None, expected_reason: str | None
):
    """Explicitly null message and reason fields are exposed as None."""
    # Arrange: One optional text field is null while the other remains valid.
    command_data: dict[str, JsonValue] = {
        "success": True,
        "message": "Command accepted",
        "reason": "queued",
    }
    command_data[field_name] = None

    # Act: Parse the command response.
    response = CommandResponse.from_api({"data": command_data})

    # Assert: Only the explicitly null field is exposed as None.
    assert response.message == expected_message
    assert response.reason == expected_reason


def test_command_response_allows_absent_optional_text_fields():
    """Absent message and reason fields default to None."""
    # Arrange: A successful response carries only the success flag.
    data: dict[str, JsonValue] = {"data": {"success": True}}

    # Act: Parse the command response.
    response = CommandResponse.from_api(data)

    # Assert: The optional text fields stay absent.
    assert response.success
    assert response.message is None
    assert response.reason is None


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("message", True),
        ("message", 42),
        ("message", []),
        ("message", {}),
        ("reason", False),
        ("reason", 42),
        ("reason", []),
        ("reason", {}),
    ],
    ids=[
        "message-bool",
        "message-number",
        "message-list",
        "message-object",
        "reason-bool",
        "reason-number",
        "reason-list",
        "reason-object",
    ],
)
def test_command_response_rejects_malformed_optional_text_fields(
    field_name: str, value: JsonValue
):
    """Supplied non-string message and reason fields fail the response contract."""
    # Arrange: The response supplies a known text field with a malformed
    # non-null JSON value.
    data: dict[str, JsonValue] = {"data": {"success": True, field_name: value}}

    # Act and assert: The malformed known field raises a response error.
    with pytest.raises(ViResponseError, match=field_name):
        CommandResponse.from_api(data)


def test_command_response_accepts_root_and_data_wrapped_payloads():
    """Command responses may arrive as the root object or inside data."""
    # Arrange: The same success flag arrives in both documented shapes.
    root_payload: dict[str, JsonValue] = {"success": "true"}
    wrapped_payload: dict[str, JsonValue] = {"data": {"success": "true"}}

    # Act: Parse both command responses.
    root_response = CommandResponse.from_api(root_payload)
    wrapped_response = CommandResponse.from_api(wrapped_payload)

    # Assert: Both shapes expose the same normalized result.
    assert root_response.success
    assert wrapped_response.success


def test_command_response_allows_unknown_fields():
    """Unknown additional command response fields remain forward-compatible."""
    # Arrange: The response carries an unknown additional field.
    data: dict[str, JsonValue] = {
        "data": {"success": True, "unknown": {"kept": ["field"]}}
    }

    # Act: Parse the command response.
    response = CommandResponse.from_api(data)

    # Assert: The unknown field does not fail parsing.
    assert response.success


def test_command_response_rejects_non_object_data():
    """A non-object data field fails the response contract."""
    # Arrange: The response wraps a non-object data field.
    data: dict[str, JsonValue] = {"data": "unexpected"}

    # Act and assert: The malformed response raises a response error.
    with pytest.raises(ViResponseError, match="object"):
        CommandResponse.from_api(data)
