"""Contract tests for validated discovery snapshots."""

import pytest

from vi_api_client.const import (
    API_BASE_URL,
    ENDPOINT_GATEWAYS,
    ENDPOINT_INSTALLATIONS,
)
from vi_api_client.exceptions import ViResponseError


@pytest.mark.parametrize(
    ("call", "endpoint", "response", "message"),
    [
        pytest.param(
            ("get_installations", ()),
            ENDPOINT_INSTALLATIONS,
            {"data": [{"description": "Home"}]},
            "Installation id must be a string or integer",
            id="installation-without-id",
        ),
        pytest.param(
            ("get_installations", ()),
            ENDPOINT_INSTALLATIONS,
            {"data": [{"id": "", "description": "Home"}]},
            "Installation id must be a string or integer",
            id="installation-empty-id",
        ),
        pytest.param(
            ("get_gateways", ()),
            ENDPOINT_GATEWAYS,
            {"data": [{"serial": "gateway-1", "installationId": True}]},
            "Gateway installationId must be a string or integer",
            id="gateway-boolean-installation-id",
        ),
        pytest.param(
            ("get_devices", ("installation-1", "gateway-1")),
            f"{ENDPOINT_INSTALLATIONS}/installation-1/gateways/gateway-1/devices",
            {"data": [{"id": "device-1", "deviceType": "heating"}]},
            "Device modelId must be a non-empty string",
            id="device-without-model-id",
        ),
        pytest.param(
            ("get_gateways", ()),
            ENDPOINT_GATEWAYS,
            {
                "data": [
                    {
                        "serial": "gateway-1",
                        "installationId": "123",
                        "version": 123,
                    }
                ]
            },
            "Gateway version must be a string",
            id="gateway-numeric-version",
        ),
        pytest.param(
            ("get_installations", ()),
            ENDPOINT_INSTALLATIONS,
            {
                "data": [
                    {
                        "id": "1",
                        "description": "Home",
                        "address": ["not-an-object"],
                    }
                ]
            },
            "Installation address must be an object",
            id="installation-address-not-object",
        ),
    ],
)
async def test_discovery_rejects_missing_or_malformed_known_fields(
    vi_client,
    mock_responses,
    call: tuple[str, tuple[str, ...]],
    endpoint: str,
    response: dict[str, object],
    message: str,
) -> None:
    """Public discovery methods reject invalid known snapshot fields."""
    # Arrange: Return the invalid response through the live client HTTP boundary.
    operation, arguments = call
    mock_responses.get(f"{API_BASE_URL}{endpoint}", payload=response)

    # Act and assert: Known violations become library-owned response errors.
    with pytest.raises(ViResponseError, match=message):
        await getattr(vi_client, operation)(*arguments)


async def test_discovery_tolerates_unknown_installation_fields_and_json_address(
    vi_client, mock_responses
) -> None:
    """Unknown installation fields are ignored while the JSON address is kept."""
    # Arrange: Return valid fields and future API data through the live client.
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

    # Act: Read the public discovery snapshot.
    installations = await vi_client.get_installations()

    # Assert: The numeric ID is normalized and the free-form address survives;
    # Installation has no field for unknown data, so futureField is dropped.
    assert installations[0].id == "12"
    assert installations[0].address == {"city": "Berlin", "future": ["value"]}
