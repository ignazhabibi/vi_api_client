"""Tests for vitoclient.api module (Flat Architecture)."""

import re
from copy import deepcopy
from dataclasses import replace

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client.api import ViClient
from vi_api_client.auth import AbstractAuth
from vi_api_client.const import (
    API_BASE_URL,
    ENDPOINT_FEATURES,
    ENDPOINT_GATEWAYS,
    ENDPOINT_INSTALLATIONS,
)
from vi_api_client.exceptions import (
    ViAuthError,
    ViConnectionError,
    ViNotFoundError,
    ViRateLimitError,
    ViResponseError,
    ViServerInternalError,
    ViValidationError,
)
from vi_api_client.models import Device, FeatureControl


class MockAuth(AbstractAuth):
    """Mock implementation of AbstractAuth for testing."""

    def __init__(self, session: aiohttp.ClientSession):
        super().__init__(session)
        self._access_token = "mock_access_token"

    async def async_get_access_token(self) -> str:
        return self._access_token


def _build_gateway_device(device_id: str) -> Device:
    """Build a device for gateway-scoped refresh tests."""
    return Device(
        id=device_id,
        gateway_serial="gateway-1",
        installation_id="installation-1",
        model_id=f"model-{device_id}",
        device_type="heating",
        status="connected",
    )


@pytest.mark.asyncio
async def test_update_gateway_devices_refreshes_multiple_devices_with_one_request(
    load_fixture_json,
):
    # Arrange: Mock a gateway response with requested and unrelated features.
    response = load_fixture_json("gateway_device_features.json")
    devices = [_build_gateway_device("10"), _build_gateway_device("0")]
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/features/filter"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(url, payload=response)
        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act: Refresh both devices through the public gateway operation.
            result = await client.update_gateway_devices(devices)

    # Assert: Both devices are refreshed in input order by one bulk request.
    assert result.is_complete
    assert [device.id for device in result.updated_devices] == ["10", "0"]
    assert result.updated_devices[0].model_id == "model-10"
    supply_temperature = result.updated_devices[0].get_feature(
        "heating.sensors.temperature.supply"
    )
    outside_temperature = result.updated_devices[1].get_feature(
        "heating.sensors.temperature.outside"
    )
    heating_curve_slope = result.updated_devices[1].get_feature(
        "heating.circuits.0.heating.curve.slope"
    )
    assert supply_temperature is not None
    assert outside_temperature is not None
    assert heating_curve_slope is not None
    assert supply_temperature.value == 34.2
    assert outside_temperature.value == 5.5
    assert heating_curve_slope.is_writable
    assert len(mock_responses.requests) == 1
    request = next(iter(mock_responses.requests.values()))[0]
    assert request.kwargs["json"] == {
        "includeDevicesFeatures": True,
        "skipDisabled": True,
        "skipNotReady": True,
    }


@pytest.mark.asyncio
async def test_update_gateway_devices_decodes_complete_device_uri_segments():
    # Arrange: Return a feature for a device ID containing an encoded slash.
    response = {
        "data": [
            {
                "feature": "heating.status",
                "properties": {"value": "ready"},
                "uri": (
                    "/iot/v2/features/installations/installation-1/gateways/"
                    "gateway-1/devices/device%2F0/features/heating.status"
                ),
            }
        ]
    }
    device = _build_gateway_device("device/0")
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/features/filter"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(url, payload=response)
        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act: Refresh the encoded device ID.
            result = await client.update_gateway_devices([device])

    # Assert: The decoded complete segment maps to the requested device.
    assert result.is_complete
    assert result.updated_devices[0].id == "device/0"
    heating_status = result.updated_devices[0].get_feature("heating.status")
    assert heating_status is not None
    assert heating_status.value == "ready"


@pytest.mark.asyncio
async def test_update_gateway_devices_accepts_empty_input_without_request():
    # Arrange: Create a client without registering any HTTP response.
    async with aiohttp.ClientSession() as session:
        client = ViClient(MockAuth(session))

        # Act: Refresh an empty gateway device collection.
        result = await client.update_gateway_devices([])

    # Assert: Empty input is a complete result and performs no I/O.
    assert result.is_complete
    assert result.updated_devices == []
    assert result.errors_by_device_id == {}


