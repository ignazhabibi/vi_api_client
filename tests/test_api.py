"""Tests for vitoclient.api module (Flat Architecture)."""

import re
from dataclasses import replace

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client.api import ViClient
from vi_api_client.auth import AbstractAuth
from vi_api_client.const import (
    API_BASE_URL,
    ENDPOINT_ANALYTICS_THERMAL,
    ENDPOINT_FEATURES,
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
    # Arrange: Load fixture and mock API endpoint for installations.
    data = load_fixture_json("installations.json")

    with aioresponses() as m:
        url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # Act: Fetch installations from API.
            installations = await client.get_installations()

            # Assert: Should return 2 installations with correct IDs.
            assert len(installations) == 2
            assert installations[0].id == "123456"
            assert installations[1].id == "789012"


@pytest.mark.asyncio
async def test_get_installations_error():
    """Test error handling when fetching installations fails."""
    # Arrange: Mock API to return 500 Internal Server Error.
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"

    with aioresponses() as m:
        m.get(url, status=500)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # Act and Assert: Fetch should raise ViServerInternalError.
            with pytest.raises(ViServerInternalError):
                await client.get_installations()


@pytest.mark.asyncio
async def test_get_gateways(load_fixture_json):
    """Test fetching gateways."""
    # Arrange: Load fixture and mock gateways endpoint.
    data = load_fixture_json("gateways.json")
    url = f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"

    with aioresponses() as m:
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # Act: Fetch gateways from API.
            gateways = await client.get_gateways()

            # Assert: Should return 1 gateway with correct serial.
            assert len(gateways) == 1
            assert gateways[0].serial == "1234567890"


@pytest.mark.asyncio
async def test_get_devices(load_fixture_json):
    """Test fetching devices for a gateway."""
    # Arrange: Load device fixture and mock devices endpoint.
    data = load_fixture_json("devices_heating.json")
    inst_id = "123456"
    gw_serial = "1234567890"
    url = (
        f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/{inst_id}/gateways/{gw_serial}/devices"
    )

    with aioresponses() as m:
        m.get(url, payload=data)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # Act: Fetch devices for specific gateway.
            devices = await client.get_devices(inst_id, gw_serial)

            # Assert: Should return 2 devices with correct properties.
            assert len(devices) == 2
            assert devices[0].id == "0"
            assert devices[0].device_type == "heating"


@pytest.mark.asyncio
async def test_get_features(load_fixture_json):
    """Test fetching all features for a device (Parsing check)."""
    # Arrange: Create device and mock features endpoint to return all features.
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

            # Act: Fetch all features for the device.
            features = await client.get_features(device)

            # Assert: Verify all features are returned and parsed correctly.
            assert len(features) == 2
            assert features[0].name == "heating.sensors.temperature.outside"
            assert features[0].value == 5.5
            assert features[1].name == "heating.circuits.0.active"


@pytest.mark.asyncio
async def test_get_feature(load_fixture_json):
    """Test fetching a specific feature."""
    # Arrange: Create device and mock features endpoint to return a single filtered feature.
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

            # Act: Fetch a specific feature by name.
            features = await client.get_features(
                device, feature_names=["heating.sensors.temperature.outside"]
            )

            # Assert: Verify only the requested feature is returned and parsed.
            assert len(features) == 1
            feature = features[0]

            assert feature.name == "heating.sensors.temperature.outside"
            assert feature.value == 5.5


@pytest.mark.asyncio
async def test_get_feature_not_found(load_fixture_json):
    """Test fetching a non-existent feature."""
    # Arrange: Create device and mock features endpoint to return 404 for a non-existent feature.
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

            # Act and Assert: Execute and verify in one step.
            with pytest.raises(ViNotFoundError):
                await client.get_features(device, feature_names=["nonexistent.feature"])


@pytest.mark.asyncio
async def test_get_consumption(load_fixture_json):
    """Test the get_consumption method with various metrics."""
    # Arrange: Prepare test data and fixtures.
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

            # Act: Fetch consumption data with summary metric.
            result_summary = await client.get_consumption(
                device, start, end, metric="summary"
            )

            # Assert: Summary should return 3 features with total consumption.
            assert isinstance(result_summary, list)
            assert len(result_summary) == 3

            feature_total = next(
                feature
                for feature in result_summary
                if feature.name == "analytics.heating.power.consumption.total"
            )
            assert feature_total.value == 15.5
            assert feature_total.unit == "kilowattHour"

            # Act: Fetch consumption data for total metric only.
            result_total = await client.get_consumption(
                device, start, end, metric="total"
            )

            # Assert: Individual metric should return single feature.
            assert isinstance(result_total, list)
            assert len(result_total) == 1
            assert result_total[0].name == "analytics.heating.power.consumption.total"
            assert result_total[0].value == 15.5

            # Act: Attempt to fetch with invalid metric (should raise ValueError).
            with pytest.raises(ValueError):
                await client.get_consumption(device, start, end, metric="invalid")


@pytest.mark.asyncio
async def test_update_device(load_fixture_json):
    """Test efficient device update."""
    # Arrange: Prepare test data and fixtures.
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

            # Act: Execute the function being tested.
            updated_dev = await client.update_device(dev)

            # Assert: Verify the results match expectations.
            assert updated_dev.id == "0"
            assert len(updated_dev.features) == 1
            assert updated_dev.features[0].name == "new.feature"


@pytest.mark.asyncio
async def test_validate_constraints_step():
    """Test step validation logic."""
    # Arrange: Create test values for step validation.
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

    # Act & Assert: Case 1 (Valid Step)
    client._validate_numeric_constraints(ctrl, 10.5)  # Should pass
    client._validate_numeric_constraints(ctrl, 11.0)  # Should pass

    # Act & Assert: Case 2 (Invalid Step)
    with pytest.raises(ValueError) as exc:
        client._validate_numeric_constraints(ctrl, 10.7)
    assert "does not align with step" in str(exc.value)

    # Act & Assert: Case 3 (Floating point precision)
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


@pytest.mark.asyncio
async def test_get_devices_with_hydration(load_fixture_json):
    """Test fetching devices with automatic feature hydration."""
    # Arrange: Load fixtures.
    devices_data = load_fixture_json("devices_heating.json")
    features_data = load_fixture_json("features_heating_sensors.json")

    inst_id = "123456"
    gw_serial = "1234567890"

    devices_url = (
        f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/{inst_id}/gateways/{gw_serial}/devices"
    )

    with aioresponses() as m:
        # 1. Mock Devices Call
        m.get(devices_url, payload=devices_data)

        # 2. Mock Features Call (for any device ID on this gateway)
        features_pattern = re.compile(
            f"{API_BASE_URL}{ENDPOINT_FEATURES}/{inst_id}/gateways/{gw_serial}/devices/.*/features/filter"
        )
        m.post(features_pattern, payload=features_data, repeat=True)

        async with aiohttp.ClientSession() as session:
            auth = MockAuth(session)
            client = ViClient(auth)

            # Act: Fetch devices with hydration enabled.
            devices = await client.get_devices(
                inst_id, gw_serial, include_features=True
            )

            # Assert:
            assert len(devices) == 2

            # Check Device 0 (Heating)
            dev0 = next(d for d in devices if d.id == "0")
            assert len(dev0.features) > 0
            assert dev0.features[0].name == "heating.sensors.temperature.outside"


@pytest.mark.asyncio
async def test_set_feature_with_dependency(load_fixture_json):
    """Test setting a feature that has a sibling dependency (slope needs shift)."""
    # Arrange
    fixtures_data = load_fixture_json("feature_heating_curve.json")

    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"

    # URL to fetch specific feature (or all features in this filter context)
    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"

    # URL for the command
    command_url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/"
        "features/heating.circuits.0.heating.curve/commands/setCurve"
    )

    with aioresponses() as m:
        # Mock Feature Fetching
        m.post(features_url, payload={"data": fixtures_data})

        # Mock Command Execution
        m.post(command_url, payload={"data": {"success": True}})

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            # 1. Manually construct device
            device = Device(
                id=device_id,
                gateway_serial=gw_serial,
                installation_id=install_id,
                model_id="Vitocal250A",
                device_type="heatpump",
                status="Online",
            )

            # 2. Fetch features (this now uses our small fixture)
            features = await client.get_features(device)
            device = replace(device, features=features)

            # 3. Find the 'slope' feature
            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")
            assert slope_feature is not None

            # Act
            # Set slope to 1.2.
            # The fixture says 'shift' is 4.
            # Expect payload: { "slope": 1.2, "shift": 4 }
            await client.set_feature(device, slope_feature, 1.2)

            # Assert
            # Find the call with the matching URL
            found_call = None
            for (method, url), calls in m.requests.items():
                if method == "POST" and str(url) == command_url:
                    found_call = calls[0]
                    break

            assert found_call is not None
            assert found_call.kwargs["json"] == {"slope": 1.2, "shift": 4}


