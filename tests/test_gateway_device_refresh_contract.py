"""Shared gateway-scoped device refresh contract tests."""

from collections.abc import Callable
from typing import Any

import pytest

from vi_api_client.api import ViClient
from vi_api_client.exceptions import ViResponseError, ViValidationError
from vi_api_client.mock_client import MockViClient
from vi_api_client.models import Device


class _ScriptedGatewayDiscoveryAdapter:
    """Provide scripted raw responses while recording gateway refresh calls."""

    def __init__(
        self,
        gateway_response: dict[str, Any],
        device_responses: dict[str, dict[str, Any]],
    ) -> None:
        self.gateway_response = gateway_response
        self.device_responses = device_responses
        self.calls: list[str] = []
        self.gateway_error: ViValidationError | None = None
        self.device_errors: dict[str, ViValidationError] = {}

    async def get_installations(self) -> dict[str, Any]:
        """Return an unused empty installation envelope."""
        return {"data": []}

    async def get_gateways(self) -> dict[str, Any]:
        """Return an unused empty gateway envelope."""
        return {"data": []}

    async def get_devices(
        self, installation_id: str, gateway_serial: str
    ) -> dict[str, Any]:
        """Return an unused empty device envelope."""
        return {"data": []}

    async def get_gateway_features(
        self, devices: list[Device], payload: dict[str, bool]
    ) -> dict[str, Any]:
        """Return the scripted gateway-scoped response."""
        self.calls.append("gateway")
        if self.gateway_error is not None:
            raise self.gateway_error
        return self.gateway_response

    async def get_features(
        self, device: Device, payload: dict[str, bool | list[str]]
    ) -> dict[str, Any]:
        """Return the scripted individual device response."""
        self.calls.append(f"device:{device.id}")
        if device.id in self.device_errors:
            raise self.device_errors[device.id]
        return self.device_responses[device.id]


def _build_gateway_device(device_id: str) -> Device:
    """Build a device for gateway-scoped refresh contract tests."""
    return Device(
        id=device_id,
        gateway_serial="gateway-1",
        installation_id="installation-1",
        model_id=f"model-{device_id}",
        device_type="heating",
        status="connected",
    )


def _create_live_client(adapter: _ScriptedGatewayDiscoveryAdapter) -> ViClient:
    """Create a live client whose raw discovery boundary is scripted."""
    client = ViClient.__new__(ViClient)
    client._discovery_adapter = adapter
    return client


def _create_fixture_client(adapter: _ScriptedGatewayDiscoveryAdapter) -> MockViClient:
    """Create a fixture client whose raw discovery boundary is scripted."""
    client = MockViClient.__new__(MockViClient)
    client._discovery_adapter = adapter
    return client


@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_gateway_refresh_falls_back_for_omitted_devices_and_preserves_order(
    create_client: Callable[[_ScriptedGatewayDiscoveryAdapter], ViClient],
):
    """Both clients should share omission fallback and input-order behavior."""
    # Arrange: The bulk response supplies only device 10; device 0 needs fallback.
    adapter = _ScriptedGatewayDiscoveryAdapter(
        gateway_response={
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
        },
        device_responses={"0": {"data": []}},
    )
    client = create_client(adapter)
    devices = [_build_gateway_device("10"), _build_gateway_device("0")]

    # Act: Refresh from either raw-response adapter.
    result = await client.update_gateway_devices(devices)

    # Assert: Both use one bulk call, fall back only for the omission, and retain order.
    assert adapter.calls == ["gateway", "device:0"]
    assert [device.id for device in result.updated_devices] == ["10", "0"]
    assert result.updated_devices[0].model_id == "model-10"
    assert result.updated_devices[1].features == []
    assert result.is_complete


@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_gateway_refresh_groups_only_requested_device_features(
    create_client: Callable[[_ScriptedGatewayDiscoveryAdapter], ViClient],
):
    """Both clients should ignore gateway-owned and unrelated device features."""
    # Arrange: A complete bulk response includes requested, gateway, and unrelated data.
    adapter = _ScriptedGatewayDiscoveryAdapter(
        gateway_response={
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
        },
        device_responses={},
    )
    client = create_client(adapter)

    # Act: Refresh the only requested device from either raw-response adapter.
    result = await client.update_gateway_devices([_build_gateway_device("0")])

    # Assert: Only the requested device's feature is exposed and no fallback occurs.
    assert adapter.calls == ["gateway"]
    assert [feature.name for feature in result.updated_devices[0].features] == [
        "heating.status"
    ]


