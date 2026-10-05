"""Integration tests for realistic offline workflows using FixtureViClient."""

import pytest

from vi_api_client import FixtureViClient
from vi_api_client.models import Device, Feature

FIXTURE_DEVICES = FixtureViClient.get_available_fixture_devices()


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_discovery_uses_shared_domain_conversion_without_auth():
    """Fixture discovery should share the client conversion without credentials."""
    # Arrange: Use a fixture-backed client with no auth or HTTP dependencies.
    client = FixtureViClient("Vitodens200W")

    # Act: Discover installations and gateways through the inherited workflow.
    installations = await client.get_installations()
    gateways = await client.get_gateways()

    # Assert: Fixture responses are converted by the shared client implementation.
    assert installations[0].id == "99999"
    assert installations[0].description == "Mock Installation (Vitodens200W)"
    assert gateways[0].serial == "MOCK_GATEWAY_SERIAL"
    assert gateways[0].installation_id == installations[0].id


async def _enabled_features_by_name(fixture_device: str) -> dict[str, Feature]:
    """Return the enabled features of a discovered fixture device by name."""
    client = FixtureViClient(fixture_device)
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    features = await client.get_features(device, only_enabled=True)
    return {feature.name: feature for feature in features}


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_http_requests")
async def test_gas_boiler_fixture_exposes_writable_curve_and_outside_temperature():
    """The gas boiler fixture carries a bounded curve slope and an outside sensor."""
    # Act: Read the enabled features of the gas boiler fixture.
    features = await _enabled_features_by_name("Vitodens200W")

    # Assert: The slope is writable within the command limits of the fixture,
    # and the outside temperature is a numeric reading in Celsius.
    slope = features["heating.circuits.0.heating.curve.slope"]
    assert slope.value is not None
    assert slope.control is not None
    assert (slope.control.min, slope.control.max) == (0.2, 3.5)
    outside_temperature = features["heating.sensors.temperature.outside"]
    assert isinstance(outside_temperature.value, (int, float))
    assert outside_temperature.unit == "celsius"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_http_requests")
async def test_heat_pump_fixture_exposes_compressor_sensor_and_writable_mode():
    """The heat pump fixture carries compressor data and a writable circuit mode."""
    # Act: Read the enabled features of the heat pump fixture.
    features = await _enabled_features_by_name("Vitocal250A")

    # Assert: Heat-pump-specific sensors and the circuit mode control are present.
    assert features["heating.compressors.0.sensors.temperature.outlet"].unit == (
        "celsius"
    )
    assert features["heating.circuits.0.operating.modes.active"].is_writable is True


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_set_feature_stays_offline():
    """Fixture writes succeed through simulated command execution."""
    # Arrange: Hydrate a fixture-backed heat pump without authentication or HTTP resources.
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
@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_gateway_device_refresh_stays_offline():
    """Gateway refresh keeps the order and metadata of fixture devices."""
    # Arrange: Use two known devices on the same fixture gateway.
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

    # Assert: Fixture refresh preserves order and metadata without HTTP access.
    assert result.is_complete
    assert [device.id for device in result.updated_devices] == ["10", "0"]
    assert [device.model_id for device in result.updated_devices] == [
        "model-10",
        "model-0",
    ]
    assert all(device.features for device in result.updated_devices)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_http_requests")
@pytest.mark.parametrize("fixture_device", FIXTURE_DEVICES, ids=FIXTURE_DEVICES)
async def test_every_catalog_device_supports_the_standard_workflow(fixture_device):
    """Each bundled catalog device runs discovery, a filtered read, and a write."""
    # Arrange: Create a fixture client for one bundled catalog device.
    client = FixtureViClient(fixture_device)

    # Act: Discover the installation, gateway, and hydrated device.
    installation = (await client.get_installations())[0]
    gateway = (await client.get_gateways())[0]
    devices = await client.get_devices(
        installation_id=installation.id,
        gateway_serial=gateway.serial,
        include_features=True,
    )

    # Assert: The chain yields one hydrated device with features.
    assert len(devices) == 1
    device = devices[0]
    assert device.features

    # Act: Read one feature back through the name-filtered public read.
    probe = device.features[0]
    filtered = await client.get_features(device, feature_names=[probe.name])

    # Assert: The filtered read returns exactly the requested feature.
    assert [feature.name for feature in filtered] == [probe.name]

    # Act: Write the current value of the first writable feature back.
    writable = next(
        (
            feature
            for feature in device.features
            if feature.is_writable and feature.value is not None
        ),
        None,
    )
    # Every bundled device exposes at least one writable feature; a catalog
    # addition without one must be reviewed instead of silently skipped.
    assert writable is not None
    response, updated_device = await client.set_feature(
        device, writable, writable.value
    )

    # Assert: The write succeeds and the returned snapshot carries the value.
    assert response.success
    updated = updated_device.get_feature(writable.name)
    assert updated is not None
    assert updated.value == writable.value


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_event_history_returns_one_page_with_cursor():
    """Fixture clients should serve the event history page without network."""
    # Arrange: Use a fixture-backed client with no auth or HTTP dependencies.
    client = FixtureViClient("Vitodens200W")

    # Act: Fetch the first page of a rolling week.
    page = await client.get_event_history("99999", days=7, limit=50)

    # Assert: The bundled page keeps events, bodies, and pagination.
    assert len(page.events) == 3
    first = page.events[0]
    assert first.event_type == "feature-changed"
    assert first.created_at == "2026-09-20T10:15:30.878Z"
    assert first.event_timestamp == "2026-09-20T10:15:30.000Z"
    assert first.gateway_serial == "7630175843100101"
    assert first.body == {
        "featureName": "heating.dhw.temperature.main",
        "commandName": "setTargetTemperature",
        "commandBody": {"temperature": 55},
    }
    assert first.fields["origin"] == "API"
    assert page.events[1].body is None
    assert page.next_cursor == "b3BhcXVlLWN1cnNvci10b2tlbg=="


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_event_history_serves_final_page_for_cursor_requests():
    """Fixture cursor requests should serve the observed final page shape."""
    # Arrange: Use a fixture-backed client with no auth or HTTP dependencies.
    client = FixtureViClient("Vitodens200W")

    # Act: Request the continuation page of the bundled window.
    final_page = await client.get_event_history(
        "99999", cursor="b3BhcXVlLWN1cnNvci10b2tlbg=="
    )

    # Assert: The cursor page is the empty final page without a next cursor.
    assert final_page.events == ()
    assert final_page.next_cursor is None
