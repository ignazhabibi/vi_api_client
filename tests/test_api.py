"""Tests for the ViClient public workflows (Flat Architecture)."""

from copy import deepcopy
from dataclasses import replace

import aiohttp
import pytest
from aioresponses import aioresponses
from builders import build_gateway_device
from yarl import URL

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

# The heating curve fixture names its command by this absolute URI, so writes
# reach it regardless of the device the test reads the feature for.
CURVE_COMMAND_URL = (
    f"{API_BASE_URL}{ENDPOINT_FEATURES}/123/gateways/GW123/devices/0/"
    "features/heating.circuits.0.heating.curve/commands/setCurve"
)


def _devices_url(installation_id: str, gateway_serial: str) -> str:
    """Return the device list URL of one gateway."""
    return (
        f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/{installation_id}/gateways/"
        f"{gateway_serial}/devices"
    )


def _device_features_url(device: Device) -> str:
    """Return the feature filter URL of one device."""
    return (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{device.installation_id}/gateways/"
        f"{device.gateway_serial}/devices/{device.id}/features/filter"
    )


def _gateway_features_url(device: Device) -> str:
    """Return the bulk feature filter URL of the gateway owning a device."""
    return (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{device.installation_id}/gateways/"
        f"{device.gateway_serial}/features/filter"
    )


@pytest.mark.asyncio
async def test_update_gateway_devices_refreshes_multiple_devices_with_one_request(
    load_fixture_json,
    static_token_auth,
):
    # Arrange: Mock a gateway response with requested and unrelated features.
    response = load_fixture_json("gateway_device_features.json")
    devices = [build_gateway_device("10"), build_gateway_device("0")]
    url = _gateway_features_url(devices[0])

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
    (request,) = mock_responses.requests[("POST", URL(url))]
    assert request.kwargs["json"] == {
        "includeDevicesFeatures": True,
        "skipDisabled": True,
        "skipNotReady": True,
    }


@pytest.mark.asyncio
async def test_update_gateway_devices_falls_back_only_for_omitted_devices_in_input_order(
    static_token_auth,
):
    # Arrange: The bulk response covers device 10 only, so device 0 needs its
    # own read.
    device_10 = build_gateway_device("10")
    device_0 = build_gateway_device("0")
    gateway_url = _gateway_features_url(device_10)
    bulk_response = {
        "data": [
            {
                "feature": "heating.status",
                "properties": {"value": "ready"},
                "uri": (
                    "/iot/v2/features/installations/installation-1/gateways/"
                    "gateway-1/devices/10/features/heating.status"
                ),
            }
        ]
    }

    with aioresponses() as mock_responses:
        mock_responses.post(gateway_url, payload=bulk_response)
        mock_responses.post(_device_features_url(device_0), payload={"data": []})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Refresh both devices through the public gateway operation.
            result = await client.update_gateway_devices([device_10, device_0])

    # Assert: Only the omitted device is read individually, and the result keeps
    # the caller's order across both refresh paths.
    assert set(mock_responses.requests) == {
        ("POST", URL(gateway_url)),
        ("POST", URL(_device_features_url(device_0))),
    }
    assert len(mock_responses.requests[("POST", URL(gateway_url))]) == 1
    assert result.is_complete
    assert [device.id for device in result.updated_devices] == ["10", "0"]
    assert result.updated_devices[0].model_id == "model-10"
    assert result.updated_devices[1].features == ()


@pytest.mark.asyncio
async def test_update_gateway_devices_ignores_gateway_owned_and_unrelated_device_features(
    static_token_auth,
):
    # Arrange: The bulk response mixes the requested device's feature with a
    # gateway feature and a feature of a device the caller did not request.
    device = build_gateway_device("0")
    url = _gateway_features_url(device)
    response = {
        "data": [
            {
                "feature": "heating.status",
                "properties": {"value": "ready"},
                "uri": "/devices/0/features/heating.status",
            },
            {
                "feature": "gateway.status",
                "properties": {"value": "online"},
                "uri": "/gateways/gateway-1/features/gateway.status",
            },
            {
                "feature": "device.serial",
                "properties": {"value": "other-device"},
                "uri": "/devices/99/features/device.serial",
            },
        ]
    }

    with aioresponses() as mock_responses:
        mock_responses.post(url, payload=response)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Refresh the only requested device.
            result = await client.update_gateway_devices([device])

    # Assert: Only the requested device's feature is exposed, and the bulk
    # request was enough, so no individual fallback read happened.
    assert list(mock_responses.requests) == [("POST", URL(url))]
    assert [feature.name for feature in result.updated_devices[0].features] == [
        "heating.status"
    ]


