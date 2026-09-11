"""Public workflow tests for the fixture-backed client."""

import pytest

from vi_api_client import MockViClient


@pytest.mark.asyncio
async def test_mock_device_hydration_uses_deterministic_fixture_topology():
    """Mock device discovery should use the selected fixture in one topology."""
    # Arrange: Select a known fixture device.
    client = MockViClient("Vitodens200W")

    # Act: Discover and hydrate through the public client workflow.
    devices = await client.get_devices(
        "99999", "MOCK_GATEWAY_SERIAL", include_features=True
    )

    # Assert: The public mock represents exactly its selected fixture device.
    assert len(devices) == 1
    assert devices[0].id == "0"
    assert devices[0].model_id == "Vitodens200W"
    assert devices[0].features


@pytest.mark.asyncio
async def test_mock_update_device_returns_hydrated_copy_without_mutating_input():
    """Mock refresh should have the same immutable public contract as live refresh."""
    # Arrange: Discover an unhydrated fixture device.
    client = MockViClient("Vitodens200W")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]

    # Act: Refresh through the public client workflow.
    refreshed_device = await client.update_device(device)

    # Assert: The input remains unhydrated and the returned copy has features.
    assert device.features == []
    assert refreshed_device is not device
    assert refreshed_device.features


@pytest.mark.asyncio
async def test_mock_feature_filters_apply_shared_enabled_and_name_semantics():
    """Mock filtering should retain only requested enabled and ready features."""
    # Arrange: Select a fixture that includes disabled features.
    client = MockViClient("Vitocal200S")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]

    # Act: Request enabled and ready features by explicit names.
    features = await client.get_features(
        device,
        only_enabled=True,
        feature_names=[
            "heating.burners.0",
            "device.serial",
        ],
    )

    # Assert: Shared filtering retains the requested enabled and ready feature only.
    assert [feature.name for feature in features] == ["device.serial"]


@pytest.mark.asyncio
async def test_mock_execute_command_is_offline_and_stateless():
    """Mock command execution should not alter the loaded fixture features."""
    # Arrange: Load a writable fixture feature through the public workflow.
    client = MockViClient("Vitocal250A")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    device = await client.update_device(device)
    feature = device.get_feature("heating.circuits.0.heating.curve.slope")
    assert feature is not None

    # Act: Execute a command with an explicit payload.
    response = await client.execute_command(feature, {"slope": 0.7, "shift": 7.0})

    # Assert: The response is deterministic and the fixture-derived value is unchanged.
    assert response.success
    assert response.reason == "Mock Execution Success"
    assert feature.value == 0.6
