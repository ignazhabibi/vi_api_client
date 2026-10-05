"""Tests for the ViClient public workflows (Flat Architecture)."""

import re
from copy import deepcopy
from dataclasses import replace

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client._types import JsonValue
from vi_api_client.client import ViClient
from vi_api_client.const import (
    API_BASE_URL,
    ENDPOINT_FEATURES,
    ENDPOINT_GATEWAYS,
    ENDPOINT_INSTALLATIONS,
)
from vi_api_client.exceptions import (
    ViAuthError,
    ViConnectionError,
    ViRateLimitError,
    ViResponseError,
    ViServerInternalError,
    ViValidationError,
)
from vi_api_client.models import Device


@pytest.mark.asyncio
async def test_live_client_hides_raw_transport_access(static_token_auth):
    """Live clients should expose only typed client workflows."""
    # Arrange: Construct the client with an authenticated request provider.
    async with aiohttp.ClientSession() as session:
        client = ViClient(static_token_auth(session))

        # Assert: The former raw connector is not part of the client contract.
        assert not hasattr(client, "connector")


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
    static_token_auth,
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
            client = ViClient(static_token_auth(session))

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
async def test_update_gateway_devices_decodes_complete_device_uri_segments(
    static_token_auth,
):
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
            client = ViClient(static_token_auth(session))

            # Act: Refresh the encoded device ID.
            result = await client.update_gateway_devices([device])

    # Assert: The decoded complete segment maps to the requested device.
    assert result.is_complete
    assert result.updated_devices[0].id == "device/0"
    heating_status = result.updated_devices[0].get_feature("heating.status")
    assert heating_status is not None
    assert heating_status.value == "ready"


@pytest.mark.asyncio
async def test_update_gateway_devices_accepts_empty_input_without_request(
    static_token_auth,
):
    # Arrange: Create a client without registering any HTTP response.
    async with aiohttp.ClientSession() as session:
        client = ViClient(static_token_auth(session))

        # Act: Refresh an empty gateway device collection.
        result = await client.update_gateway_devices([])

    # Assert: Empty input is a complete result and performs no I/O.
    assert result.is_complete
    assert result.updated_devices == ()
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
    devices: list[Device], message: str, static_token_auth
):
    # Arrange: Create a client without registering any HTTP response.
    async with aiohttp.ClientSession() as session:
        client = ViClient(static_token_auth(session))

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
async def test_update_gateway_devices_rejects_invalid_bulk_responses(
    response, static_token_auth
):
    # Arrange: Return a malformed successful response from the gateway endpoint.
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/features/filter"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(url, payload=response)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: Invalid response ownership is a public response error.
            with pytest.raises(ViResponseError):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.asyncio
async def test_update_gateway_devices_uses_shared_response_validation(
    static_token_auth,
):
    """Gateway responses should fail with the same response messages as other reads."""
    # Arrange: Return a gateway response whose entry is not an object.
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/features/filter"
    )
    with aioresponses() as mock_responses:
        mock_responses.post(url, payload={"data": [None]})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The shared response validation names the resource.
            with pytest.raises(
                ViResponseError,
                match="Gateway feature response data entries must be objects",
            ):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.asyncio
async def test_update_gateway_devices_translates_duplicate_feature_names(
    static_token_auth,
):
    """Duplicate device features in a gateway response violate the API contract."""
    # Arrange: Return the same device feature twice from the gateway endpoint.
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/features/filter"
    )
    api_feature = {
        "feature": "heating.status",
        "uri": (
            "/iot/v2/features/installations/installation-1/gateways/gateway-1/"
            "devices/0/features/heating.status"
        ),
        "properties": {"value": {"type": "string", "value": "ready"}},
        "commands": {},
        "isEnabled": True,
        "isReady": True,
    }
    with aioresponses() as mock_responses:
        mock_responses.post(url, payload={"data": [api_feature, dict(api_feature)]})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The refresh reports a response error, not a ValueError.
            with pytest.raises(ViResponseError, match="Duplicate feature name"):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.parametrize(
    "uri",
    [None, 5],
    ids=["missing-uri", "non-string-uri"],
)
@pytest.mark.asyncio
async def test_update_gateway_devices_rejects_entries_without_valid_uris(
    uri, static_token_auth
):
    """Bulk entries without a usable device URI should reject as response errors."""
    # Arrange: Return a bulk entry whose device URI is missing or malformed.
    entry = {"feature": "heating.status", "properties": {"value": "ready"}, "uri": uri}
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/features/filter"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(url, payload={"data": [entry]})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The unusable URI becomes a public response error.
            with pytest.raises(ViResponseError, match="no valid URI"):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.asyncio