@pytest.mark.parametrize(
    ("devices", "message"),
    [
        (
            [build_gateway_device("0"), build_gateway_device("0")],
            "unique IDs",
        ),
        (
            [
                build_gateway_device("0"),
                replace(build_gateway_device("1"), gateway_serial="gateway-2"),
            ],
            "same installation and gateway",
        ),
    ],
    ids=["duplicate-device-ids", "different-gateways"],
)
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_http_requests")
async def test_update_gateway_devices_rejects_ambiguous_device_sets_before_any_request(
    devices: list[Device], message: str, static_token_auth
):
    # Arrange: Create a client; any HTTP request would fail the test.
    async with aiohttp.ClientSession() as session:
        client = ViClient(static_token_auth(session))

        # Act and assert: Duplicate IDs or mixed gateways cannot form one bulk
        # request, so they are rejected before any I/O.
        with pytest.raises(ValueError, match=message):
            await client.update_gateway_devices(devices)


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
    device = build_gateway_device("device/0")

    with aioresponses() as mock_responses:
        mock_responses.post(_gateway_features_url(device), payload=response)
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
@pytest.mark.usefixtures("no_http_requests")
async def test_update_gateway_devices_accepts_empty_input_without_request(
    static_token_auth,
):
    # Arrange: Create a client; any HTTP request would fail the test.
    async with aiohttp.ClientSession() as session:
        client = ViClient(static_token_auth(session))

        # Act: Refresh an empty gateway device collection.
        result = await client.update_gateway_devices([])

    # Assert: Empty input is a complete result and performs no I/O.
    assert result.is_complete
    assert result.updated_devices == ()
    assert result.errors_by_device_id == {}


