"""Integration tests for the full workflow using Mock Client."""

import pytest

from vi_api_client import MockViClient
from vi_api_client.models import Device


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mock_workflow_vitodens():
    """Verify Vitodens (gas boiler) workflow with mock data."""
    # --- ARRANGE ---
    client = MockViClient("Vitodens200W")
    device = Device(
        id="0",
        gateway_serial="MOCK_GW",
        installation_id="123",
        model_id="Vitodens200W",
        device_type="heating",
        status="Online",
    )

    # --- ACT ---
    # 1. Fetch features
    features = await client.get_features(device, only_enabled=True)

    # --- ASSERT ---
    # Basic Integrity
    assert len(features) > 0
    assert all(f.is_enabled for f in features)

    # Specific Feature Check (Heating Curve)
    slope = next(
        (f for f in features if f.name == "heating.circuits.0.heating.curve.slope"),
        None,
    )
    assert slope is not None
    assert slope.value is not None
    assert slope.is_writable is True

    # Check Constraints
    assert slope.control is not None
    assert slope.control.min == 0.2
    assert slope.control.max == 3.5

    # Specific Feature Check (Sensor)
    temp = next(
        (f for f in features if f.name == "heating.sensors.temperature.outside"), None
    )
    assert temp is not None
    assert isinstance(temp.value, (int, float))
    assert temp.unit == "celsius"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mock_workflow_vitocal():
    """Verify heat pump specific features (compressor) with mock data."""
    # --- ARRANGE ---
    client = MockViClient("Vitocal250A")
    device = Device(
        id="0",
        gateway_serial="MOCK_GW_HP",
        installation_id="123",
        model_id="Vitocal250A",
        device_type="heatpump",
        status="Online",
    )

    # --- ACT ---
    features = await client.get_features(device, only_enabled=True)

    # --- ASSERT ---
    assert len(features) > 0

    # Check for Compressor Feature (Heat Pump specific)
    # Based on fixture: heating.compressors.0
    # Note: "heating.compressors.0" logic in fixture usually expands to .active if boolean?
    # Let's check the grep output again:
    # "feature": "heating.compressors.0", "uri"...
    # It likely has properties like "enabled", "active", "phase".

    # Let's rely on flattened names.
    # If the feature is "heating.compressors.0", the parser flattens it.
    # Let's check for "heating.compressors.0" itself or its children.

    # In flat architecture, if properties has "value", name is base name.
    # If properties has keys, name is base_name + "." + key.

    # Let's check for a known flattened feature from the fixture grep.
    # "heating.compressors.0.sensors.temperature.outlet"
    outlet_temp = next(
        (
            f
            for f in features
            if f.name == "heating.compressors.0.sensors.temperature.outlet"
        ),
        None,
    )
    assert outlet_temp is not None
    assert outlet_temp.unit == "celsius"

    # Check for a writable feature (if any)
    # Based on grep, "heating.compressors.0" has commands like "setPhase".
    # So "heating.compressors.0.phase" might be writable?
    # Or "heating.circuits.0.operating.modes.active" checks.

    circuit_mode = next(
        (f for f in features if f.name == "heating.circuits.0.operating.modes.active"),
        None,
    )
    assert circuit_mode is not None
    assert circuit_mode.is_writable is True
