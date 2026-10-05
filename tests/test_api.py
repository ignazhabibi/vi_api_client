"""Tests for the ViClient public workflows (Flat Architecture)."""

from copy import deepcopy
from dataclasses import replace

import pytest
from builders import build_device
from yarl import URL

from vi_api_client._types import JsonValue
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


async def test_update_gateway_devices_refreshes_multiple_devices_with_one_request(
    vi_client,
    mock_responses,
    load_fixture_json,
):
    # Arrange: Mock a gateway response with requested and unrelated features.
    response = load_fixture_json("gateway_device_features.json")
    devices = [build_device("10"), build_device("0")]
    url = _gateway_features_url(devices[0])

    mock_responses.post(url, payload=response)

    # Act: Refresh both devices through the public gateway operation.
    result = await vi_client.update_gateway_devices(devices)

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


async def test_update_gateway_devices_falls_back_only_for_omitted_devices_in_input_order(
    vi_client, mock_responses
):
    # Arrange: The bulk response covers device 10 only, so device 0 needs its
    # own read.
    device_10 = build_device("10")
    device_0 = build_device("0")
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

    mock_responses.post(gateway_url, payload=bulk_response)
    mock_responses.post(_device_features_url(device_0), payload={"data": []})

    # Act: Refresh both devices through the public gateway operation.
    result = await vi_client.update_gateway_devices([device_10, device_0])

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


async def test_update_gateway_devices_ignores_gateway_owned_and_unrelated_device_features(
    vi_client, mock_responses
):
    # Arrange: The bulk response mixes the requested device's feature with a
    # gateway feature and a feature of a device the caller did not request.
    device = build_device("0")
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

    mock_responses.post(url, payload=response)

    # Act: Refresh the only requested device.
    result = await vi_client.update_gateway_devices([device])

    # Assert: Only the requested device's feature is exposed, and the bulk
    # request was enough, so no individual fallback read happened.
    assert list(mock_responses.requests) == [("POST", URL(url))]
    assert [feature.name for feature in result.updated_devices[0].features] == [
        "heating.status"
    ]


@pytest.mark.parametrize(
    ("devices", "message"),
    [
        pytest.param(
            [build_device("0"), build_device("0")],
            "unique IDs",
            id="duplicate-device-ids",
        ),
        pytest.param(
            [
                build_device("0"),
                replace(build_device("1"), gateway_serial="gateway-2"),
            ],
            "same installation and gateway",
            id="different-gateways",
        ),
    ],
)
@pytest.mark.usefixtures("no_http_requests")
async def test_update_gateway_devices_rejects_ambiguous_device_sets_before_any_request(
    vi_client, devices: list[Device], message: str
):
    # Act and assert: Duplicate IDs or mixed gateways cannot form one bulk
    # request, so they are rejected before any I/O.
    with pytest.raises(ValueError, match=message):
        await vi_client.update_gateway_devices(devices)


