"""Contract tests for validated discovery snapshots."""

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client.auth import AbstractAuth
from vi_api_client.client import ViClient
from vi_api_client.const import (
    API_BASE_URL,
    ENDPOINT_GATEWAYS,
    ENDPOINT_INSTALLATIONS,
)
from vi_api_client.exceptions import ViResponseError


class _StaticAuth(AbstractAuth):
    """Provide a static token for live client request-flow tests."""

    async def async_get_access_token(self) -> str:
        """Return the access token used by mocked HTTP requests."""
        return "access-token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "endpoint", "envelope", "message"),
    [
        (
            "installations",
            ENDPOINT_INSTALLATIONS,
            {"data": [{"description": "Home"}]},
            "id",
        ),
        (
            "installations",
            ENDPOINT_INSTALLATIONS,
            {"data": [{"id": "", "description": "Home"}]},
            "id",
        ),
        (
            "gateways",
            ENDPOINT_GATEWAYS,
            {"data": [{"serial": "gateway-1", "installationId": True}]},
            "installationId",
        ),
        (
            "devices",
            f"{ENDPOINT_INSTALLATIONS}/installation-1/gateways/gateway-1/devices",
            {"data": [{"id": "device-1", "deviceType": "heating"}]},
            "modelId",
        ),
    ],
)
async def test_discovery_rejects_missing_or_malformed_known_fields(
    operation: str, endpoint: str, envelope: dict[str, object], message: str
) -> None:
    """Public discovery methods reject invalid known snapshot fields."""
    # Arrange: Return the invalid envelope through the live client HTTP boundary.
    with aioresponses() as mock_responses:
        mock_responses.get(f"{API_BASE_URL}{endpoint}", payload=envelope)
        async with aiohttp.ClientSession() as session:
            client = ViClient(_StaticAuth(session))

            # Act and assert: Known violations become library-owned response errors.
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
    # Arrange: Return valid fields and future API data through the live client.
    with aioresponses() as mock_responses:
        mock_responses.get(
            f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}",
            payload={
                "data": [
                    {
                        "id": 12,
                        "description": "Home",
                        "address": {"city": "Berlin", "future": ["value"]},
                        "futureField": {"enabled": True},
                    }
                ]
            },
        )
        async with aiohttp.ClientSession() as session:
            client = ViClient(_StaticAuth(session))

            # Act: Read the public discovery snapshot.
            installations = await client.get_installations()

    # Assert: Identifier normalization and flexible JSON address are retained.
    assert installations[0].id == "12"
    assert installations[0].address == {"city": "Berlin", "future": ["value"]}