_DUPLICATED_GATEWAY_FEATURE = {
    "feature": "heating.status",
    "uri": (
        "/iot/v2/features/installations/installation-1/gateways/gateway-1/"
        "devices/0/features/heating.status"
    ),
    "properties": {"value": {"type": "string", "value": "ready"}},
}


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ([], "response must be an object"),
        ({"data": {}}, "data must be a list"),
        ({"data": ["not-an-object"]}, "entries must be objects"),
        ({"data": [{"uri": "/iot/v2/features/devices"}]}, "URI has no device ID"),
        (
            {"data": [{"uri": "/iot/v2/features/devices/%ZZ/features/heating"}]},
            "invalid encoded URI",
        ),
        (
            {"data": [{"uri": "/iot/v2/features/devices/0/features/missing.feature"}]},
            "Feature name must be a non-empty string",
        ),
        (
            {
                "data": [
                    {"uri": "/iot/v2/features/devices/0/features/devices/10/heating"}
                ]
            },
            "ambiguous device ownership",
        ),
        (
            {
                "data": [
                    {
                        "feature": "heating",
                        "uri": "/iot/v2/features/devices/0/features/heating",
                        "properties": [],
                    }
                ]
            },
            "Feature properties must be an object",
        ),
        (
            {"data": [_DUPLICATED_GATEWAY_FEATURE, dict(_DUPLICATED_GATEWAY_FEATURE)]},
            "Duplicate feature name in API response: heating.status",
        ),
        (
            {"data": [{"feature": "heating.status", "properties": {}, "uri": None}]},
            "Gateway feature entry has no valid URI",
        ),
        (
            {"data": [{"feature": "heating.status", "properties": {}, "uri": 5}]},
            "Gateway feature entry has no valid URI",
        ),
        (
            {
                "data": [
                    {
                        "feature": "heating.status",
                        "properties": {},
                        "uri": "/devices/%FF/features/heating.status",
                    }
                ]
            },
            "Gateway feature entry has an invalid URI",
        ),
    ],
    ids=[
        "not-an-object",
        "data-not-a-list",
        "entry-not-an-object",
        "uri-without-device",
        "invalid-encoded-uri",
        "missing-feature-name",
        "ambiguous-ownership",
        "properties-not-an-object",
        "duplicate-feature-name",
        "missing-uri",
        "non-string-uri",
        "undecodable-uri",
    ],
)
@pytest.mark.asyncio
async def test_update_gateway_devices_rejects_invalid_bulk_responses(
    response, message, static_token_auth
):
    # Arrange: Return a malformed successful response from the gateway endpoint.
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(_gateway_features_url(device), payload=response)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: Each contract violation names its specific cause as
            # a public response error rather than a parsing exception.
            with pytest.raises(ViResponseError, match=message):
                await client.update_gateway_devices([device])


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (400, "DEVICE_COMMUNICATION_ERROR"),
        (404, "DEVICE_NOT_FOUND"),
        (403, "PACKAGE_NOT_PAID_FOR"),
    ],
    ids=["device-communication-error", "device-not-found", "package-not-paid-for"],
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
    devices = [build_gateway_device("10"), build_gateway_device("0")]

    with aioresponses() as mock_responses:
        mock_responses.post(
            _gateway_features_url(devices[0]),
            status=400,
            payload={
                "message": "Gateway unavailable",
                "errorType": "DEVICE_COMMUNICATION_ERROR",
            },
        )
        mock_responses.post(
            _device_features_url(devices[0]), payload=device_10_response
        )
        mock_responses.post(
            _device_features_url(devices[1]),
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
    ("status", "error_type", "expected_error", "message"),
    [
        (401, "UNAUTHORIZED", ViAuthError, "Unauthorized: Global failure"),
        (429, "RATE_LIMIT_EXCEEDED", ViRateLimitError, "Rate Limit Exceeded"),
        (500, "INTERNAL_ERROR", ViServerInternalError, "Server Error 500"),
        (400, "UNKNOWN_VALIDATION_ERROR", ViValidationError, "Global failure"),
    ],
    ids=["unauthorized", "rate-limited", "server-error", "non-fallback-validation"],
)
@pytest.mark.asyncio
async def test_update_gateway_devices_propagates_global_gateway_errors(
    status: int,
    error_type: str,
    expected_error: type[Exception],
    message: str,
    static_token_auth,
):
    # Arrange: Return a gateway error that does not trigger the device fallback.
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(
            _gateway_features_url(device),
            status=status,
            payload={"message": "Global failure", "errorType": error_type},
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: Global failures abort the entire refresh.
            with pytest.raises(expected_error, match=message):
                await client.update_gateway_devices([device])


@pytest.mark.asyncio
async def test_update_gateway_devices_propagates_connection_errors(static_token_auth):
    # Arrange: Do not register the bulk endpoint, causing a network failure.
    with aioresponses():
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: Connection failures abort the entire refresh.
            with pytest.raises(ViConnectionError, match="Network error"):
                await client.update_gateway_devices([build_gateway_device("0")])


@pytest.mark.asyncio
async def test_update_gateway_devices_translates_malformed_fallback_response(
    static_token_auth,
):
    # Arrange: An empty bulk response triggers the fallback, which then returns
    # invalid feature properties.
    device = build_gateway_device("0")
    with aioresponses() as mock_responses:
        mock_responses.post(_gateway_features_url(device), payload={"data": []})
        mock_responses.post(
            _device_features_url(device),
            payload={"data": [{"feature": "broken", "properties": []}]},
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: Fallback contract failures use the public error.
            with pytest.raises(ViResponseError, match="properties must be an object"):
                await client.update_gateway_devices([device])


@pytest.mark.asyncio
async def test_get_installations_returns_every_listed_installation(
    load_fixture_json, static_token_auth
):
    # Arrange: Answer the installation list with two installations.
    data = load_fixture_json("installations.json")

    with aioresponses() as mock_responses:
        mock_responses.get(f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}", payload=data)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Fetch installations from the API.
            installations = await client.get_installations()

    # Assert: Both installations are returned in response order.
    assert [installation.id for installation in installations] == [
        "123456",
        "789012",
    ]


@pytest.mark.asyncio
async def test_get_gateways_returns_the_listed_gateway(
    load_fixture_json, static_token_auth
):
    # Arrange: Answer the gateway list with one gateway.
    data = load_fixture_json("gateways.json")

    with aioresponses() as mock_responses:
        mock_responses.get(f"{API_BASE_URL}{ENDPOINT_GATEWAYS}", payload=data)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Fetch gateways from the API.
            gateways = await client.get_gateways()

    # Assert: The single gateway is identified by its serial.
    assert [gateway.serial for gateway in gateways] == ["1234567890"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "request_method", "operation", "arguments"),
    [
        (f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}", "get", "get_installations", ()),
        (f"{API_BASE_URL}{ENDPOINT_GATEWAYS}", "get", "get_gateways", ()),
        (
            _devices_url("installation-1", "gateway-1"),
            "get",
            "get_devices",
            ("installation-1", "gateway-1"),
        ),
        (
            _device_features_url(build_gateway_device("0")),
            "post",
            "get_features",
            (build_gateway_device("0"),),
        ),
    ],
    ids=["installations", "gateways", "devices", "features"],
)
async def test_discovery_rejects_successful_non_json_responses(
    url, request_method, operation, arguments, static_token_auth
):
    # Arrange: Return non-JSON content from each discovery endpoint.
    with aioresponses() as mock_responses:
        getattr(mock_responses, request_method)(
            url, body="not JSON", content_type="text/plain"
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The public response error communicates the contract failure.
            with pytest.raises(ViResponseError, match="not valid JSON"):
                await getattr(client, operation)(*arguments)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "call"),
    [
        (("get", f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"), ("get_installations", ())),
        (("get", f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"), ("get_gateways", ())),
        (
            ("get", _devices_url("installation-1", "gateway-1")),
            ("get_devices", ("installation-1", "gateway-1")),
        ),
        (
            ("post", _device_features_url(build_gateway_device("0"))),
            ("get_features", (build_gateway_device("0"),)),
        ),
    ],
    ids=["installations", "gateways", "devices", "features"],
)
@pytest.mark.parametrize(
    ("response", "message"),
    [
        ([], "response must be an object"),
        ({}, "data must be a list"),
        ({"data": {}}, "data must be a list"),
        ({"data": [None]}, "data entries must be objects"),
    ],
    ids=["root-list", "missing-data", "data-not-list", "data-entry-not-object"],
)
async def test_discovery_rejects_successful_malformed_json_responses(
    endpoint, call, response, message, static_token_auth
):
    # Arrange: Return JSON that violates a collection response requirement.
    request_method, url = endpoint
    operation, arguments = call
    with aioresponses() as mock_responses:
        getattr(mock_responses, request_method)(url, payload=response)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The public response error communicates the contract failure.
            with pytest.raises(ViResponseError, match=message):
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
    devices_url = _devices_url(installation_id, matching_gateway)

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
    devices_url = _devices_url(installation_id, gateway_serial)
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
async def test_get_devices_returns_the_devices_of_one_gateway(
    load_fixture_json, static_token_auth
):
    # Arrange: Answer the device list of one gateway with two devices.
    data = load_fixture_json("devices_heating.json")

    with aioresponses() as mock_responses:
        mock_responses.get(_devices_url("installation-1", "gateway-1"), payload=data)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Fetch the devices of the gateway.
            devices = await client.get_devices("installation-1", "gateway-1")

    # Assert: Both devices are returned with their scope and type.
    assert [device.id for device in devices] == ["0", "gateway"]
    assert devices[0].device_type == "heating"
    assert devices[0].installation_id == "installation-1"
    assert devices[0].gateway_serial == "gateway-1"


@pytest.mark.asyncio
async def test_get_features_returns_every_device_feature_as_flat_features(
    load_fixture_json, static_token_auth
):
    # Arrange: Answer the device feature read with a sensor and a circuit.
    data = load_fixture_json("features_heating_sensors.json")
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(_device_features_url(device), payload=data)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Fetch all features for the device.
            features = await client.get_features(device)

    # Assert: Each API property becomes one dot-named flat feature.
    assert [feature.name for feature in features] == [
        "heating.sensors.temperature.outside",
        "heating.circuits.0.active",
    ]
    assert features[0].value == 5.5


@pytest.mark.asyncio
async def test_get_features_translates_duplicate_api_feature_names(
    load_fixture_json, static_token_auth
):
    # Arrange: Mock a response containing the same feature twice.
    data = load_fixture_json("features_heating_sensors.json")
    data["data"].append(deepcopy(data["data"][0]))
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(_device_features_url(device), payload=data)
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
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(_device_features_url(device), payload=data)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Request only a feature that appears once.
            features = await client.get_features(
                device, feature_names=["heating.circuits.0.active"]
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
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(_device_features_url(device), payload=data)
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
    device = build_gateway_device("0")
    url = _device_features_url(device)
    with aioresponses() as mock_responses:
        mock_responses.post(url, payload=load_fixture_device("Vitocal250A"))
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Request features by one name.
            features = await client.get_features(device, feature_names=[requested_name])

    # Assert: Matching is local, so the request carries no server-side name filter.
    assert sorted(feature.name for feature in features) == expected_names
    (request,) = mock_responses.requests[("POST", URL(url))]
    assert "filter" not in request.kwargs["json"]


@pytest.mark.asyncio
async def test_get_features_returns_nothing_for_unknown_names(
    load_fixture_json, static_token_auth
):
    """Unknown names select no features instead of failing the request."""
    # Arrange: Return a successful device feature response.
    device = build_gateway_device("0")
    with aioresponses() as mock_responses:
        mock_responses.post(
            _device_features_url(device),
            payload=load_fixture_json("features_heating_sensors.json"),
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Request a feature the device does not report.
            features = await client.get_features(
                device, feature_names=["nonexistent.feature"]
            )

    # Assert: The unknown name selects nothing.
    assert features == []


@pytest.mark.asyncio
async def test_update_device_returns_a_new_device_with_the_read_features(
    load_fixture_json, static_token_auth
):
    # Arrange: Answer the refresh with one feature the device does not have yet.
    data = load_fixture_json("update_device_response.json")
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(_device_features_url(device), payload=data)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Refresh the device through the public client method.
            updated_device = await client.update_device(device)

    # Assert: The refresh is a new snapshot; the input device stays unhydrated.
    assert updated_device is not device
    assert updated_device.id == "0"
    assert [feature.name for feature in updated_device.features] == ["new.feature"]
    assert device.features == ()


@pytest.mark.asyncio
async def test_update_device_rejects_malformed_feature_responses(static_token_auth):
    # Arrange: Return an invalid feature collection for an existing device.
    device = build_gateway_device("0")
    with aioresponses() as mock_responses:
        mock_responses.post(_device_features_url(device), payload={"data": [None]})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The composed refresh keeps the public response error.
            with pytest.raises(ViResponseError, match="entries must be objects"):
                await client.update_device(device)


@pytest.mark.asyncio
async def test_get_devices_hydrates_each_device_with_its_own_features(
    load_fixture_json, static_token_auth
):
    # Arrange: Discover two devices and give each its own feature response, so
    # a device hydrated from the other's read would be detected.
    devices_data = load_fixture_json("devices_heating.json")

    with aioresponses() as mock_responses:
        mock_responses.get(
            _devices_url("installation-1", "gateway-1"), payload=devices_data
        )
        mock_responses.post(
            _device_features_url(build_gateway_device("0")),
            payload=load_fixture_json("features_heating_sensors.json"),
        )
        mock_responses.post(
            _device_features_url(build_gateway_device("gateway")),
            payload=load_fixture_json("update_device_response.json"),
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Fetch devices with hydration enabled.
            devices = await client.get_devices(
                "installation-1", "gateway-1", include_features=True
            )

    # Assert: Every discovered device carries the features of its own read.
    assert {
        device.id: [feature.name for feature in device.features] for device in devices
    } == {
        "0": ["heating.sensors.temperature.outside", "heating.circuits.0.active"],
        "gateway": ["new.feature"],
    }


@pytest.mark.asyncio
async def test_set_feature_sends_the_current_value_of_a_required_sibling(
    load_fixture_json, static_token_auth
):
    # Arrange: Read the curve whose setCurve command requires slope and shift.
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(
            _device_features_url(device),
            payload={"data": load_fixture_json("feature_heating_curve.json")},
        )
        mock_responses.post(CURVE_COMMAND_URL, payload={"data": {"success": True}})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))
            device = replace(device, features=await client.get_features(device))
            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None

            # Act: Set only the slope.
            response, _updated_device = await client.set_feature(
                device, slope_feature, 1.2
            )

    # Assert: The command also carries the fixture's current shift of 4.
    assert response.success
    (request,) = mock_responses.requests[("POST", URL(CURVE_COMMAND_URL))]
    assert request.kwargs["json"] == {"slope": 1.2, "shift": 4}


@pytest.mark.asyncio
async def test_set_feature_returns_a_new_device_with_the_written_value(
    load_fixture_json, static_token_auth
):
    # Arrange: Read the curve with a slope of 0.6 and accept the write.
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(
            _device_features_url(device),
            payload={"data": load_fixture_json("feature_heating_curve.json")},
        )
        mock_responses.post(CURVE_COMMAND_URL, payload={"data": {"success": True}})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))
            device = replace(device, features=await client.get_features(device))
            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None

            # Act: Set the slope to a new value.
            response, updated_device = await client.set_feature(
                device, slope_feature, 0.7
            )

    # Assert: The returned device has the new slope, while the input snapshot
    # keeps its value because devices are never mutated in place.
    assert response.success
    assert updated_device is not device
    updated_slope_feature = updated_device.get_feature(
        "heating.circuits.0.heating.curve.slope"
    )
    assert updated_slope_feature is not None
    assert updated_slope_feature.value == 0.7
    input_slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
    assert input_slope_feature is not None
    assert input_slope_feature.value == 0.6


@pytest.mark.asyncio
async def test_execute_command_preserves_explicit_parameters(
    load_fixture_json, static_token_auth
):
    # Arrange: Read a writable feature and accept its command.
    device = build_gateway_device("0")
    parameters: dict[str, JsonValue] = {"slope": 0.7, "shift": 7.0}

    with aioresponses() as mock_responses:
        mock_responses.post(
            _device_features_url(device),
            payload={"data": load_fixture_json("feature_heating_curve.json")},
        )
        mock_responses.post(CURVE_COMMAND_URL, payload={"data": {"success": True}})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))
            device = replace(device, features=await client.get_features(device))
            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None

            # Act: Submit the caller's complete parameter set.
            response = await client.execute_command(slope_feature, parameters)

    # Assert: The adapter sends the supplied parameters unchanged.
    assert response.success
    (request,) = mock_responses.requests[("POST", URL(CURVE_COMMAND_URL))]
    assert request.kwargs["json"] == parameters


@pytest.mark.asyncio
async def test_execute_command_rejects_malformed_success_response(
    load_fixture_json, static_token_auth
):
    # Arrange: Return a valid JSON value that violates the command response contract.
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(
            _device_features_url(device),
            payload={"data": load_fixture_json("feature_heating_curve.json")},
        )
        mock_responses.post(CURVE_COMMAND_URL, payload=["unexpected"])
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))
            device = replace(device, features=await client.get_features(device))
            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None

            # Act and assert: The public client exposes a library exception.
            with pytest.raises(
                ViResponseError, match="Command response must be an object"
            ):
                await client.execute_command(slope_feature, {"slope": 0.7, "shift": 4})