@pytest.mark.parametrize(
    ("devices", "message"),
    [
        (
            [
                _build_gateway_device("0"),
                replace(_build_gateway_device("1"), gateway_serial="gateway-2"),
            ],
            "same installation and gateway",
        ),
        (
            [
                _build_gateway_device("0"),
                replace(_build_gateway_device("1"), installation_id="installation-2"),
            ],
            "same installation and gateway",
        ),
        (
            [_build_gateway_device("0"), _build_gateway_device("0")],
            "unique IDs",
        ),
    ],
)
@pytest.mark.asyncio
async def test_update_gateway_devices_rejects_ambiguous_device_sets(
    devices: list[Device], message: str
):
    # Arrange: Create a client without registering any HTTP response.
    async with aiohttp.ClientSession() as session:
        client = ViClient(MockAuth(session))

        # Act and assert: Invalid device collections fail before network access.
        with pytest.raises(ValueError, match=message):
            await client.update_gateway_devices(devices)


@pytest.mark.parametrize(
    "response",
    [
        [],
        {"data": {}},
        {"data": ["not-an-object"]},
        {"data": [{"uri": "/iot/v2/features/devices"}]},
        {"data": [{"uri": "/iot/v2/features/devices/%ZZ/features/heating"}]},
        {"data": [{"uri": "/iot/v2/features/devices/0/features/missing.feature"}]},
        {"data": [{"uri": ("/iot/v2/features/devices/0/features/devices/10/heating")}]},
        {
            "data": [
                {
                    "uri": "/iot/v2/features/devices/0/features/heating",
                    "properties": [],
                }
            ]
        },
    ],
)
@pytest.mark.asyncio
async def test_update_gateway_devices_rejects_invalid_bulk_responses(response):
    # Arrange: Return a malformed successful response from the gateway endpoint.
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/features/filter"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(url, payload=response)
        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act and assert: Invalid response ownership is a public response error.
            with pytest.raises(ViResponseError):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.asyncio