async def test_update_gateway_devices_decodes_complete_device_uri_segments(
    vi_client, mock_responses
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
    device = build_device("device/0")

    mock_responses.post(_gateway_features_url(device), payload=response)

    # Act: Refresh the encoded device ID.
    result = await vi_client.update_gateway_devices([device])

    # Assert: The decoded complete segment maps to the requested device.
    assert result.is_complete
    assert result.updated_devices[0].id == "device/0"
    heating_status = result.updated_devices[0].get_feature("heating.status")
    assert heating_status is not None
    assert heating_status.value == "ready"


@pytest.mark.usefixtures("no_http_requests")
async def test_update_gateway_devices_accepts_empty_input_without_request(vi_client):
    # Act: Refresh an empty gateway device collection.
    result = await vi_client.update_gateway_devices([])

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
        pytest.param([], "response must be an object", id="not-an-object"),
        pytest.param({"data": {}}, "data must be a list", id="data-not-a-list"),
        pytest.param(
            {"data": ["not-an-object"]},
            "entries must be objects",
            id="entry-not-an-object",
        ),
        pytest.param(
            {"data": [{"uri": "/iot/v2/features/devices"}]},
            "URI has no device ID",
            id="uri-without-device",
        ),
        pytest.param(
            {"data": [{"uri": "/iot/v2/features/devices/%ZZ/features/heating"}]},
            "invalid encoded URI",
            id="invalid-encoded-uri",
        ),
        pytest.param(
            {"data": [{"uri": "/iot/v2/features/devices/0/features/missing.feature"}]},
            "Feature name must be a non-empty string",
            id="missing-feature-name",
        ),
        pytest.param(
            {
                "data": [
                    {"uri": "/iot/v2/features/devices/0/features/devices/10/heating"}
                ]
            },
            "ambiguous device ownership",
            id="ambiguous-ownership",
        ),
        pytest.param(
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
            id="properties-not-an-object",
        ),
        pytest.param(
            {"data": [_DUPLICATED_GATEWAY_FEATURE, dict(_DUPLICATED_GATEWAY_FEATURE)]},
            "Duplicate feature name in API response: heating.status",
            id="duplicate-feature-name",
        ),
        pytest.param(
            {"data": [{"feature": "heating.status", "properties": {}, "uri": None}]},
            "Gateway feature entry has no valid URI",
            id="missing-uri",
        ),
        pytest.param(
            {"data": [{"feature": "heating.status", "properties": {}, "uri": 5}]},
            "Gateway feature entry has no valid URI",
            id="non-string-uri",
        ),
        pytest.param(
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
            id="undecodable-uri",
        ),
    ],
)
async def test_update_gateway_devices_rejects_invalid_bulk_responses(
    vi_client, mock_responses, response, message
):
    # Arrange: Return a malformed successful response from the gateway endpoint.
    device = build_device("0")

    mock_responses.post(_gateway_features_url(device), payload=response)

    # Act and assert: Each contract violation names its specific cause as
    # a public response error rather than a parsing exception.
    with pytest.raises(ViResponseError, match=message):
        await vi_client.update_gateway_devices([device])


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        pytest.param(
            400, "DEVICE_COMMUNICATION_ERROR", id="device-communication-error"
        ),
        pytest.param(404, "DEVICE_NOT_FOUND", id="device-not-found"),
        pytest.param(403, "PACKAGE_NOT_PAID_FOR", id="package-not-paid-for"),
    ],
)
async def test_update_gateway_devices_captures_device_specific_fallback_errors(
    vi_client, mock_responses, status: int, error_type: str, load_fixture_json
):
    # Arrange: Gateway communication fails and device 0 then fails specifically.
    fixture = load_fixture_json("gateway_device_features.json")
    device_10_response = {
        "data": [
            feature for feature in fixture["data"] if "/devices/10/" in feature["uri"]
        ]
    }
    devices = [build_device("10"), build_device("0")]

    mock_responses.post(
        _gateway_features_url(devices[0]),
        status=400,
        payload={
            "message": "Gateway unavailable",
            "errorType": "DEVICE_COMMUNICATION_ERROR",
        },
    )
    mock_responses.post(_device_features_url(devices[0]), payload=device_10_response)
    mock_responses.post(
        _device_features_url(devices[1]),
        status=status,
        payload={"message": "Device unavailable", "errorType": error_type},
    )

    # Act: Refresh through the public gateway operation.
    result = await vi_client.update_gateway_devices(devices)

    # Assert: Successful devices and per-device errors remain independent.
    assert not result.is_complete
    assert [device.id for device in result.updated_devices] == ["10"]
    assert set(result.errors_by_device_id) == {"0"}
    assert result.errors_by_device_id["0"].error_type == error_type


@pytest.mark.parametrize(
    ("status", "error_type", "expected_error", "message"),
    [
        pytest.param(
            401,
            "UNAUTHORIZED",
            ViAuthError,
            "Unauthorized: Global failure",
            id="unauthorized",
        ),
        pytest.param(
            429,
            "RATE_LIMIT_EXCEEDED",
            ViRateLimitError,
            "Rate Limit Exceeded",
            id="rate-limited",
        ),
        pytest.param(
            500,
            "INTERNAL_ERROR",
            ViServerInternalError,
            "Server Error 500",
            id="server-error",
        ),
        pytest.param(
            400,
            "UNKNOWN_VALIDATION_ERROR",
            ViValidationError,
            "Global failure",
            id="non-fallback-validation",
        ),
    ],
)
async def test_update_gateway_devices_propagates_global_gateway_errors(
    vi_client,
    mock_responses,
    status: int,
    error_type: str,
    expected_error: type[Exception],
    message: str,
):
    # Arrange: Return a gateway error that does not trigger the device fallback.
    device = build_device("0")

    mock_responses.post(
        _gateway_features_url(device),
        status=status,
        payload={"message": "Global failure", "errorType": error_type},
    )

    # Act and assert: Global failures abort the entire refresh.
    with pytest.raises(expected_error, match=message):
        await vi_client.update_gateway_devices([device])


