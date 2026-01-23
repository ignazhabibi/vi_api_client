"""Tests for vitoclient.api module (Flat Architecture)."""

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client.api import ViClient
from vi_api_client.auth import AbstractAuth
from vi_api_client.const import (
    API_BASE_URL,
    ENDPOINT_ANALYTICS_THERMAL,
    ENDPOINT_GATEWAYS,
    ENDPOINT_INSTALLATIONS,
)
from vi_api_client.exceptions import (
    ViNotFoundError,
    ViServerInternalError,
)
from vi_api_client.models import Device, FeatureControl


class MockAuth(AbstractAuth):
    """Mock implementation of AbstractAuth for testing."""

    def __init__(self, session: aiohttp.ClientSession):
        super().__init__(session)
        self._access_token = "mock_access_token"

    async def async_get_access_token(self) -> str:
        return self._access_token


class TestViClient:
    """Tests for ViClient."""

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
                        {"id": 789012, "description": "Office"},
                    ]
                },
            )

            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = ViClient(auth)

                installations = await client.get_installations()

                assert len(installations) == 2
                assert installations[0].id == "123456"
                assert installations[1].id == "789012"

    @pytest.mark.asyncio
    async def test_get_installations_error(self):
        """Test error handling when fetching installations fails."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
            m.get(url, status=500)

            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = ViClient(auth)

                # Should still raise ViServerInternalError for 500
                with pytest.raises(ViServerInternalError):
                    await client.get_installations()

    @pytest.mark.asyncio
    async def test_get_gateways(self):
        """Test fetching gateways."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"
            m.get(
                url,
                payload={"data": [{"serial": "1234567890", "installationId": 123456}]},
            )

            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = ViClient(auth)

                gateways = await client.get_gateways()

                assert len(gateways) == 1
                assert gateways[0].serial == "1234567890"

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
                        {"id": "gateway", "deviceType": "tcu", "modelId": "E3_TCU"},
                    ]
                },
            )

            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = ViClient(auth)

                devices = await client.get_devices(123456, "1234567890")

                assert len(devices) == 2
                assert devices[0].id == "0"
                assert devices[0].device_type == "heating"

    @pytest.mark.asyncio
    async def test_get_features(self):
        """Test fetching all features for a device (Parsing check)."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/filter"
            m.post(
                url,
                payload={
                    "data": [
                        {
                            "feature": "heating.sensors.temperature.outside",
                            "properties": {"value": {"value": 5.5, "unit": "celsius"}},
                        },
                        {
                            "feature": "heating.circuits.0",
                            "properties": {"active": {"value": True}},
                        },
                    ]
                },
            )

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
                features = await client.get_features(device)

                # Expect 2 flat features.
                # 1. heating.sensors.temperature.outside (value)
                # 2. heating.circuits.0.active
                assert len(features) == 2
                assert features[0].name == "heating.sensors.temperature.outside"
                assert features[0].value == 5.5
                assert features[1].name == "heating.circuits.0.active"

    @pytest.mark.asyncio
    async def test_get_feature(self):
        """Test fetching a specific feature."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/filter"
            m.post(
                url,
                payload={
                    "data": [
                        {
                            "feature": "heating.sensors.temperature.outside",
                            "properties": {
                                "value": {
                                    "type": "number",
                                    "value": 5.5,
                                    "unit": "celsius",
                                }
                            },
                        }
                    ]
                },
            )

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
                features = await client.get_features(
                    device, feature_names=["heating.sensors.temperature.outside"]
                )
                assert len(features) == 1
                feature = features[0]

                assert feature.name == "heating.sensors.temperature.outside"
                assert feature.value == 5.5

    @pytest.mark.asyncio
    async def test_get_feature_not_found(self):
        """Test fetching a non-existent feature."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/filter"
            # Mock 404 from API
            m.post(
                url,
                status=404,
                payload={
                    "viErrorId": "iot.feature-not-found",
                    "errorType": "DEVICE_LEVEL_ERROR",
                    "message": "Feature not found",
                    "reason": "NOT_FOUND",
                },
            )

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
                # With get_features, a missing feature just means empty list, not 404 error logic
                # (The API might return 404 or just empty list for filter, but our client abstracts it)
                # If we mock 404, the connector raises ClientError.
                # BUT: get_features uses filter endpoint (POST). If that returns 404, it raises exception.

                with pytest.raises(ViNotFoundError):
                    await client.get_features(
                        device, feature_names=["nonexistent.feature"]
                    )

    @pytest.mark.asyncio
    async def test_get_consumption(self):
        """Test the get_consumption method with various metrics."""
        with aioresponses() as m:
            url = f"{API_BASE_URL}{ENDPOINT_ANALYTICS_THERMAL}"

            # API response structure
            mock_data = {
                "data": {
                    "data": {
                        "summary": {
                            "heating.power.consumption.total": 15.5,
                            "heating.power.consumption.heating": 10.0,
                            "heating.power.consumption.dhw": 5.5,
                        }
                    }
                }
            }

            m.post(url, payload=mock_data, repeat=True)

            async with aiohttp.ClientSession() as session:
                auth = MockAuth(session)
                client = ViClient(auth)

                device = Device(
                    id="dev",
                    gateway_serial="gw",
                    installation_id="inst",
                    model_id="model",
                    device_type="heating",
                    status="ok",
                )

                start = "2023-01-01T00:00:00"
                end = "2023-01-01T23:59:59"

                # 1. Summary (Default) -> List[Feature]
                result_summary = await client.get_consumption(
                    device, start, end, metric="summary"
                )
                assert isinstance(result_summary, list)
                assert len(result_summary) == 3

                f_total = next(
                    f
                    for f in result_summary
                    if f.name == "analytics.heating.power.consumption.total"
                )
                assert f_total.value == 15.5
                assert f_total.unit == "kilowattHour"

                # 2. Individual Metric -> List[Feature] (always returns list now)
                result_total = await client.get_consumption(
                    device, start, end, metric="total"
                )
                assert isinstance(result_total, list)
                assert len(result_total) == 1
                assert (
                    result_total[0].name == "analytics.heating.power.consumption.total"
                )
                assert result_total[0].value == 15.5

                # 3. Invalid Metric
                with pytest.raises(ValueError):
                    await client.get_consumption(device, start, end, metric="invalid")

    @pytest.mark.asyncio
    async def test_update_device(self):
        """Test efficient device update."""

        with aioresponses() as m:
            # Device has context
            dev = Device(
                id="0",
                gateway_serial="GW1",
                installation_id="123",
                model_id="TestModel",
                device_type="heating",
                status="ok",
            )

            # Expect Post call
            url = f"{API_BASE_URL}/iot/v2/features/installations/123/gateways/GW1/devices/0/features/filter"
            m.post(
                url,
                payload={
                    "data": [
                        {
                            "feature": "new.feature",
                            "properties": {"value": {"value": 1}},
                        }
                    ]
                },
            )

            async with aiohttp.ClientSession() as session:
                client = ViClient(MockAuth(session))

                updated_dev = await client.update_device(dev)

                assert updated_dev.id == "0"
                assert len(updated_dev.features) == 1
                assert updated_dev.features[0].name == "new.feature"

    async def test_validate_constraints_step(self):
        """Test step validation logic."""
        # Use a mock/stub since we just want to test the _validate_constraints method logic
        # But since it's private and part of Client, we can test it by calling it or via set_feature logic if we mocked enough.
        # Easier: Just instantiate ViClient with mock auth and call private method directly (whitebox testing).

        client = ViClient(None)  # type: ignore

        # Case 1: Valid Step
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
        client._validate_numeric_constraints(ctrl, 10.5)  # Should pass
        client._validate_numeric_constraints(ctrl, 11.0)  # Should pass

        # Case 2: Invalid Step (10.5 + 0.2 = 10.7 != 0.5 step)
        with pytest.raises(ValueError) as exc:
            client._validate_numeric_constraints(ctrl, 10.7)
        assert "does not align with step" in str(exc.value)

        # Case 3: Floating point precision (0.1 + 0.2)
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
        client._validate_numeric_constraints(
            ctrl2, 0.3
        )  # Should pass despite float arith