async def test_update_gateway_devices_falls_back_only_for_missing_devices(
    load_fixture_json,
):
    # Arrange: The bulk response includes device 10 but omits device 0.
    fixture = load_fixture_json("gateway_device_features.json")
    bulk_response = {
        "data": [
            feature for feature in fixture["data"] if "/devices/10/" in feature["uri"]
        ]
    }
    devices = [_build_gateway_device("10"), _build_gateway_device("0")]
    gateway_url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/features/filter"
    )
    device_url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/devices/0/features/filter"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(gateway_url, payload=bulk_response)
        mock_responses.post(device_url, payload={"data": []})
        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act: Refresh the two devices.
            result = await client.update_gateway_devices(devices)

    # Assert: Only the absent device uses the fallback; empty features are successful.
    assert result.is_complete
    assert [device.id for device in result.updated_devices] == ["10", "0"]
    assert result.updated_devices[1].features == []
    assert len(mock_responses.requests) == 2
    assert any(
        method == "POST" and str(request_url) == device_url
        for method, request_url in mock_responses.requests
    )


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, "DEVICE_COMMUNICATION_ERROR"),
        (404, "DEVICE_NOT_FOUND"),
        (403, "PACKAGE_NOT_PAID_FOR"),
    ],
)
@pytest.mark.asyncio
async def test_update_gateway_devices_captures_device_specific_fallback_errors(
    status: int, error_type: str, load_fixture_json
):
    # Arrange: Gateway communication fails and device 0 then fails specifically.
    fixture = load_fixture_json("gateway_device_features.json")
    device_10_response = {
        "data": [
            feature for feature in fixture["data"] if "/devices/10/" in feature["uri"]
        ]
    }
    base_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/gateway-1"
    devices = [_build_gateway_device("10"), _build_gateway_device("0")]

    with aioresponses() as mock_responses:
        mock_responses.post(
            f"{base_url}/features/filter",
            status=400,
            payload={
                "message": "Gateway unavailable",
                "errorType": "DEVICE_COMMUNICATION_ERROR",
            },
        )
        mock_responses.post(
            f"{base_url}/devices/10/features/filter", payload=device_10_response
        )
        mock_responses.post(
            f"{base_url}/devices/0/features/filter",
            status=status,
            payload={"message": "Device unavailable", "errorType": error_type},
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act: Refresh through the public gateway operation.
            result = await client.update_gateway_devices(devices)

    # Assert: Successful devices and per-device errors remain independent.
    assert not result.is_complete
    assert [device.id for device in result.updated_devices] == ["10"]
    assert set(result.errors_by_device_id) == {"0"}
    assert result.errors_by_device_id["0"].error_type == error_type


@pytest.mark.parametrize(
    ("status", "error_type", "expected_error"),
    [
        (401, "UNAUTHORIZED", ViAuthError),
        (429, "RATE_LIMIT_EXCEEDED", ViRateLimitError),
        (500, "INTERNAL_ERROR", ViServerInternalError),
        (400, "UNKNOWN_VALIDATION_ERROR", ViValidationError),
    ],
)
@pytest.mark.asyncio
async def test_update_gateway_devices_propagates_global_gateway_errors(
    status: int, error_type: str, expected_error: type[Exception]
):
    # Arrange: Return a non-fallback gateway error.
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/features/filter"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(
            url,
            status=status,
            payload={"message": "Global failure", "errorType": error_type},
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act and assert: Global failures abort the entire refresh.
            with pytest.raises(expected_error):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.asyncio
async def test_update_gateway_devices_propagates_connection_errors():
    # Arrange: Do not register the bulk endpoint, causing a network failure.
    with aioresponses():
        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act and assert: Connection failures abort the entire refresh.
            with pytest.raises(ViConnectionError):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.asyncio
async def test_update_gateway_devices_translates_malformed_fallback_response():
    # Arrange: Trigger fallback and return invalid feature properties.
    base_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/gateway-1"
    with aioresponses() as mock_responses:
        mock_responses.post(f"{base_url}/features/filter", payload={"data": []})
        mock_responses.post(
            f"{base_url}/devices/0/features/filter",
            payload={"data": [{"feature": "broken", "properties": []}]},
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act and assert: Fallback contract failures use the public error.
            with pytest.raises(ViResponseError):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.asyncio
async def test_get_installations(load_fixture_json):
    """Test fetching installations."""
    # Arrange: Load fixture and mock API endpoint for installations.
    data = load_fixture_json("installations.json")

    with aioresponses() as m:
        url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # Act: Fetch installations from API.
            installations = await client.get_installations()

            # Assert: Should return 2 installations with correct IDs.
            assert len(installations) == 2
            assert installations[0].id == "123456"
            assert installations[1].id == "789012"


@pytest.mark.asyncio
async def test_get_installations_error():
    """Test error handling when fetching installations fails."""
    # Arrange: Mock API to return 500 Internal Server Error.
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"

    with aioresponses() as m:
        m.get(url, status=500)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # Act and Assert: Fetch should raise ViServerInternalError.
            with pytest.raises(ViServerInternalError):
                await client.get_installations()


@pytest.mark.asyncio
async def test_get_gateways(load_fixture_json):
    """Test fetching gateways."""
    # Arrange: Load fixture and mock gateways endpoint.
    data = load_fixture_json("gateways.json")
    url = f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"

    with aioresponses() as m:
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # Act: Fetch gateways from API.
            gateways = await client.get_gateways()

            # Assert: Should return 1 gateway with correct serial.
            assert len(gateways) == 1
            assert gateways[0].serial == "1234567890"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "method"),
    [
        (ENDPOINT_INSTALLATIONS, "get_installations"),
        (ENDPOINT_GATEWAYS, "get_gateways"),
    ],
)
async def test_discovery_rejects_successful_non_json_responses(endpoint, method):
    """Discovery should reject successful responses that are not JSON objects."""
    # Arrange: Return non-JSON content from each discovery endpoint.
    url = f"{API_BASE_URL}{endpoint}"

    with aioresponses() as mock_responses:
        mock_responses.get(url, body="not JSON", content_type="text/plain")
        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act and assert: The public response error communicates the contract failure.
            with pytest.raises(ViResponseError):
                await getattr(client, method)()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "method"),
    [
        (ENDPOINT_INSTALLATIONS, "get_installations"),
        (ENDPOINT_GATEWAYS, "get_gateways"),
    ],
)
async def test_discovery_rejects_successful_malformed_json_envelopes(endpoint, method):
    """Discovery should reject successful JSON that violates its envelope contract."""
    # Arrange: Return a JSON object whose data member is not a collection.
    url = f"{API_BASE_URL}{endpoint}"

    with aioresponses() as mock_responses:
        mock_responses.get(url, payload={"data": {}})
        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act and assert: The public response error communicates the contract failure.
            with pytest.raises(ViResponseError):
                await getattr(client, method)()


