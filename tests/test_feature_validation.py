"""Public request-flow tests for feature response validation."""

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client.auth import AbstractAuth
from vi_api_client.client import ViClient
from vi_api_client.const import API_BASE_URL, ENDPOINT_FEATURES
from vi_api_client.exceptions import ViResponseError
from vi_api_client.models import Device


class _StaticAuth(AbstractAuth):
    """Provide a static token for mocked live feature reads."""

    async def async_get_access_token(self) -> str:
        """Return the access token used by mocked HTTP requests."""
        return "access-token"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("feature", "message"),
    [
        ({"properties": {"value": 1}}, "Feature name"),
        ({"feature": "heating.status", "properties": []}, "properties"),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "isEnabled": "true",
            },
            "booleans",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": []}},
            },
            "params",
        ),
    ],
)
async def test_live_feature_read_rejects_malformed_known_fields(feature, message):
    """Live feature reads reject malformed known fields before flattening."""
    # Arrange: Return an invalid feature through the public HTTP request flow.
    device = Device(
        id="device-1",
        gateway_serial="gateway-1",
        installation_id="installation-1",
        model_id="model-1",
        device_type="heating",
        status="connected",
    )
    endpoint = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/gateway-1/"
        "devices/device-1/features/filter"
    )
    with aioresponses() as mock_responses:
        mock_responses.post(endpoint, status=200, payload={"data": [feature]})
        async with aiohttp.ClientSession() as session:
            client = ViClient(_StaticAuth(session))

            # Act and assert: The public read rejects known contract violations.
            with pytest.raises(ViResponseError, match=message):
                await client.get_features(device)