async def test_update_gateway_devices_propagates_connection_errors(vi_client):
    # Arrange: Do not register the bulk endpoint, causing a network failure.

    # Act and assert: Connection failures abort the entire refresh.
    with pytest.raises(ViConnectionError, match="Network error"):
        await vi_client.update_gateway_devices([build_device("0")])


async def test_update_gateway_devices_translates_malformed_fallback_response(
    vi_client, mock_responses
):
    # Arrange: An empty bulk response triggers the fallback, which then returns
    # invalid feature properties.
    device = build_device("0")
    mock_responses.post(_gateway_features_url(device), payload={"data": []})
    mock_responses.post(
        _device_features_url(device),
        payload={"data": [{"feature": "broken", "properties": []}]},
    )

    # Act and assert: Fallback contract failures use the public error.
    with pytest.raises(ViResponseError, match="properties must be an object"):
        await vi_client.update_gateway_devices([device])


async def test_get_installations_returns_every_listed_installation(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Answer the installation list with two installations.
    data = load_fixture_json("installations.json")

    mock_responses.get(f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}", payload=data)

    # Act: Fetch installations from the API.
    installations = await vi_client.get_installations()

    # Assert: Both installations are returned in response order.
    assert [installation.id for installation in installations] == [
        "123456",
        "789012",
    ]


async def test_get_gateways_returns_the_listed_gateway(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Answer the gateway list with one gateway.
    data = load_fixture_json("gateways.json")

    mock_responses.get(f"{API_BASE_URL}{ENDPOINT_GATEWAYS}", payload=data)

    # Act: Fetch gateways from the API.
    gateways = await vi_client.get_gateways()

    # Assert: The single gateway is identified by its serial.
    assert [gateway.serial for gateway in gateways] == ["1234567890"]


@pytest.mark.parametrize(
    ("url", "request_method", "operation", "arguments"),
    [
        pytest.param(
            f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}",
            "get",
            "get_installations",
            (),
            id="installations",
        ),
        pytest.param(
            f"{API_BASE_URL}{ENDPOINT_GATEWAYS}",
            "get",
            "get_gateways",
            (),
            id="gateways",
        ),
        pytest.param(
            _devices_url("installation-1", "gateway-1"),
            "get",
            "get_devices",
            ("installation-1", "gateway-1"),
            id="devices",
        ),
        pytest.param(
            _device_features_url(build_device("0")),
            "post",
            "get_features",
            (build_device("0"),),
            id="features",
        ),
    ],
)
async def test_discovery_rejects_successful_non_json_responses(
    vi_client, mock_responses, url, request_method, operation, arguments
):
    # Arrange: Return non-JSON content from each discovery endpoint.
    getattr(mock_responses, request_method)(
        url, body="not JSON", content_type="text/plain"
    )

    # Act and assert: The public response error communicates the contract failure.
    with pytest.raises(ViResponseError, match="not valid JSON"):
        await getattr(vi_client, operation)(*arguments)


@pytest.mark.parametrize(
    ("endpoint", "call"),
    [
        pytest.param(
            ("get", f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"),
            ("get_installations", ()),
            id="installations",
        ),
        pytest.param(
            ("get", f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"),
            ("get_gateways", ()),
            id="gateways",
        ),
        pytest.param(
            ("get", _devices_url("installation-1", "gateway-1")),
            ("get_devices", ("installation-1", "gateway-1")),
            id="devices",
        ),
        pytest.param(
            ("post", _device_features_url(build_device("0"))),
            ("get_features", (build_device("0"),)),
            id="features",
        ),
    ],
)
@pytest.mark.parametrize(
    ("response", "message"),
    [
        pytest.param([], "response must be an object", id="root-list"),
        pytest.param({}, "data must be a list", id="missing-data"),
        pytest.param({"data": {}}, "data must be a list", id="data-not-list"),
        pytest.param(
            {"data": [None]}, "data entries must be objects", id="data-entry-not-object"
        ),
    ],
)
async def test_discovery_rejects_successful_malformed_json_responses(
    vi_client, mock_responses, endpoint, call, response, message
):
    # Arrange: Return JSON that violates a collection response requirement.
    request_method, url = endpoint
    operation, arguments = call
    getattr(mock_responses, request_method)(url, payload=response)

    # Act and assert: The public response error communicates the contract failure.
    with pytest.raises(ViResponseError, match=message):
        await getattr(vi_client, operation)(*arguments)


async def test_discovery_keeps_a_caller_managed_session_open(
    vi_client, static_token_auth, mock_responses, load_fixture_json
):
    """Discovery must not close a session supplied through authentication."""
    # Arrange: The auth carries a caller-owned session.
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
    mock_responses.get(url, payload=load_fixture_json("installations.json"))
    session = static_token_auth.websession

    # Act: Run discovery through the adapter-backed client.
    await vi_client.get_installations()

    # Assert: The client did not close or replace the caller's session.
    assert static_token_auth.websession is session
    assert session is not None
    assert not session.closed


async def test_get_full_installation_status_uses_matching_gateways_only(
    vi_client, mock_responses
):
    """Full status should not query gateways from other installations."""
    # Arrange: Mock one gateway for each of two installations.
    installation_id = "installation-a"
    matching_gateway = "gateway-a"
    other_gateway = "gateway-b"
    gateways_url = f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"
    devices_url = _devices_url(installation_id, matching_gateway)

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

    # Act: Fetch the complete status for the first installation.
    devices = await vi_client.get_full_installation_status(installation_id)

    # Assert: Only the matching gateway should receive a devices request.
    requested_urls = [str(url) for _method, url in mock_responses.requests]
    assert devices == []
    assert devices_url in requested_urls
    assert other_gateway not in "".join(requested_urls)


async def test_get_full_installation_status_rejects_malformed_device_responses(
    vi_client, mock_responses
):
    """Full status should preserve discovery response validation."""
    # Arrange: Return a matching gateway followed by invalid device collection entries.
    installation_id = "installation-1"
    gateway_serial = "gateway-1"
    gateways_url = f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"
    devices_url = _devices_url(installation_id, gateway_serial)
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

    # Act and assert: The composed read keeps the public response error.
    with pytest.raises(ViResponseError, match="entries must be objects"):
        await vi_client.get_full_installation_status(installation_id)


async def test_get_devices_returns_the_devices_of_one_gateway(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Answer the device list of one gateway with two devices.
    data = load_fixture_json("devices_heating.json")

    mock_responses.get(_devices_url("installation-1", "gateway-1"), payload=data)

    # Act: Fetch the devices of the gateway.
    devices = await vi_client.get_devices("installation-1", "gateway-1")

    # Assert: Both devices are returned with their scope and type.
    assert [device.id for device in devices] == ["0", "gateway"]
    assert devices[0].device_type == "heating"
    assert devices[0].installation_id == "installation-1"
    assert devices[0].gateway_serial == "gateway-1"


async def test_get_features_returns_every_device_feature_as_flat_features(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Answer the device feature read with a sensor and a circuit.
    data = load_fixture_json("features_heating_sensors.json")
    device = build_device("0")

    mock_responses.post(_device_features_url(device), payload=data)

    # Act: Fetch all features for the device.
    features = await vi_client.get_features(device)

    # Assert: Each API property becomes one dot-named flat feature.
    assert [feature.name for feature in features] == [
        "heating.sensors.temperature.outside",
        "heating.circuits.0.active",
    ]
    assert features[0].value == 5.5


async def test_get_features_translates_duplicate_api_feature_names(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Mock a response containing the same feature twice.
    data = load_fixture_json("features_heating_sensors.json")
    data["data"].append(deepcopy(data["data"][0]))
    device = build_device("0")

    mock_responses.post(_device_features_url(device), payload=data)

    # Act and assert: The client translates an invalid API response.
    with pytest.raises(ViResponseError, match="Duplicate feature name"):
        await vi_client.get_features(
            device, feature_names=["heating.sensors.temperature.outside"]
        )


async def test_get_features_ignores_duplicates_outside_requested_names(
    vi_client, mock_responses, load_fixture_json
):
    """Duplicates only matter for features the client returns."""
    # Arrange: Duplicate a feature that the request does not select.
    data = load_fixture_json("features_heating_sensors.json")
    data["data"].append(deepcopy(data["data"][0]))
    device = build_device("0")

    mock_responses.post(_device_features_url(device), payload=data)

    # Act: Request only a feature that appears once.
    features = await vi_client.get_features(
        device, feature_names=["heating.circuits.0.active"]
    )

    # Assert: The requested feature is returned without a duplicate error.
    assert [feature.name for feature in features] == ["heating.circuits.0.active"]


async def test_get_features_applies_enabled_ready_and_name_filters_after_response(
    vi_client,
    mock_responses,
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
    device = build_device("0")

    mock_responses.post(_device_features_url(device), payload=data)

    # Act: Ask for a specific enabled and ready feature.
    features = await vi_client.get_features(
        device,
        only_enabled=True,
        feature_names=["heating.circuits.0.active", "test.notReady.active"],
    )

    # Assert: Client-side filtering enforces the public semantics.
    assert [feature.name for feature in features] == ["heating.circuits.0.active"]


@pytest.mark.parametrize(
    ("requested_name", "expected_names"),
    [
        pytest.param(
            "heating.circuits.0.heating.curve",
            [
                "heating.circuits.0.heating.curve.shift",
                "heating.circuits.0.heating.curve.slope",
            ],
            id="api-feature-name",
        ),
        pytest.param(
            "heating.circuits.0.heating.curve.slope",
            ["heating.circuits.0.heating.curve.slope"],
            id="feature-name",
        ),
        pytest.param(
            "heating.circuits.0.name",
            ["heating.circuits.0.name", "heating.circuits.0.name.name"],
            id="feature-and-api-feature-name",
        ),
    ],
)
async def test_get_features_matches_feature_and_api_feature_names_locally(
    vi_client, mock_responses, requested_name, expected_names, load_fixture_device
):
    """Names select flat features by their own or their API feature's name."""
    # Arrange: Return a complete device feature response from the live API.
    device = build_device("0")
    url = _device_features_url(device)
    mock_responses.post(url, payload=load_fixture_device("Vitocal250A"))

    # Act: Request features by one name.
    features = await vi_client.get_features(device, feature_names=[requested_name])

    # Assert: Matching is local, so the request carries no server-side name filter.
    assert sorted(feature.name for feature in features) == expected_names
    (request,) = mock_responses.requests[("POST", URL(url))]
    assert "filter" not in request.kwargs["json"]


async def test_get_features_returns_nothing_for_unknown_names(
    vi_client, mock_responses, load_fixture_json
):
    """Unknown names select no features instead of failing the request."""
    # Arrange: Return a successful device feature response.
    device = build_device("0")
    mock_responses.post(
        _device_features_url(device),
        payload=load_fixture_json("features_heating_sensors.json"),
    )

    # Act: Request a feature the device does not report.
    features = await vi_client.get_features(
        device, feature_names=["nonexistent.feature"]
    )

    # Assert: The unknown name selects nothing.
    assert features == []


async def test_update_device_returns_a_new_device_with_the_read_features(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Answer the refresh with one feature the device does not have yet.
    data = load_fixture_json("update_device_response.json")
    device = build_device("0")

    mock_responses.post(_device_features_url(device), payload=data)

    # Act: Refresh the device through the public client method.
    updated_device = await vi_client.update_device(device)

    # Assert: The refresh is a new snapshot; the input device stays unhydrated.
    assert updated_device is not device
    assert updated_device.id == "0"
    assert [feature.name for feature in updated_device.features] == ["new.feature"]
    assert device.features == ()


async def test_update_device_rejects_malformed_feature_responses(
    vi_client, mock_responses
):
    # Arrange: Return an invalid feature collection for an existing device.
    device = build_device("0")
    mock_responses.post(_device_features_url(device), payload={"data": [None]})

    # Act and assert: The composed refresh keeps the public response error.
    with pytest.raises(ViResponseError, match="entries must be objects"):
        await vi_client.update_device(device)


async def test_get_devices_hydrates_each_device_with_its_own_features(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Discover two devices and give each its own feature response, so
    # a device hydrated from the other's read would be detected.
    devices_data = load_fixture_json("devices_heating.json")

    mock_responses.get(
        _devices_url("installation-1", "gateway-1"), payload=devices_data
    )
    mock_responses.post(
        _device_features_url(build_device("0")),
        payload=load_fixture_json("features_heating_sensors.json"),
    )
    mock_responses.post(
        _device_features_url(build_device("gateway")),
        payload=load_fixture_json("update_device_response.json"),
    )

    # Act: Fetch devices with hydration enabled.
    devices = await vi_client.get_devices(
        "installation-1", "gateway-1", include_features=True
    )

    # Assert: Every discovered device carries the features of its own read.
    assert {
        device.id: [feature.name for feature in device.features] for device in devices
    } == {
        "0": ["heating.sensors.temperature.outside", "heating.circuits.0.active"],
        "gateway": ["new.feature"],
    }


async def test_set_feature_sends_the_current_value_of_a_required_sibling(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Read the curve whose setCurve command requires slope and shift.
    device = build_device("0")

    mock_responses.post(
        _device_features_url(device),
        payload={"data": load_fixture_json("feature_heating_curve.json")},
    )
    mock_responses.post(CURVE_COMMAND_URL, payload={"data": {"success": True}})
    device = replace(device, features=await vi_client.get_features(device))
    slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
    assert slope_feature is not None

    # Act: Set only the slope.
    response, _updated_device = await vi_client.set_feature(device, slope_feature, 1.2)

    # Assert: The command also carries the fixture's current shift of 4.
    assert response.success
    (request,) = mock_responses.requests[("POST", URL(CURVE_COMMAND_URL))]
    assert request.kwargs["json"] == {"slope": 1.2, "shift": 4}


async def test_set_feature_returns_a_new_device_with_the_written_value(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Read the curve with a slope of 0.6 and accept the write.
    device = build_device("0")

    mock_responses.post(
        _device_features_url(device),
        payload={"data": load_fixture_json("feature_heating_curve.json")},
    )
    mock_responses.post(CURVE_COMMAND_URL, payload={"data": {"success": True}})
    device = replace(device, features=await vi_client.get_features(device))
    slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
    assert slope_feature is not None

    # Act: Set the slope to a new value.
    response, updated_device = await vi_client.set_feature(device, slope_feature, 0.7)

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


async def test_execute_command_preserves_explicit_parameters(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Read a writable feature and accept its command.
    device = build_device("0")
    parameters: dict[str, JsonValue] = {"slope": 0.7, "shift": 7.0}

    mock_responses.post(
        _device_features_url(device),
        payload={"data": load_fixture_json("feature_heating_curve.json")},
    )
    mock_responses.post(CURVE_COMMAND_URL, payload={"data": {"success": True}})
    device = replace(device, features=await vi_client.get_features(device))
    slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
    assert slope_feature is not None

    # Act: Submit the caller's complete parameter set.
    response = await vi_client.execute_command(slope_feature, parameters)

    # Assert: The adapter sends the supplied parameters unchanged.
    assert response.success
    (request,) = mock_responses.requests[("POST", URL(CURVE_COMMAND_URL))]
    assert request.kwargs["json"] == parameters


async def test_execute_command_rejects_malformed_success_response(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Return a valid JSON value that violates the command response contract.
    device = build_device("0")

    mock_responses.post(
        _device_features_url(device),
        payload={"data": load_fixture_json("feature_heating_curve.json")},
    )
    mock_responses.post(CURVE_COMMAND_URL, payload=["unexpected"])
    device = replace(device, features=await vi_client.get_features(device))
    slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
    assert slope_feature is not None

    # Act and assert: The public client exposes a library exception.
    with pytest.raises(ViResponseError, match="Command response must be an object"):
        await vi_client.execute_command(slope_feature, {"slope": 0.7, "shift": 4})


async def test_set_feature_returns_the_original_device_when_the_api_rejects_the_write(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Read the curve and let the API reject the command.
    device = build_device("0")

    mock_responses.post(
        _device_features_url(device),
        payload={"data": load_fixture_json("feature_heating_curve.json")},
    )
    mock_responses.post(
        CURVE_COMMAND_URL,
        payload={"data": {"success": False, "reason": "Device unavailable"}},
    )
    device = replace(device, features=await vi_client.get_features(device))
    slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
    assert slope_feature is not None

    # Act: Try to set a value the API then rejects.
    response, updated_device = await vi_client.set_feature(device, slope_feature, 0.7)

    # Assert: The rejection reason is exposed and no new snapshot is created.
    assert not response.success
    assert response.reason == "Device unavailable"
    assert updated_device is device


async def test_set_feature_sends_the_optimistic_value_of_a_previous_write(
    vi_client, mock_responses, load_fixture_json
):
    # Arrange: Read the curve with slope 0.6 and shift 4, and accept every write.
    device = build_device("0")

    mock_responses.post(
        _device_features_url(device),
        payload={"data": load_fixture_json("feature_heating_curve.json")},
    )
    mock_responses.post(
        CURVE_COMMAND_URL, payload={"data": {"success": True}}, repeat=True
    )
    device = replace(device, features=await vi_client.get_features(device))
    slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
    shift_feature = device.get_feature("heating.circuits.0.heating.curve.shift")
    assert slope_feature is not None
    assert shift_feature is not None

    # Act: Write the slope, then the shift on the device the first write
    # returned, without re-reading the features in between.
    slope_response, device = await vi_client.set_feature(device, slope_feature, 0.7)
    shift_response, device = await vi_client.set_feature(device, shift_feature, 7.0)

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
        pytest.param(
            lambda client, device: client.get_features(device),
            False,
            id="get-features-default",
        ),
        pytest.param(
            lambda client, device: client.get_features(device, only_enabled=True),
            True,
            id="get-features-enabled",
        ),
        pytest.param(
            lambda client, device: client.update_device(device),
            True,
            id="update-device-default",
        ),
        pytest.param(
            lambda client, device: client.update_device(device, only_enabled=False),
            False,
            id="update-device-all",
        ),
    ],
)
async def test_feature_reads_send_the_enabled_filter_hint(
    vi_client, mock_responses, read, expected_hint: bool
):
    """Feature reads tell the API whether to skip disabled and not-ready features.

    ``get_features`` asks for all features by default, while ``update_device``
    asks the API to skip disabled and not-ready ones unless told otherwise.
    """
    # Arrange: Answer the device feature read with an empty collection.
    device = build_device("0")
    url = _device_features_url(device)

    mock_responses.post(url, payload={"data": []})

    # Act: Read the device features through the public method.
    await read(vi_client, device)

    # Assert: The request body carries the expected server-side hint.
    (request,) = mock_responses.requests[("POST", URL(url))]
    assert request.kwargs["json"] == {
        "skipDisabled": expected_hint,
        "skipNotReady": expected_hint,
    }


@pytest.mark.parametrize(
    "only_active_features",
    [
        pytest.param(True, id="active-only"),
        pytest.param(False, id="all-features"),
    ],
)
async def test_get_devices_passes_the_feature_filter_to_hydration(
    vi_client, mock_responses, only_active_features: bool
):
    # Arrange: Discover one device and answer its feature read.
    device = build_device("0")
    devices_url = _devices_url("installation-1", "gateway-1")
    features_url = _device_features_url(device)

    mock_responses.get(
        devices_url,
        payload={"data": [{"id": "0", "modelId": "m", "deviceType": "heating"}]},
    )
    mock_responses.post(features_url, payload={"data": []})

    # Act: Discover and hydrate the devices.
    await vi_client.get_devices(
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


async def test_update_gateway_devices_fallback_reraises_non_device_errors(
    vi_client, mock_responses
):
    """Only device-specific errors are isolated; others abort the fallback."""
    # Arrange: The bulk read falls back, then the device read is unauthorized.
    device = build_device("0")

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

    # Act and assert: The authentication error ends the whole refresh.
    with pytest.raises(ViAuthError, match="Unauthorized: Token expired"):
        await vi_client.update_gateway_devices([device])