@pytest.mark.asyncio
async def test_discovery_keeps_a_caller_managed_session_open(load_fixture_json):
    """Discovery must not close a session supplied through authentication."""
    # Arrange: Provide a caller-owned session and successful installation envelope.
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
    with aioresponses() as mock_responses:
        mock_responses.get(url, payload=load_fixture_json("installations.json"))
        session = aiohttp.ClientSession()
        try:
            client = ViClient(MockAuth(session))

            # Act: Run discovery through the adapter-backed client.
            await client.get_installations()

            # Assert: The client did not create or close a replacement session.
            assert not session.closed
        finally:
            await session.close()


@pytest.mark.asyncio
async def test_get_full_installation_status_uses_matching_gateways_only():
    """Full status should not query gateways from other installations."""
    # Arrange: Mock one gateway for each of two installations.
    installation_id = "installation-a"
    matching_gateway = "gateway-a"
    other_gateway = "gateway-b"
    gateways_url = f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"
    devices_url = (
        f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/{installation_id}/gateways/"
        f"{matching_gateway}/devices"
    )

    with aioresponses() as mock_responses:
        mock_responses.get(
            gateways_url,
            payload={
                "data": [
                    {
                        "installationId": installation_id,
                        "serial": matching_gateway,
                        "status": "connected",
                        "version": "1.0.0",
                    },
                    {
                        "installationId": "installation-b",
                        "serial": other_gateway,
                        "status": "connected",
                        "version": "1.0.0",
                    },
                ]
            },
        )
        mock_responses.get(devices_url, payload={"data": []})

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act: Fetch the complete status for the first installation.
            devices = await client.get_full_installation_status(installation_id)

    # Assert: Only the matching gateway should receive a devices request.
    requested_urls = [str(url) for _method, url in mock_responses.requests]
    assert devices == []
    assert devices_url in requested_urls
    assert other_gateway not in "".join(requested_urls)


@pytest.mark.asyncio
async def test_get_devices(load_fixture_json):
    """Test fetching devices for a gateway."""
    # Arrange: Load device fixture and mock devices endpoint.
    data = load_fixture_json("devices_heating.json")
    inst_id = "123456"
    gw_serial = "1234567890"
    url = (
        f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/{inst_id}/gateways/{gw_serial}/devices"
    )

    with aioresponses() as m:
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # Act: Fetch devices for specific gateway.
            devices = await client.get_devices(inst_id, gw_serial)

            # Assert: Should return 2 devices with correct properties.
            assert len(devices) == 2
            assert devices[0].id == "0"
            assert devices[0].device_type == "heating"