@pytest.mark.asyncio
async def test_set_feature_validation_limit(load_fixture_json):
    """Test client-side validation for min/max limits."""
    fixtures_data = load_fixture_json("feature_heating_curve.json")
    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"
    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"

    with aioresponses() as m:
        m.post(features_url, payload={"data": fixtures_data})

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            device = Device(
                id=device_id,
                gateway_serial=gw_serial,
                installation_id=install_id,
                model_id="M",
                device_type="H",
                status="O",
            )
            device = replace(device, features=await client.get_features(device))

            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")

            # Act & Assert: Max limit violation (Max is 3.5)
            with pytest.raises(ValueError, match=r"Value 5.0 > max"):
                await client.set_feature(device, slope_feature, 5.0)


@pytest.mark.asyncio
async def test_set_feature_validation_step(load_fixture_json):
    """Test client-side validation for stepping."""
    fixtures_data = load_fixture_json("feature_heating_curve.json")
    install_id = "123"
    gw_serial = "GW123"
    device_id = "0"
    features_url = f"{API_BASE_URL}{ENDPOINT_FEATURES}/{install_id}/gateways/{gw_serial}/devices/{device_id}/features/filter"

    with aioresponses() as m:
        m.post(features_url, payload={"data": fixtures_data})

        async with aiohttp.ClientSession() as session:
            client = ViClient(MockAuth(session))

            device = Device(
                id=device_id,
                gateway_serial=gw_serial,
                installation_id=install_id,
                model_id="M",
                device_type="H",
                status="O",
            )
            device = replace(device, features=await client.get_features(device))

            slope_feature = device.get_feature("heating.circuits.0.heating.curve.slope")

            # Act & Assert: Step violation (Step is 0.1)
            # 1.25 is not valid for step 0.1
            with pytest.raises(ValueError, match=r"does not align with step"):
                await client.set_feature(device, slope_feature, 1.25)