@pytest.mark.asyncio
async def test_set_feature_returns_the_original_device_when_the_api_rejects_the_write(
    load_fixture_json, static_token_auth
):
    # Arrange: Read the curve and let the API reject the command.
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(
            _device_features_url(device),
            payload={"data": load_fixture_json("feature_heating_curve.json")},
        )
        mock_responses.post(
            CURVE_COMMAND_URL,
            payload={"data": {"success": False, "reason": "Device unavailable"}},
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))
            device = replace(device, features=await client.get_features(device))
            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None

            # Act: Try to set a value the API then rejects.
            response, updated_device = await client.set_feature(
                device, slope_feature, 0.7
            )

    # Assert: The rejection reason is exposed and no new snapshot is created.
    assert not response.success
    assert response.reason == "Device unavailable"
    assert updated_device is device


@pytest.mark.asyncio
async def test_set_feature_sends_the_optimistic_value_of_a_previous_write(
    load_fixture_json, static_token_auth
):
    # Arrange: Read the curve with slope 0.6 and shift 4, and accept every write.
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(
            _device_features_url(device),
            payload={"data": load_fixture_json("feature_heating_curve.json")},
        )
        mock_responses.post(
            CURVE_COMMAND_URL, payload={"data": {"success": True}}, repeat=True
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))
            device = replace(device, features=await client.get_features(device))
            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            shift_feature = device.get_feature("heating.circuits.0.heating.curve.shift")
            assert slope_feature is not None
            assert shift_feature is not None

            # Act: Write the slope, then the shift on the device the first write
            # returned, without re-reading the features in between.
            slope_response, device = await client.set_feature(
                device, slope_feature, 0.7
            )
            shift_response, device = await client.set_feature(
                device, shift_feature, 7.0
            )

    # Assert: The second command carries the slope of the first write, not the
    # 0.6 originally read.
    assert slope_response.success
    assert shift_response.success
    _slope_request, shift_request = mock_responses.requests[
        ("POST", URL(CURVE_COMMAND_URL))
    ]
    assert shift_request.kwargs["json"] == {"slope": 0.7, "shift": 7.0}


