"""Integration tests for realistic offline workflows using FixtureViClient."""

import pytest

from vi_api_client import FixtureViClient
from vi_api_client.models import Device


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fixture_discovery_uses_shared_domain_conversion_without_auth():
    """Fixture discovery should share client conversion without a live connector."""
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
async def test_fixture_workflow_vitodens():
    """Verify Vitodens (gas boiler) workflow with fixture data."""
    # Arrange: Discover the Vitodens device through the fixture chain.
    client = FixtureViClient("Vitodens200W")
    installation = (await client.get_installations())[0]
    gateway = (await client.get_gateways())[0]
    device = (
        await client.get_devices(
            installation_id=installation.id, gateway_serial=gateway.serial
        )
    )[0]

    # Act: Fetch all enabled features for the discovered device.
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
async def test_fixture_workflow_vitocal():
    """Verify heat pump specific features (compressor) with fixture data."""
    # Arrange: Discover the heat pump device through the fixture chain.
    client = FixtureViClient("Vitocal250A")
    installation = (await client.get_installations())[0]
    gateway = (await client.get_gateways())[0]
    device = (
        await client.get_devices(
            installation_id=installation.id, gateway_serial=gateway.serial
        )
    )[0]

    # Act: Fetch all enabled features for the discovered device.
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
async def test_fixture_set_feature_stays_offline():
    """Verify fixture-backed writes use simulated execution without live resources."""
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
async def test_fixture_workflow_auto_hydration():
    """Verify that get_devices(include_features=True) works with FixtureViClient."""
    # Arrange
    client = FixtureViClient("Vitodens200W")

    # Act: Use the new single-step hydration (Smart get_devices)
    # IDs don't matter much for FixtureViClient, but we provide them for consistency
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
async def test_fixture_gateway_device_refresh_stays_offline():
    """Verify gateway-scoped refresh has offline mock parity."""
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
async def test_every_catalog_device_supports_the_standard_workflow(
    available_fixture_devices,
):
    """Each bundled catalog device runs the full offline workflow chain."""
    for device_name in available_fixture_devices:
        # Arrange: Create a fixture client for one bundled catalog device.
        client = FixtureViClient(device_name)

        # Act: Discover the installation, gateway, and hydrated device.
        installation = (await client.get_installations())[0]
        gateway = (await client.get_gateways())[0]
        devices = await client.get_devices(
            installation_id=installation.id,
            gateway_serial=gateway.serial,
            include_features=True,
        )

        # Assert: The chain yields one hydrated device with features.
        assert devices, f"{device_name}: discovery yielded no device"
        device = devices[0]
        assert device.features, f"{device_name}: hydration yielded no features"

        # Act: Read one feature back through the name-filtered public read.
        probe = device.features[0]
        filtered = await client.get_features(device, feature_names=[probe.name])

        # Assert: The filtered read returns exactly the requested feature.
        assert [feature.name for feature in filtered] == [probe.name], (
            f"{device_name}: name-filtered read lost {probe.name}"
        )

        # Act: Write the current value of the first writable feature, if any.
        writable = next(
            (
                feature
                for feature in device.features
                if feature.is_writable and feature.value is not None
            ),
            None,
        )
        if writable is None:
            continue
        response, updated_device = await client.set_feature(
            device, writable, writable.value
        )

        # Assert: The write succeeds and the returned snapshot carries the value.
        assert response.success, f"{device_name}: write to {writable.name} failed"
        updated = updated_device.get_feature(writable.name)
        assert updated is not None
        assert updated.value == writable.value, device_name


@pytest.mark.integration
@pytest.mark.asyncio
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