@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_gateway_refresh_returns_device_specific_fallback_errors(
    create_client: Callable[[_ScriptedGatewayDiscoveryAdapter], ViClient],
):
    """Both clients should retain successful devices beside fallback failures."""
    # Arrange: Gateway failure triggers individual refresh; device 0 has a known error.
    adapter = _ScriptedGatewayDiscoveryAdapter(
        gateway_response={"data": []},
        device_responses={
            "10": {
                "data": [
                    {
                        "feature": "heating.status",
                        "properties": {"value": "ready"},
                        "uri": "/devices/10/features/heating.status",
                    }
                ]
            }
        },
    )
    adapter.gateway_error = ViValidationError(
        "Gateway unavailable", error_type="DEVICE_COMMUNICATION_ERROR"
    )
    adapter.device_errors["0"] = ViValidationError(
        "Device unavailable", error_type="DEVICE_NOT_FOUND"
    )
    client = create_client(adapter)

    # Act: Refresh through the public gateway operation.
    result = await client.update_gateway_devices(
        [_build_gateway_device("10"), _build_gateway_device("0")]
    )

    # Assert: The successful device remains available with the known error isolated.
    assert adapter.calls == ["gateway", "device:10", "device:0"]
    assert [device.id for device in result.updated_devices] == ["10"]
    assert result.errors_by_device_id["0"].error_type == "DEVICE_NOT_FOUND"


@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_gateway_refresh_rejects_invalid_bulk_responses(
    create_client: Callable[[_ScriptedGatewayDiscoveryAdapter], ViClient],
):
    """Both clients should expose malformed shared responses as public errors."""
    # Arrange: Return a malformed successful gateway response.
    adapter = _ScriptedGatewayDiscoveryAdapter(
        gateway_response={"data": [{"uri": "/devices/0/features/heating"}]},
        device_responses={},
    )
    client = create_client(adapter)

    # Act and assert: Invalid raw data aborts the shared refresh consistently.
    with pytest.raises(ViResponseError, match="valid feature name"):
        await client.update_gateway_devices([_build_gateway_device("0")])
    assert adapter.calls == ["gateway"]


@pytest.mark.parametrize(
    ("devices", "error_message"),
    [
        ([_build_gateway_device("0"), _build_gateway_device("0")], "unique IDs"),
        (
            [
                _build_gateway_device("0"),
                Device(
                    id="1",
                    gateway_serial="gateway-2",
                    installation_id="installation-1",
                    model_id="model-1",
                    device_type="heating",
                    status="connected",
                ),
            ],
            "same installation and gateway",
        ),
    ],
)
@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_gateway_refresh_rejects_ambiguous_device_sets_before_adapter_calls(
    create_client: Callable[[_ScriptedGatewayDiscoveryAdapter], ViClient],
    devices: list[Device],
    error_message: str,
):
    """Both clients should reject ambiguous device sets before fetching features."""
    # Arrange: Give both clients a scripted adapter that must not receive a request.
    adapter = _ScriptedGatewayDiscoveryAdapter(
        gateway_response={"data": []}, device_responses={}
    )
    client = create_client(adapter)

    # Act and assert: Conflicting scope or duplicate IDs are rejected before I/O.
    with pytest.raises(ValueError, match=error_message):
        await client.update_gateway_devices(devices)
    assert adapter.calls == []


@pytest.mark.parametrize("create_client", [_create_live_client, _create_fixture_client])
@pytest.mark.asyncio
async def test_gateway_refresh_rejects_ambiguous_feature_ownership(
    create_client: Callable[[_ScriptedGatewayDiscoveryAdapter], ViClient],
):
    """Both clients should reject feature URIs with multiple device owners."""
    # Arrange: Return a feature URI that names two device path segments.
    adapter = _ScriptedGatewayDiscoveryAdapter(
        gateway_response={
            "data": [
                {
                    "feature": "heating.status",
                    "properties": {"value": "ready"},
                    "uri": "/devices/0/features/devices/1/heating.status",
                }
            ]
        },
        device_responses={},
    )
    client = create_client(adapter)

    # Act and assert: Ambiguous ownership is a shared response-contract failure.
    with pytest.raises(ViResponseError, match="ambiguous device ownership"):
        await client.update_gateway_devices([_build_gateway_device("0")])
    assert adapter.calls == ["gateway"]
