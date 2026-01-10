"""Tests for vitoclient.api module."""

import pytest
from aioresponses import aioresponses
import aiohttp

from vi_api_client.api import Client
from vi_api_client.auth import AbstractAuth
from vi_api_client.const import API_BASE_URL, ENDPOINT_INSTALLATIONS, ENDPOINT_GATEWAYS
from vi_api_client.exceptions import VitoConnectionError


class MockAuth(AbstractAuth):
    """Mock implementation of AbstractAuth for testing."""
    
    def __init__(self, session: aiohttp.ClientSession):
        super().__init__(session)
        self._access_token = "mock_access_token"
    
    async def async_get_access_token(self) -> str:
        return self._access_token


class TestClient:
    """Tests for Client."""

    @pytest.mark.asyncio
    async def test_get_installations(self):
        """Test fetching installations."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
            m.get(
                url,
                payload={
                    "data": [
                        {"id": 123456, "description": "Home"},
                        {"id": 789012, "description": "Office"}
                    ]
                }
            )
            
            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = Client(auth)
                
                installations = await client.get_installations()
                
                assert len(installations) == 2
                assert installations[0]["id"] == 123456
                assert installations[1]["id"] == 789012

    @pytest.mark.asyncio
    async def test_get_installations_error(self):
        """Test error handling when fetching installations fails."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
            m.get(url, status=500)
            
            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = Client(auth)
                
                with pytest.raises(VitoConnectionError):
                    await client.get_installations()

    @pytest.mark.asyncio
    async def test_get_gateways(self):
        """Test fetching gateways."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"
            m.get(
                url,
                payload={
                    "data": [
                        {"serial": "1234567890", "installationId": 123456}
                    ]
                }
            )
            
            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = Client(auth)
                
                gateways = await client.get_gateways()
                
                assert len(gateways) == 1
                assert gateways[0]["serial"] == "1234567890"

    @pytest.mark.asyncio
    async def test_get_devices(self):
        """Test fetching devices for a gateway."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/123456/gateways/1234567890/devices"
            m.get(
                url,
                payload={
                    "data": [
                        {"id": "0", "deviceType": "heating", "modelId": "E3_Vitocal"},
                        {"id": "gateway", "deviceType": "tcu", "modelId": "E3_TCU"}
                    ]
                }
            )
            
            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = Client(auth)
                
                devices = await client.get_devices(123456, "1234567890")
                
                assert len(devices) == 2
                assert devices[0]["id"] == "0"
                assert devices[0]["deviceType"] == "heating"

    @pytest.mark.asyncio
    async def test_get_features(self):
        """Test fetching all features for a device."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features"
            m.get(
                url,
                payload={
                    "data": [
                        {"feature": "heating.sensors.temperature.outside"},
                        {"feature": "heating.circuits.0"}
                    ]
                }
            )
            
            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = Client(auth)
                
                features = await client.get_features(123456, "1234567890", "0")
                
                assert len(features) == 2
                assert features[0]["feature"] == "heating.sensors.temperature.outside"

    @pytest.mark.asyncio
    async def test_get_feature(self):
        """Test fetching a specific feature."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/heating.sensors.temperature.outside"
            m.get(
                url,
                payload={
                    "data": {
                        "feature": "heating.sensors.temperature.outside",
                        "properties": {
                            "value": {"type": "number", "value": 5.5, "unit": "celsius"}
                        }
                    }
                }
            )
            
            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = Client(auth)
                
                feature = await client.get_feature(
                    123456, "1234567890", "0", "heating.sensors.temperature.outside"
                )
                
                assert feature["feature"] == "heating.sensors.temperature.outside"
                assert feature["properties"]["value"]["value"] == 5.5

    @pytest.mark.asyncio
    async def test_get_feature_not_found(self):
        """Test error handling when feature is not found."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/nonexistent.feature"
            m.get(
                url,
                status=404,
                payload={
                    "errorType": "FEATURE_NOT_FOUND",
                    "message": "Feature not found"
                }
            )
            
            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = Client(auth)
                
                with pytest.raises(VitoConnectionError) as exc_info:
                    await client.get_feature(
                        123456, "1234567890", "0", "nonexistent.feature"
                    )
                
                assert "404" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_features_with_values(self):
        """Test getting features with formatted values."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features"
            
            # Reponse with simulated features
            mock_data = {
                "data": [
                    {
                        "feature": "heating.sensors.temperature.outside",
                        "isEnabled": True,
                        "properties": {
                            "value": {"type": "number", "value": 12.5, "unit": "celsius"},
                            "status": {"type": "string", "value": "connected"}
                        }
                    },
                    {
                        "feature": "heating.nested.complex",
                        "isEnabled": True,
                        "properties": {
                            "day": {"type": "array", "value": [1.1, 2.2]},
                            "total": {"type": "number", "value": 100, "unit": "kWh"}
                        }
                    },
                    {
                        "feature": "heating.disabled.feature",
                        "isEnabled": False,
                        "properties": {}
                    }
                ]
            }
            
            m.get(url, payload=mock_data)
            # Second call for the !only_enabled case
            m.get(url, payload=mock_data)
            
            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = Client(auth)
                
                # Test 1: Only enabled
                features = await client.get_features_with_values(123456, "1234567890", "0", only_enabled=True)
                assert len(features) == 2
                
                f1 = next(f for f in features if f["name"] == "heating.sensors.temperature.outside")
                # 'value' and 'status' should be combined cleanly
                assert "12.5 celsius" in f1["value"]
                assert "connected" in f1["value"]
                
                f2 = next(f for f in features if f["name"] == "heating.nested.complex")
                # Nested keys should be prefixed
                assert "day: [1.1, 2.2]" in f2["value"]
                assert "total: 100 kWh" in f2["value"]

                # Test 2: All features
                features_all = await client.get_features_with_values(123456, "1234567890", "0", only_enabled=False)
                assert len(features_all) == 3
                assert any(f["name"] == "heating.disabled.feature" for f in features_all)
