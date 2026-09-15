"""Contract tests for validated discovery snapshots."""

from typing import Any

import pytest

from vi_api_client.client import ViClient
from vi_api_client.exceptions import ViResponseError
from vi_api_client.models import Device


class _DiscoveryResponses:
    """Supply controlled discovery envelopes at the client boundary."""

    def __init__(self, envelope: dict[str, Any]) -> None:
        self.envelope = envelope

    async def get_installations(self) -> dict[str, Any]:
        return self.envelope

    async def get_gateways(self) -> dict[str, Any]:
        return self.envelope

    async def get_devices(
        self, installation_id: str, gateway_serial: str
    ) -> dict[str, Any]:
        return self.envelope

    async def get_features(
        self, device: Device, payload: dict[str, bool | list[str]]
    ) -> dict[str, Any]:
        return {"data": []}

    async def get_gateway_features(
        self, devices: list[Device], payload: dict[str, bool]
    ) -> dict[str, Any]:
        return {"data": []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "envelope", "message"),
    [
        ("installations", {"data": [{"description": "Home"}]}, "id"),
        (
            "installations",
            {"data": [{"id": "", "description": "Home"}]},
            "id",
        ),
        (
            "gateways",
            {"data": [{"serial": "gateway-1", "installationId": True}]},
            "installationId",
        ),
        (
            "devices",
            {"data": [{"id": "device-1", "deviceType": "heating"}]},
            "modelId",
        ),
    ],
)
async def test_discovery_rejects_missing_or_malformed_known_fields(
    operation: str, envelope: dict[str, Any], message: str
) -> None:
    """Public discovery methods reject invalid known snapshot fields."""
    # Arrange: Replace the data adapter while retaining the public client method.
    client = ViClient.__new__(ViClient)
    client._discovery_adapter = _DiscoveryResponses(envelope)

    # Act and assert: Known contract violations are library-owned response errors.
    with pytest.raises(ViResponseError, match=message):
        if operation == "installations":
            await client.get_installations()
        elif operation == "gateways":
            await client.get_gateways()
        else:
            await client.get_devices("installation-1", "gateway-1")


@pytest.mark.asyncio
async def test_discovery_keeps_unknown_installation_fields_and_json_address() -> None:
    """Forward-compatible discovery preserves only the documented snapshot data."""
    # Arrange: Provide valid known fields with additional future API data.
    client = ViClient.__new__(ViClient)
    client._discovery_adapter = _DiscoveryResponses(
        {
            "data": [
                {
                    "id": 12,
                    "description": "Home",
                    "address": {"city": "Berlin", "future": ["value"]},
                    "futureField": {"enabled": True},
                }
            ]
        }
    )

    # Act: Read the public discovery snapshot.
    installations = await client.get_installations()

    # Assert: Identifier normalization and flexible JSON address are retained.
    assert installations[0].id == "12"
    assert installations[0].address == {"city": "Berlin", "future": ["value"]}
