"""Integration tests for the full workflow using Mock Client."""

import pytest

from vi_api_client import FixtureViClient
from vi_api_client.models import Device


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mock_discovery_uses_shared_domain_conversion_without_auth():
    """Mock discovery should share client conversion without a live connector."""
    # Arrange: Use a fixture-backed client with no auth or HTTP dependencies.
    client = FixtureViClient("Vitodens200W")

    # Act: Discover installations and gateways through the inherited workflow.
    installations = await client.get_installations()
    gateways = await client.get_gateways()

    # Assert: Fixture envelopes are converted by the shared client implementation.
    assert installations[0].id == "99999"
    assert installations[0].description == "Mock Installation (Vitodens200W)"
    assert gateways[0].serial == "MOCK_GATEWAY_SERIAL"
    assert gateways[0].installation_id == installations[0].id
    assert not hasattr(client, "connector")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mock_workflow_vitodens():
    """Verify Vitodens (gas boiler) workflow with mock data."""
    # Arrange: Prepare the fixture client and device.
    client = FixtureViClient("Vitodens200W")
    device = Device(
        id="0",
        gateway_serial="MOCK_GW",
        installation_id="123",
        model_id="Vitodens200W",
        device_type="heating",
        status="Online",
    )

    # Act: Fetch all enabled features from the mock API.
    features = await client.get_features(device, only_enabled=True)

    # Assert: Verify feature count and critical heating curve properties.
    assert len(features) > 0
    assert all(feature.is_enabled for feature in features)

    # Verify the heating curve slope feature exists and is writable.
    slope = next(
        (
            feature
            for feature in features
            if feature.name == "heating.circuits.0.heating.curve.slope"
        ),
        None,
    )
    assert slope is not None
    assert slope.value is not None
    assert slope.is_writable is True

    # Verify constraints are correctly parsed.
    assert slope.control is not None
    assert slope.control.min == 0.2
    assert slope.control.max == 3.5

    # Verify temperature sensor feature.
    temp = next(
        (
            feature
            for feature in features
            if feature.name == "heating.sensors.temperature.outside"
        ),
        None,
    )
    assert temp is not None
    assert isinstance(temp.value, (int, float))
    assert temp.unit == "celsius"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mock_workflow_vitocal():
    """Verify heat pump specific features (compressor) with mock data."""
    # Arrange: Prepare the fixture client for a heat pump device.
    client = FixtureViClient("Vitocal250A")
    device = Device(
        id="0",
        gateway_serial="MOCK_GW_HP",
        installation_id="123",
        model_id="Vitocal250A",
        device_type="heatpump",
        status="Online",
    )

    # Act: Fetch all enabled features from the mock API.
    features = await client.get_features(device, only_enabled=True)

    # Assert: Verify basic feature count.
    assert len(features) > 0

    # Verify compressor outlet temperature sensor (heat pump specific).
    outlet_temp = next(
        (
            feature
            for feature in features
            if feature.name == "heating.compressors.0.sensors.temperature.outlet"
        ),
        None,
    )
    assert outlet_temp is not None
    assert outlet_temp.unit == "celsius"

    # Verify a writable circuit mode feature exists.
    circuit_mode = next(
        (
            feature
            for feature in features
            if feature.name == "heating.circuits.0.operating.modes.active"
        ),
        None,
    )
    assert circuit_mode is not None
    assert circuit_mode.is_writable is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mock_set_feature_stays_offline():
    """Verify mock writes use simulated execution without live resources."""
    # Arrange: Hydrate a mock heat pump without authentication or HTTP resources.
    client = FixtureViClient("Vitocal250A")
    device = (
        await client.get_devices(
            installation_id="99999",
            gateway_serial="MOCK_GW",
            include_features=True,
        )
    )[0]
    slope = device.get_feature("heating.circuits.0.heating.curve.slope")
    assert slope is not None

    # Act: Set the writable slope through the standard high-level client method.
    response, updated_device = await client.set_feature(device, slope, 0.7)

    # Assert: The command should succeed locally.
    updated_slope = updated_device.get_feature(slope.name)
    assert response.success is True
    assert updated_slope is not None
    assert updated_slope.value == 0.7
    assert slope.value != updated_slope.value


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mock_workflow_auto_hydration():
    """Verify that get_devices(include_features=True) works with MockClient."""
    # Arrange
    client = FixtureViClient("Vitodens200W")

    # Act: Use the new single-step hydration (Smart get_devices)
    # IDs don't matter much for MockClient, but we provide them for consistency
    devices = await client.get_devices(
        installation_id="99999", gateway_serial="MOCK_GW", include_features=True
    )

    # Assert
    assert len(devices) == 1
    device = devices[0]

    # The device should be already hydrated (features list populated)
    # without needing a separate manual step.
    assert len(device.features) > 0

    # Verify we can find a standard feature
    temp = device.get_feature("heating.sensors.temperature.outside")
    assert temp is not None
    assert temp.value == 9


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mock_gateway_device_refresh_stays_offline():
    """Verify gateway-scoped refresh has offline mock parity."""
    # Arrange: Use two known devices on the same mock gateway.
    client = FixtureViClient("Vitodens200W")
    devices = [
        Device(
            id=device_id,
            gateway_serial="MOCK_GW",
            installation_id="99999",
            model_id=f"model-{device_id}",
            device_type="heating",
            status="connected",
        )
        for device_id in ("10", "0")
    ]

    # Act: Refresh both devices through the gateway-scoped public API.
    result = await client.update_gateway_devices(devices)

    # Assert: Mock refresh preserves order and metadata without HTTP access.
    assert result.is_complete
    assert [device.id for device in result.updated_devices] == ["10", "0"]
    assert [device.model_id for device in result.updated_devices] == [
        "model-10",
        "model-0",
    ]
    assert all(device.features for device in result.updated_devices)