@pytest.mark.parametrize(
    ("read", "expected_hint"),
    [
        (lambda client, device: client.get_features(device), False),
        (
            lambda client, device: client.get_features(device, only_enabled=True),
            True,
        ),
        (lambda client, device: client.update_device(device), True),
        (
            lambda client, device: client.update_device(device, only_enabled=False),
            False,
        ),
    ],
    ids=[
        "get-features-default",
        "get-features-enabled",
        "update-device-default",
        "update-device-all",
    ],
)
@pytest.mark.asyncio
async def test_feature_reads_send_the_enabled_filter_hint(
    read, expected_hint: bool, static_token_auth
):
    """Feature reads tell the API whether to skip disabled and not-ready features.

    ``get_features`` asks for all features by default, while ``update_device``
    asks the API to skip disabled and not-ready ones unless told otherwise.
    """
    # Arrange: Answer the device feature read with an empty collection.
    device = build_gateway_device("0")
    url = _device_features_url(device)

    with aioresponses() as mock_responses:
        mock_responses.post(url, payload={"data": []})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Read the device features through the public method.
            await read(client, device)

    # Assert: The request body carries the expected server-side hint.
    (request,) = mock_responses.requests[("POST", URL(url))]
    assert request.kwargs["json"] == {
        "skipDisabled": expected_hint,
        "skipNotReady": expected_hint,
    }


