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


@pytest.mark.asyncio
async def test_get_installations(load_fixture_json):
    """Test fetching installations."""
    # --- ARRANGE ---
    data = load_fixture_json("installations.json")

    with aioresponses() as m:
        url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # --- ACT ---
            installations = await client.get_installations()

            # --- ASSERT ---
            assert len(installations) == 2
            assert installations[0].id == "123456"
            assert installations[1].id == "789012"


@pytest.mark.asyncio
async def test_get_installations_error():
    """Test error handling when fetching installations fails."""
    # --- ARRANGE ---
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"

    with aioresponses() as m:
        m.get(url, status=500)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # --- ACT & ASSERT ---
            with pytest.raises(ViServerInternalError):
                await client.get_installations()


@pytest.mark.asyncio
async def test_get_gateways(load_fixture_json):
    """Test fetching gateways."""
    # --- ARRANGE ---
    data = load_fixture_json("gateways.json")
    url = f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"

    with aioresponses() as m:
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # --- ACT ---
            gateways = await client.get_gateways()

            # --- ASSERT ---
            assert len(gateways) == 1
            assert gateways[0].serial == "1234567890"


@pytest.mark.asyncio
async def test_get_devices(load_fixture_json):
    """Test fetching devices for a gateway."""
    # --- ARRANGE ---
    data = load_fixture_json("devices_heating.json")
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/123456/gateways/1234567890/devices"

    with aioresponses() as m:
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # --- ACT ---
            devices = await client.get_devices(123456, "1234567890")

            # --- ASSERT ---
            assert len(devices) == 2
            assert devices[0].id == "0"
            assert devices[0].device_type == "heating"


@pytest.mark.asyncio
async def test_get_features(load_fixture_json):
    """Test fetching all features for a device (Parsing check)."""
    # --- ARRANGE ---
    data = load_fixture_json("features_heating_sensors.json")
    url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/filter"

    with aioresponses() as m:
        m.post(url, payload=data)

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

            # --- ACT ---
            features = await client.get_features(device)

            # --- ASSERT ---
            assert len(features) == 2
            assert features[0].name == "heating.sensors.temperature.outside"
            assert features[0].value == 5.5
            assert features[1].name == "heating.circuits.0.active"


@pytest.mark.asyncio
async def test_get_feature(load_fixture_json):
    """Test fetching a specific feature."""
    # --- ARRANGE ---
    data = load_fixture_json("features_filtered_single.json")
    url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/filter"

    with aioresponses() as m:
        m.post(url, payload=data)

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

            # --- ACT ---
            features = await client.get_features(
                device, feature_names=["heating.sensors.temperature.outside"]
            )

            # --- ASSERT ---
            assert len(features) == 1
            feature = features[0]

            assert feature.name == "heating.sensors.temperature.outside"
            assert feature.value == 5.5


@pytest.mark.asyncio
async def test_get_feature_not_found(load_fixture_json):
    """Test fetching a non-existent feature."""
    # --- ARRANGE ---
    data = load_fixture_json("device_error_404.json")
    url = f"{API_BASE_URL}/iot/v2/features/installations/123456/gateways/1234567890/devices/0/features/filter"

    with aioresponses() as m:
        m.post(url, status=404, payload=data)

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

            # --- ACT & ASSERT ---
            with pytest.raises(ViNotFoundError):
                await client.get_features(device, feature_names=["nonexistent.feature"])


@pytest.mark.asyncio
async def test_get_consumption(load_fixture_json):
    """Test the get_consumption method with various metrics."""
    # --- ARRANGE ---
    data = load_fixture_json("analytics/consumption_summary.json")
    url = f"{API_BASE_URL}{ENDPOINT_ANALYTICS_THERMAL}"

    with aioresponses() as m:
        m.post(url, payload=data, repeat=True)

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

            # --- ACT 1: Summary ---
            result_summary = await client.get_consumption(
                device, start, end, metric="summary"
            )

            # --- ASSERT 1 ---
            assert isinstance(result_summary, list)
            assert len(result_summary) == 3

            f_total = next(
                f
                for f in result_summary
                if f.name == "analytics.heating.power.consumption.total"
            )
            assert f_total.value == 15.5
            assert f_total.unit == "kilowattHour"

            # --- ACT 2: Individual Metric ---
            result_total = await client.get_consumption(
                device, start, end, metric="total"
            )

            # --- ASSERT 2 ---
            assert isinstance(result_total, list)
            assert len(result_total) == 1
            assert result_total[0].name == "analytics.heating.power.consumption.total"
            assert result_total[0].value == 15.5

            # --- ACT 3: Invalid Metric ---
            with pytest.raises(ValueError):
                await client.get_consumption(device, start, end, metric="invalid")


@pytest.mark.asyncio
async def test_update_device(load_fixture_json):
    """Test efficient device update."""
    # --- ARRANGE ---
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
            client = ViClient(MockAuth(session))

            # --- ACT ---
            updated_dev = await client.update_device(dev)

            # --- ASSERT ---
            assert updated_dev.id == "0"
            assert len(updated_dev.features) == 1
            assert updated_dev.features[0].name == "new.feature"


@pytest.mark.asyncio
async def test_validate_constraints_step():
    """Test step validation logic."""
    # --- ARRANGE ---
    # Use a mock/stub since we just want to test the _validate_constraints method logic
    client = ViClient(None)  # type: ignore

    # Mode 1: Valid Step
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

    # --- ACT & ASSERT (Case 1) ---
    client._validate_numeric_constraints(ctrl, 10.5)  # Should pass
    client._validate_numeric_constraints(ctrl, 11.0)  # Should pass

    # --- ACT & ASSERT (Case 2: Invalid Step) ---
    with pytest.raises(ValueError) as exc:
        client._validate_numeric_constraints(ctrl, 10.7)
    assert "does not align with step" in str(exc.value)

    # --- ACT & ASSERT (Case 3: Floating point precision) ---
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
    client._validate_numeric_constraints(ctrl2, 0.3)  # Should pass despite float arith