async def test_update_gateway_devices_rejects_undecodable_device_uris(
    static_token_auth,
):
    """Device URIs that fail strict decoding should reject as response errors."""
    # Arrange: Return a URI whose percent sequence is invalid UTF-8.
    entry = {
        "feature": "heating.status",
        "properties": {"value": "ready"},
        "uri": "/devices/%FF/features/heating.status",
    }
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/features/filter"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(url, payload={"data": [entry]})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The undecodable URI becomes a public response error.
            with pytest.raises(ViResponseError, match="an invalid URI"):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.asyncio
async def test_update_gateway_devices_falls_back_only_for_missing_devices(
    load_fixture_json,
    static_token_auth,
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
            client = ViClient(static_token_auth(session))

            # Act: Refresh the two devices.
            result = await client.update_gateway_devices(devices)

    # Assert: Only the absent device uses the fallback; empty features are successful.
    assert result.is_complete
    assert [device.id for device in result.updated_devices] == ["10", "0"]
    assert result.updated_devices[1].features == ()
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
    status: int, error_type: str, load_fixture_json, static_token_auth
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
            client = ViClient(static_token_auth(session))

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
    status: int, error_type: str, expected_error: type[Exception], static_token_auth
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
            client = ViClient(static_token_auth(session))

            # Act and assert: Global failures abort the entire refresh.
            with pytest.raises(expected_error):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.asyncio
async def test_update_gateway_devices_propagates_connection_errors(static_token_auth):
    # Arrange: Do not register the bulk endpoint, causing a network failure.
    with aioresponses():
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: Connection failures abort the entire refresh.
            with pytest.raises(ViConnectionError):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.asyncio
async def test_update_gateway_devices_translates_malformed_fallback_response(
    static_token_auth,
):
    # Arrange: Trigger fallback and return invalid feature properties.
    base_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/gateway-1"
    with aioresponses() as mock_responses:
        mock_responses.post(f"{base_url}/features/filter", payload={"data": []})
        mock_responses.post(
            f"{base_url}/devices/0/features/filter",
            payload={"data": [{"feature": "broken", "properties": []}]},
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: Fallback contract failures use the public error.
            with pytest.raises(ViResponseError):
                await client.update_gateway_devices([_build_gateway_device("0")])


@pytest.mark.asyncio
async def test_get_installations(load_fixture_json, static_token_auth):
    """Test fetching installations."""
    # Arrange: Load fixture and mock API endpoint for installations.
    data = load_fixture_json("installations.json")

    with aioresponses() as m:
        url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = static_token_auth(session)
            client = ViClient(auth)

            # Act: Fetch installations from API.
            installations = await client.get_installations()

            # Assert: Should return 2 installations with correct IDs.
            assert len(installations) == 2
            assert installations[0].id == "123456"
            assert installations[1].id == "789012"


@pytest.mark.asyncio
async def test_get_installations_error(static_token_auth):
    """Test error handling when fetching installations fails."""
    # Arrange: Mock API to return 500 Internal Server Error.
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"

    with aioresponses() as m:
        m.get(url, status=500)

        async with aiohttp.ClientSession() as session:
            auth = static_token_auth(session)
            client = ViClient(auth)

            # Act and assert: The public client raises the server error type.
            with pytest.raises(ViServerInternalError):
                await client.get_installations()


@pytest.mark.asyncio
async def test_get_gateways(load_fixture_json, static_token_auth):
    """Test fetching gateways."""
    # Arrange: Load fixture and mock gateways endpoint.
    data = load_fixture_json("gateways.json")
    url = f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"

    with aioresponses() as m:
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = static_token_auth(session)
            client = ViClient(auth)

            # Act: Fetch gateways from API.
            gateways = await client.get_gateways()

            # Assert: Should return 1 gateway with correct serial.
            assert len(gateways) == 1
            assert gateways[0].serial == "1234567890"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "request_method", "operation", "arguments"),
    [
        (f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}", "get", "get_installations", ()),
        (f"{API_BASE_URL}{ENDPOINT_GATEWAYS}", "get", "get_gateways", ()),
        (
            f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/installation-1/gateways/gateway-1/devices",
            "get",
            "get_devices",
            ("installation-1", "gateway-1"),
        ),
        (
            f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/gateway-1/devices/0/features/filter",
            "post",
            "get_features",
            (_build_gateway_device("0"),),
        ),
    ],
)
async def test_discovery_rejects_successful_non_json_responses(
    url, request_method, operation, arguments, static_token_auth
):
    """Discovery should reject successful responses that are not JSON objects."""
    # Arrange: Return non-JSON content from each discovery endpoint.
    with aioresponses() as mock_responses:
        getattr(mock_responses, request_method)(
            url, body="not JSON", content_type="text/plain"
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The public response error communicates the contract failure.
            with pytest.raises(ViResponseError):
                await getattr(client, operation)(*arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "operation", "arguments"),
    [
        (("get", f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"), "get_installations", ()),
        (("get", f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"), "get_gateways", ()),
        (
            (
                "get",
                f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/installation-1/gateways/gateway-1/devices",
            ),
            "get_devices",
            ("installation-1", "gateway-1"),
        ),
        (
            (
                "post",
                f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/gateway-1/devices/0/features/filter",
            ),
            "get_features",
            (_build_gateway_device("0"),),
        ),
    ],
)
@pytest.mark.parametrize(
    "response",
    [[], {}, {"data": {}}, {"data": [None]}],
    ids=["root-list", "missing-data", "data-not-list", "data-entry-not-object"],
)
async def test_discovery_rejects_successful_malformed_json_responses(
    endpoint, operation, arguments, response, static_token_auth
):
    """Discovery should reject successful JSON that violates its response contract."""
    # Arrange: Return JSON that violates a collection response requirement.
    with aioresponses() as mock_responses:
        request_method, url = endpoint
        getattr(mock_responses, request_method)(url, payload=response)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The public response error communicates the contract failure.
            with pytest.raises(ViResponseError):
                await getattr(client, operation)(*arguments)


@pytest.mark.asyncio
async def test_discovery_keeps_a_caller_managed_session_open(
    load_fixture_json, static_token_auth
):
    """Discovery must not close a session supplied through authentication."""
    # Arrange: Provide a caller-owned session and successful installation response.
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
    with aioresponses() as mock_responses:
        mock_responses.get(url, payload=load_fixture_json("installations.json"))
        session = aiohttp.ClientSession()
        try:
            client = ViClient(static_token_auth(session))

            # Act: Run discovery through the adapter-backed client.
            await client.get_installations()

            # Assert: The client did not create or close a replacement session.
            assert not session.closed
        finally:
            await session.close()


@pytest.mark.asyncio
async def test_get_full_installation_status_uses_matching_gateways_only(
    static_token_auth,
):
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
            client = ViClient(static_token_auth(session))

            # Act: Fetch the complete status for the first installation.
            devices = await client.get_full_installation_status(installation_id)

    # Assert: Only the matching gateway should receive a devices request.
    requested_urls = [str(url) for _method, url in mock_responses.requests]
    assert devices == []
    assert devices_url in requested_urls
    assert other_gateway not in "".join(requested_urls)


@pytest.mark.asyncio
async def test_get_full_installation_status_rejects_malformed_device_responses(
    static_token_auth,
):
    """Full status should preserve discovery response validation."""
    # Arrange: Return a matching gateway followed by invalid device collection entries.
    installation_id = "installation-1"
    gateway_serial = "gateway-1"
    gateways_url = f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"
    devices_url = (
        f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/{installation_id}/gateways/"
        f"{gateway_serial}/devices"
    )
    with aioresponses() as mock_responses:
        mock_responses.get(
            gateways_url,
            payload={
                "data": [
                    {
                        "installationId": installation_id,
                        "serial": gateway_serial,
                        "status": "connected",
                        "version": "1.0.0",
                    }
                ]
            },
        )
        mock_responses.get(devices_url, payload={"data": [None]})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The composed read keeps the public response error.
            with pytest.raises(ViResponseError, match="entries must be objects"):
                await client.get_full_installation_status(installation_id)


@pytest.mark.asyncio
async def test_get_devices(load_fixture_json, static_token_auth):
    """Test fetching devices for a gateway."""
    # Arrange: Load device fixture and fixture devices endpoint.
    data = load_fixture_json("devices_heating.json")
    inst_id = "123456"
    gw_serial = "1234567890"
    url = (
        f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/{inst_id}/gateways/{gw_serial}/devices"
    )

    with aioresponses() as m:
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = static_token_auth(session)
            client = ViClient(auth)

            # Act: Fetch devices for specific gateway.
            devices = await client.get_devices(inst_id, gw_serial)

            # Assert: Should return 2 devices with correct properties.
            assert len(devices) == 2
            assert devices[0].id == "0"
            assert devices[0].device_type == "heating"


@pytest.mark.asyncio
async def test_get_features(load_fixture_json, static_token_auth):
    """Test fetching all features for a device (Parsing check)."""
    # Arrange: Create device and mock features endpoint to return all features.
    data = load_fixture_json("features_heating_sensors.json")
    url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/filter"

    with aioresponses() as m:
        m.post(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = static_token_auth(session)
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
async def test_get_features_translates_duplicate_api_feature_names(
    load_fixture_json, static_token_auth
):
    # Arrange: Mock a response containing the same feature twice.
    data = load_fixture_json("features_heating_sensors.json")
    data["data"].append(deepcopy(data["data"][0]))
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
            client = ViClient(static_token_auth(session))

            # Act and assert: The client translates an invalid API response.
            with pytest.raises(ViResponseError, match="Duplicate feature name"):
                await client.get_features(
                    device, feature_names=["heating.sensors.temperature.outside"]
                )


@pytest.mark.asyncio
async def test_get_features_ignores_duplicates_outside_requested_names(
    load_fixture_json, static_token_auth
):
    """Duplicates only matter for features the client returns."""
    # Arrange: Duplicate a feature that the request does not select.
    data = load_fixture_json("features_heating_sensors.json")
    data["data"].append(deepcopy(data["data"][0]))
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/gateway-1/"
        "devices/0/features/filter"
    )

    with aioresponses() as mock_responses:
        mock_responses.post(url, payload=data)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Request only a feature that appears once.
            features = await client.get_features(
                _build_gateway_device("0"),
                feature_names=["heating.circuits.0.active"],
            )

    # Assert: The requested feature is returned without a duplicate error.
    assert [feature.name for feature in features] == ["heating.circuits.0.active"]


@pytest.mark.asyncio
async def test_get_features_applies_enabled_ready_and_name_filters_after_response(
    load_fixture_json,
    static_token_auth,
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
            client = ViClient(static_token_auth(session))

            # Act: Ask for a specific enabled and ready feature.
            features = await client.get_features(
                device,
                only_enabled=True,
                feature_names=["heating.circuits.0.active", "test.notReady.active"],
            )

    # Assert: Client-side filtering enforces the public semantics.
    assert [feature.name for feature in features] == ["heating.circuits.0.active"]


@pytest.mark.asyncio
async def test_get_feature(load_fixture_json, static_token_auth):
    """Test fetching a specific feature."""
    # Arrange: Create device and mock features endpoint to return a single filtered feature.
    data = load_fixture_json("features_filtered_single.json")
    url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/filter"

    with aioresponses() as m:
        m.post(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = static_token_auth(session)
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


@pytest.mark.parametrize(
    ("requested_name", "expected_names"),
    [
        (
            "heating.circuits.0.heating.curve",
            [
                "heating.circuits.0.heating.curve.shift",
                "heating.circuits.0.heating.curve.slope",
            ],
        ),
        (
            "heating.circuits.0.heating.curve.slope",
            ["heating.circuits.0.heating.curve.slope"],
        ),
        (
            "heating.circuits.0.name",
            ["heating.circuits.0.name", "heating.circuits.0.name.name"],
        ),
    ],
    ids=["api-feature-name", "feature-name", "feature-and-api-feature-name"],
)
@pytest.mark.asyncio
async def test_get_features_matches_feature_and_api_feature_names_locally(
    requested_name, expected_names, load_fixture_device, static_token_auth
):
    """Names select flat features by their own or their API feature's name."""
    # Arrange: Return a complete device feature response from the live API.
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/gateway-1/"
        "devices/0/features/filter"
    )
    with aioresponses() as mock_responses:
        mock_responses.post(url, payload=load_fixture_device("Vitocal250A"))
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Request features by one name.
            features = await client.get_features(
                _build_gateway_device("0"), feature_names=[requested_name]
            )

    # Assert: Matching is local, so the request carries no server-side name filter.
    assert sorted(feature.name for feature in features) == expected_names
    request = next(iter(mock_responses.requests.values()))[0]
    assert "filter" not in request.kwargs["json"]


@pytest.mark.asyncio
async def test_get_features_returns_nothing_for_unknown_names(
    load_fixture_json, static_token_auth
):
    """Unknown names select no features instead of failing the request."""
    # Arrange: Return a successful device feature response.
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/gateway-1/"
        "devices/0/features/filter"
    )
    with aioresponses() as mock_responses:
        mock_responses.post(
            url, payload=load_fixture_json("features_heating_sensors.json")
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Request a feature the device does not report.
            features = await client.get_features(
                _build_gateway_device("0"), feature_names=["nonexistent.feature"]
            )

    # Assert: The unknown name selects nothing.
    assert features == []


@pytest.mark.asyncio
async def test_update_device(load_fixture_json, static_token_auth):
    """Test efficient device update."""
    # Arrange: Load the refresh response fixture for one new feature.
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
            client = ViClient(static_token_auth(session))

            # Act: Refresh the device through the public client method.
            updated_dev = await client.update_device(dev)

            # Assert: The refreshed device exposes the fixture feature.
            assert updated_dev.id == "0"
            assert len(updated_dev.features) == 1
            assert updated_dev.features[0].name == "new.feature"


@pytest.mark.asyncio
async def test_update_device_rejects_malformed_feature_responses(static_token_auth):
    """Device refresh should preserve feature response validation."""
    # Arrange: Return an invalid feature collection for an existing device.
    device = _build_gateway_device("0")
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/"
        "gateway-1/devices/0/features/filter"
    )
    with aioresponses() as mock_responses:
        mock_responses.post(url, payload={"data": [None]})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The composed refresh keeps the public response error.
            with pytest.raises(ViResponseError, match="entries must be objects"):
                await client.update_device(device)


@pytest.mark.asyncio
async def test_get_devices_with_hydration(load_fixture_json, static_token_auth):
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
        # 1. Fixture Devices Call
        m.get(devices_url, payload=devices_data)

        # 2. Mock Features Call (for any device ID on this gateway)
        features_pattern = re.compile(
            f"{API_BASE_URL}{ENDPOINT_FEATURES}/{inst_id}/gateways/{gw_serial}/devices/.*/features/filter"
        )
        m.post(features_pattern, payload=features_data, repeat=True)

        async with aiohttp.ClientSession() as session:
            auth = static_token_auth(session)
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
async def test_set_feature_with_dependency(load_fixture_json, static_token_auth):
    """Test setting a feature that has a sibling dependency (slope needs shift)."""
    # Arrange: Load the curve fixture whose setCurve command requires slope and shift.
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
            client = ViClient(static_token_auth(session))

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

            # Act: Set slope to 1.2.
            response, _updated_device = await client.set_feature(
                device, slope_feature, 1.2
            )

            # Assert: The command succeeds with the sibling 'shift' resolved from the
            # fixture (shift is 4), so the payload is {"slope": 1.2, "shift": 4}.
            assert response.success
            found_call = None
            for (method, url), calls in m.requests.items():
                if method == "POST" and str(url) == command_url:
                    found_call = calls[0]
                    break

            assert found_call is not None
            assert found_call.kwargs["json"] == {"slope": 1.2, "shift": 4}


@pytest.mark.asyncio
async def test_set_feature_validation_limit(load_fixture_json, static_token_auth):
    """Test client-side validation for min/max limits."""
    fixtures_data = load_fixture_json("feature_heating_curve.json")
    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"
    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"

    with aioresponses() as m:
        m.post(features_url, payload={"data": fixtures_data})

        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

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

            # Act and assert: Max limit violation (Max is 3.5).
            with pytest.raises(ValueError, match=r"Value 5.0 > max"):
                await client.set_feature(device, slope_feature, 5.0)


@pytest.mark.asyncio
async def test_set_feature_validation_step(load_fixture_json, static_token_auth):
    """Client-side validation accepts aligned steps and rejects misaligned ones."""
    # Arrange: Load the heating curve fixture (slope step is 0.1) and mock writes.
    fixtures_data = load_fixture_json("feature_heating_curve.json")
    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"
    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"
    command_url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/"
        "features/heating.circuits.0.heating.curve/commands/setCurve"
    )

    with aioresponses() as m:
        m.post(features_url, payload={"data": fixtures_data})
        m.post(command_url, payload={"data": {"success": True}})

        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

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

            # Act: Set a step-aligned value whose float remainder is noisy (0.3 / 0.1).
            response, _updated_device = await client.set_feature(
                device, slope_feature, 0.3
            )

            # Assert: The epsilon comparison accepts accumulated float error.
            assert response.success

            # Act and assert: A misaligned value (step is 0.1, 1.25 is invalid) rejects.
            with pytest.raises(ValueError, match=r"does not align with step"):
                await client.set_feature(device, slope_feature, 1.25)


@pytest.mark.asyncio
async def test_set_feature_returns_updated_device(load_fixture_json, static_token_auth):
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
            client = ViClient(static_token_auth(session))

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
async def test_execute_command_preserves_explicit_parameters(
    load_fixture_json, static_token_auth
):
    """Execute an explicit command without enriching its parameter payload."""
    # Arrange: Load a writable feature and configure its command endpoint.
    fixtures_data = load_fixture_json("feature_heating_curve.json")
    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"
    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"
    command_url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/"
        "features/heating.circuits.0.heating.curve/commands/setCurve"
    )
    parameters: dict[str, JsonValue] = {"slope": 0.7, "shift": 7.0}

    with aioresponses() as mock_responses:
        mock_responses.post(features_url, payload={"data": fixtures_data})
        mock_responses.post(command_url, payload={"data": {"success": True}})

        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))
            device = Device(
                id=device_id,
                gateway_serial=gw_serial,
                installation_id=install_id,
                model_id="Vitocal250A",
                device_type="heatpump",
                status="Online",
            )
            device = replace(device, features=await client.get_features(device))
            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None

            # Act: Submit the caller's complete parameter set.
            response = await client.execute_command(slope_feature, parameters)

            # Assert: The adapter sends the supplied parameters unchanged.
            assert response.success
            found_call = next(
                call
                for (method, url), calls in mock_responses.requests.items()
                if method == "POST" and str(url) == command_url
                for call in calls
            )
            assert found_call.kwargs["json"] == parameters


@pytest.mark.asyncio
async def test_execute_command_rejects_malformed_success_response(
    load_fixture_json, static_token_auth
):
    """Translate malformed successful command responses into library errors."""
    # Arrange: Return a valid JSON value that violates the command response contract.
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
        mock_responses.post(command_url, payload=["unexpected"])

        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))
            device = Device(
                id=device_id,
                gateway_serial=gw_serial,
                installation_id=install_id,
                model_id="Vitocal250A",
                device_type="heatpump",
                status="Online",
            )
            device = replace(device, features=await client.get_features(device))
            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None

            # Act and assert: The public client exposes a library exception.
            with pytest.raises(
                ViResponseError, match="Command response must be an object"
            ):
                await client.execute_command(slope_feature, {"slope": 0.7, "shift": 4})


@pytest.mark.asyncio
async def test_set_feature_returns_unchanged_device_on_failure(
    load_fixture_json, static_token_auth
):
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
            client = ViClient(static_token_auth(session))

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
async def test_interdependent_features_use_optimistic_values(
    load_fixture_json, static_token_auth
):
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
            client = ViClient(static_token_auth(session))

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