@pytest.mark.parametrize(
    "only_active_features", [True, False], ids=["active-only", "all-features"]
)
@pytest.mark.asyncio
async def test_get_devices_passes_the_feature_filter_to_hydration(
    only_active_features: bool, static_token_auth
):
    # Arrange: Discover one device and answer its feature read.
    device = build_gateway_device("0")
    devices_url = _devices_url("installation-1", "gateway-1")
    features_url = _device_features_url(device)

    with aioresponses() as mock_responses:
        mock_responses.get(
            devices_url,
            payload={"data": [{"id": "0", "modelId": "m", "deviceType": "heating"}]},
        )
        mock_responses.post(features_url, payload={"data": []})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Discover and hydrate the devices.
            await client.get_devices(
                "installation-1",
                "gateway-1",
                include_features=True,
                only_active_features=only_active_features,
            )

    # Assert: The hydration request carries the caller's filter.
    (request,) = mock_responses.requests[("POST", URL(features_url))]
    assert request.kwargs["json"] == {
        "skipDisabled": only_active_features,
        "skipNotReady": only_active_features,
    }


@pytest.mark.asyncio
async def test_update_gateway_devices_fallback_reraises_non_device_errors(
    static_token_auth,
):
    """Only device-specific errors are isolated; others abort the fallback."""
    # Arrange: The bulk read falls back, then the device read is unauthorized.
    device = build_gateway_device("0")

    with aioresponses() as mock_responses:
        mock_responses.post(
            _gateway_features_url(device),
            status=400,
            payload={
                "message": "Gateway busy",
                "errorType": "DEVICE_COMMUNICATION_ERROR",
            },
        )
        mock_responses.post(
            _device_features_url(device),
            status=401,
            payload={"message": "Token expired", "errorType": "UNAUTHORIZED"},
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The authentication error ends the whole refresh.
            with pytest.raises(ViAuthError, match="Unauthorized: Token expired"):
                await client.update_gateway_devices([device])
