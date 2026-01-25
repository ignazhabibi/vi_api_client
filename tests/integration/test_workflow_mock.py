"""Integration tests for the full workflow using Mock Client."""

import pytest

from vi_api_client import MockViClient
from vi_api_client.models import Device


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mock_workflow_vitodens():
    """Verify Vitodens (gas boiler) workflow with mock data."""
    # Arrange: Prepare the mock client and device.
    client = MockViClient("Vitodens200W")
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
    # Arrange: Prepare the mock client for a heat pump device.
    client = MockViClient("Vitocal250A")
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