@pytest.mark.asyncio
async def test_get_features(load_fixture_json):
    """Test fetching all features for a device (Parsing check)."""
    # Arrange: Create device and mock features endpoint to return all features.
    data = load_fixture_json("features_heating_sensors.json")
    url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/filter"

    with aioresponses() as m:
        m.post(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            device = Device(
                id="0",
                gateway_serial="1234567890",
                installation_id="123456",
                model_id="test",
                device_type="heating",
                status="ok",
            )

            # Act: Fetch all features for the device.
            features = await client.get_features(device)

            # Assert: Verify all features are returned and parsed correctly.
            assert len(features) == 2
            assert features[0].name == "heating.sensors.temperature.outside"
            assert features[0].value == 5.5
            assert features[1].name == "heating.circuits.0.active"


@pytest.mark.asyncio
async def test_get_features_applies_enabled_ready_and_name_filters_after_response(
    load_fixture_json,
):
    """Live feature filtering should not rely only on server-side filter hints."""
    # Arrange: Return requested, disabled, and not-ready features despite filter hints.
    data = deepcopy(load_fixture_json("features_heating_sensors.json"))
    data["data"][0]["isEnabled"] = False
    not_ready_feature = deepcopy(data["data"][1])
    not_ready_feature["feature"] = "test.notReady"
    not_ready_feature["isEnabled"] = True
    not_ready_feature["isReady"] = False
    data["data"].append(not_ready_feature)
    device = Device(
        id="0",
        gateway_serial="1234567890",
        installation_id="123456",
        model_id="test",
        device_type="heating",
        status="ok",
    )
    url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/123456/gateways/1234567890/devices/0/features/filter"

    with aioresponses() as mock_responses:
        mock_responses.post(url, payload=data)
        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act: Ask for a specific enabled and ready feature.
            features = await client.get_features(
                device,
                only_enabled=True,
                feature_names=["heating.circuits.0.active", "test.notReady.active"],
            )

    # Assert: Client-side filtering enforces the public semantics.
    assert [feature.name for feature in features] == ["heating.circuits.0.active"]


@pytest.mark.asyncio
async def test_get_feature(load_fixture_json):
    """Test fetching a specific feature."""
    # Arrange: Create device and mock features endpoint to return a single filtered feature.
    data = load_fixture_json("features_filtered_single.json")
    url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/filter"

    with aioresponses() as m:
        m.post(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            device = Device(
                id="0",
                gateway_serial="1234567890",
                installation_id="123456",
                model_id="test",
                device_type="heating",
                status="ok",
            )

            # Act: Fetch a specific feature by name.
            features = await client.get_features(
                device, feature_names=["heating.sensors.temperature.outside"]
            )

            # Assert: Verify only the requested feature is returned and parsed.
            assert len(features) == 1
            feature = features[0]

            assert feature.name == "heating.sensors.temperature.outside"
            assert feature.value == 5.5


@pytest.mark.asyncio
async def test_get_feature_not_found(load_fixture_json):
    """Test fetching a non-existent feature."""
    # Arrange: Create device and mock features endpoint to return 404 for a non-existent feature.
    data = load_fixture_json("device_error_404.json")
    url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/filter"

    with aioresponses() as m:
        m.post(url, status=404, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            device = Device(
                id="0",
                gateway_serial="1234567890",
                installation_id="123456",
                model_id="test",
                device_type="heating",
                status="ok",
            )

            # Act and Assert: Execute and verify in one step.
            with pytest.raises(ViNotFoundError):
                await client.get_features(device, feature_names=["nonexistent.feature"])


@pytest.mark.asyncio
async def test_update_device(load_fixture_json):
    """Test efficient device update."""
    # Arrange: Prepare test data and fixtures.
    data = load_fixture_json("update_device_response.json")
    url = f"{API_BASE_URL}/iot/v2/features/installations/123/gateways/GW1/devices/0/features/filter"

    with aioresponses() as m:
        m.post(url, payload=data)

        # Device has context
        dev = Device(
            id="0",
            gateway_serial="GW1",
            installation_id="123",
            model_id="TestModel",
            device_type="heating",
            status="ok",
        )

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Act: Execute the function being tested.
            updated_dev = await client.update_device(dev)

            # Assert: Verify the results match expectations.
            assert updated_dev.id == "0"
            assert len(updated_dev.features) == 1
            assert updated_dev.features[0].name == "new.feature"


@pytest.mark.asyncio
async def test_validate_constraints_step():
    """Test step validation logic."""
    # Arrange: Create test values for step validation.
    # Use a mock/stub since we just want to test the _validate_constraints method logic
    client = ViClient(None)  # type: ignore

    # Mode 1: Valid Step
    ctrl = FeatureControl(
        command_name="set",
        param_name="p",
        required_params=[],
        parent_feature_name="x",
        uri="x",
        min=10,
        max=30,
        step=0.5,
    )

    # Act & Assert: Case 1 (Valid Step)
    client._validate_numeric_constraints(ctrl, 10.5)  # Should pass
    client._validate_numeric_constraints(ctrl, 11.0)  # Should pass

    # Act & Assert: Case 2 (Invalid Step)
    with pytest.raises(ValueError) as exc:
        client._validate_numeric_constraints(ctrl, 10.7)
    assert "does not align with step" in str(exc.value)

    # Act & Assert: Case 3 (Floating point precision)
    ctrl2 = FeatureControl(
        command_name="set",
        param_name="p",
        required_params=[],
        parent_feature_name="x",
        uri="x",
        min=0,
        max=1,
        step=0.1,
    )
    client._validate_numeric_constraints(ctrl2, 0.3)  # Should pass despite float arith


@pytest.mark.asyncio
async def test_get_devices_with_hydration(load_fixture_json):
    """Test fetching devices with automatic feature hydration."""
    # Arrange: Load fixtures.
    devices_data = load_fixture_json("devices_heating.json")
    features_data = load_fixture_json("features_heating_sensors.json")

    inst_id = "123456"
    gw_serial = "1234567890"

    devices_url = (
        f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/{inst_id}/gateways/{gw_serial}/devices"
    )

    with aioresponses() as m:
        # 1. Mock Devices Call
        m.get(devices_url, payload=devices_data)

        # 2. Mock Features Call (for any device ID on this gateway)
        features_pattern = re.compile(
            f"{API_BASE_URL}{ENDPOINT_FEATURES}/{inst_id}/gateways/{gw_serial}/devices/.*/features/filter"
        )
        m.post(features_pattern, payload=features_data, repeat=True)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # Act: Fetch devices with hydration enabled.
            devices = await client.get_devices(
                inst_id, gw_serial, include_features=True
            )

            # Assert:
            assert len(devices) == 2

            # Check Device 0 (Heating)
            dev0 = next(d for d in devices if d.id == "0")
            assert len(dev0.features) > 0
            assert dev0.features[0].name == "heating.sensors.temperature.outside"


@pytest.mark.asyncio
async def test_set_feature_with_dependency(load_fixture_json):
    """Test setting a feature that has a sibling dependency (slope needs shift)."""
    # Arrange
    fixtures_data = load_fixture_json("feature_heating_curve.json")

    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"

    # URL to fetch specific feature (or all features in this filter context)
    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"

    # URL for the command
    command_url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/"
        "features/heating.circuits.0.heating.curve/commands/setCurve"
    )

    with aioresponses() as m:
        # Mock Feature Fetching
        m.post(features_url, payload={"data": fixtures_data})

        # Mock Command Execution
        m.post(command_url, payload={"data": {"success": True}})

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # 1. Manually construct device
            device = Device(
                id=device_id,
                gateway_serial=gw_serial,
                installation_id=install_id,
                model_id="Vitocal250A",
                device_type="heatpump",
                status="Online",
            )

            # 2. Fetch features (this now uses our small fixture)
            features = await client.get_features(device)
            device = replace(device, features=features)

            # 3. Find the 'slope' feature
            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None
            assert slope_feature is not None

            # Act: Set slope to 1.2 and verify dependency resolution.
            # The fixture says 'shift' is 4.
            # Expect payload: { "slope": 1.2, "shift": 4 }
            response, _updated_device = await client.set_feature(
                device, slope_feature, 1.2
            )
            assert response.success

            # Assert
            # Find the call with the matching URL
            found_call = None
            for (method, url), calls in m.requests.items():
                if method == "POST" and str(url) == command_url:
                    found_call = calls[0]
                    break

            assert found_call is not None
            assert found_call.kwargs["json"] == {"slope": 1.2, "shift": 4}


@pytest.mark.asyncio
async def test_set_feature_validation_limit(load_fixture_json):
    """Test client-side validation for min/max limits."""
    fixtures_data = load_fixture_json("feature_heating_curve.json")
    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"
    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"

    with aioresponses() as m:
        m.post(features_url, payload={"data": fixtures_data})

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            device = Device(
                id=device_id,
                gateway_serial=gw_serial,
                installation_id=install_id,
                model_id="M",
                device_type="H",
                status="O",
            )
            device = replace(device, features=await client.get_features(device))

            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None

            # Act & Assert: Max limit violation (Max is 3.5).
            with pytest.raises(ValueError, match=r"Value 5.0 > max"):
                await client.set_feature(device, slope_feature, 5.0)


@pytest.mark.asyncio
async def test_set_feature_validation_step(load_fixture_json):
    """Test client-side validation for stepping."""
    fixtures_data = load_fixture_json("feature_heating_curve.json")
    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"
    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"

    with aioresponses() as m:
        m.post(features_url, payload={"data": fixtures_data})

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            device = Device(
                id=device_id,
                gateway_serial=gw_serial,
                installation_id=install_id,
                model_id="M",
                device_type="H",
                status="O",
            )
            device = replace(device, features=await client.get_features(device))

            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None

            # Act & Assert: Step violation (Step is 0.1, 1.25 is invalid).
            with pytest.raises(ValueError, match=r"does not align with step"):
                await client.set_feature(device, slope_feature, 1.25)


@pytest.mark.asyncio
async def test_set_feature_returns_updated_device(load_fixture_json):
    """Verify optimistic device update on success."""
    # Arrange: Load heating curve fixture and setup mocks.
    fixtures_data = load_fixture_json("feature_heating_curve.json")
    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"

    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"
    command_url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/"
        "features/heating.circuits.0.heating.curve/commands/setCurve"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(features_url, payload={"data": fixtures_data})
        mock_responses.post(command_url, payload={"data": {"success": True}})

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Create base device
            base_device = Device(
                id=device_id,
                gateway_serial=gw_serial,
                installation_id=install_id,
                model_id="Vitocal250A",
                device_type="heatpump",
                status="Online",
            )

            # Hydrate with features
            features = await client.get_features(base_device)
            device = replace(base_device, features=features)

            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None
            original_slope = slope_feature.value  # Should be 0.6 from fixture

            # Act: Set slope to new value.
            response, updated_device = await client.set_feature(
                device, slope_feature, 0.7
            )

            # Assert: Returned device should have updated slope value.
            assert response.success
            updated_slope_feature = updated_device.get_feature(
                "heating.circuits.0.heating.curve.slope"
            )
            assert updated_slope_feature is not None
            assert updated_slope_feature.value == 0.7
            assert original_slope == 0.6  # Original unchanged


@pytest.mark.asyncio
async def test_set_feature_returns_unchanged_device_on_failure(load_fixture_json):
    """Verify device unchanged on command failure."""
    # Arrange: Load fixture and mock API failure.
    fixtures_data = load_fixture_json("feature_heating_curve.json")
    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"

    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"
    command_url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/"
        "features/heating.circuits.0.heating.curve/commands/setCurve"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(features_url, payload={"data": fixtures_data})
        mock_responses.post(
            command_url,
            payload={"data": {"success": False, "reason": "Device unavailable"}},
        )

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Create base device
            base_device = Device(
                id=device_id,
                gateway_serial=gw_serial,
                installation_id=install_id,
                model_id="V",
                device_type="h",
                status="o",
            )

            # Hydrate with features
            features = await client.get_features(base_device)
            device = replace(base_device, features=features)

            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None
            original_slope = slope_feature.value

            # Act: Try to set value but command fails.
            response, updated_device = await client.set_feature(
                device, slope_feature, 0.7
            )

            # Assert: Response indicates failure and device unchanged.
            assert not response.success
            assert response.reason == "Device unavailable"
            returned_slope_feature = updated_device.get_feature(
                "heating.circuits.0.heating.curve.slope"
            )
            assert returned_slope_feature is not None
            assert returned_slope_feature.value == original_slope


@pytest.mark.asyncio
async def test_interdependent_features_use_optimistic_values(load_fixture_json):
    """Test that dependencies resolve from optimistic updates."""
    # Arrange: Load heating curve fixture with slope=0.6, shift=4.
    fixtures_data = load_fixture_json("feature_heating_curve.json")
    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"

    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"
    command_url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/"
        "features/heating.circuits.0.heating.curve/commands/setCurve"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(features_url, payload={"data": fixtures_data})
        # Mock two successful command executions
        mock_responses.post(
            command_url, payload={"data": {"success": True}}, repeat=True
        )

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # Create base device
            base_device = Device(
                id=device_id,
                gateway_serial=gw_serial,
                installation_id=install_id,
                model_id="V",
                device_type="h",
                status="o",
            )

            # Hydrate with features
            features = await client.get_features(base_device)
            device = replace(base_device, features=features)

            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            shift_feature = device.get_feature("heating.circuits.0.heating.curve.shift")
            assert slope_feature is not None
            assert shift_feature is not None

            # Act: Set slope first to 0.7.
            response1, device = await client.set_feature(device, slope_feature, 0.7)
            assert response1.success

            # Act: Immediately set shift to 7.0 using optimistically updated device.
            response2, device = await client.set_feature(device, shift_feature, 7.0)
            assert response2.success

            # Assert: Second API call should use slope=0.7 (from optimistic update).
            found_call = None
            for (method, url), calls in mock_responses.requests.items():
                if method == "POST" and str(url) == command_url and len(calls) == 2:
                    # Second call should have slope=0.7
                    found_call = calls[1]
                    break

            assert found_call is not None
            assert found_call.kwargs["json"] == {"slope": 0.7, "shift": 7.0}
