"""Tests for MockViClient analytics functionality."""

import pytest

from vi_api_client.mock_client import MockViClient


@pytest.mark.asyncio
async def test_get_consumption_with_analytics_fixture() -> None:
    """Test get_consumption with Vitocal250A that has analytics fixture."""
    # Arrange: Initialize mock client with Vitocal250A device name.
    client = MockViClient(device_name="Vitocal250A")
    installations = await client.get_installations()
    gateways = await client.get_gateways()
    devices = await client.get_devices(installations[0].id, gateways[0].serial)
    device = devices[0]

    # Act: Fetch consumption data using summary metric.
    with pytest.warns(DeprecationWarning, match="deprecated"):
        features = await client.get_consumption(device, "2026-01-01", "2026-01-02")

    # Assert: Should return 3 analytics features with expected values.
    assert len(features) == 3
    assert all(f.name.startswith("analytics.") for f in features)

    # Check specific values from the fixture
    dhw_feature = next(
        f for f in features if f.name == "analytics.heating.power.consumption.dhw"
    )
    assert dhw_feature.value == 10.2
    assert dhw_feature.unit == "kilowattHour"

    heating_feature = next(
        f for f in features if f.name == "analytics.heating.power.consumption.heating"
    )
    assert heating_feature.value == 31.6

    total_feature = next(
        f for f in features if f.name == "analytics.heating.power.consumption.total"
    )
    assert total_feature.value == 41.8


@pytest.mark.asyncio
async def test_get_consumption_without_analytics_fixture() -> None:
    """Test get_consumption with device that has no analytics fixture."""
    # Arrange: Initialize mock client with device that has no analytics data.
    client = MockViClient(device_name="Vitodens200W")
    installations = await client.get_installations()
    gateways = await client.get_gateways()
    devices = await client.get_devices(installations[0].id, gateways[0].serial)
    device = devices[0]

    # Act: Attempt to fetch consumption data.
    with pytest.warns(DeprecationWarning, match="deprecated"):
        features = await client.get_consumption(device, "2026-01-01", "2026-01-02")

    # Assert: Should return empty list (no analytics fixture available).
    assert features == []


@pytest.mark.asyncio
async def test_get_consumption_specific_metric() -> None:
    """Test get_consumption with specific metric selection."""
    # Arrange: Initialize mock client with Vitocal250A.
    client = MockViClient(device_name="Vitocal250A")
    installations = await client.get_installations()
    gateways = await client.get_gateways()
    devices = await client.get_devices(installations[0].id, gateways[0].serial)
    device = devices[0]

    # Act: Fetch only DHW consumption metric.
    with pytest.warns(DeprecationWarning, match="deprecated"):
        features = await client.get_consumption(
            device, "2026-01-01", "2026-01-02", metric="dhw"
        )

    # Assert: Should return only 1 feature (dhw).
    assert len(features) == 1
    assert features[0].name == "analytics.heating.power.consumption.dhw"
    assert features[0].value == 10.2
