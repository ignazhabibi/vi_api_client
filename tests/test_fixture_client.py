"""Public workflow tests for the fixture-backed client."""

import logging

import pytest

from vi_api_client import FixtureViClient
from vi_api_client.exceptions import ViResponseError
from vi_api_client.fixture_client import _FixtureDiscoveryAdapter


def test_fixture_client_rejects_obsolete_authentication_argument():
    """Fixture clients should accept only the selected fixture device name."""
    # Act and assert: Fixture clients must not accept unused authentication state.
    with pytest.raises(TypeError, match="auth"):
        FixtureViClient("Vitodens200W", auth=None)  # pyright: ignore[reportCallIssue]


@pytest.mark.asyncio
async def test_fixture_device_hydration_uses_deterministic_fixture_topology():
    """Fixture device discovery should use the selected fixture in one topology."""
    # Arrange: Select a known fixture device.
    client = FixtureViClient("Vitodens200W")

    # Act: Discover and hydrate through the public client workflow.
    devices = await client.get_devices(
        "99999", "MOCK_GATEWAY_SERIAL", include_features=True
    )

    # Assert: The public fixture client represents exactly its selected fixture device.
    assert len(devices) == 1
    assert devices[0].id == "0"
    assert devices[0].model_id == "Vitodens200W"
    assert devices[0].features


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fixture_name", "model_id", "device_type"),
    [
        ("Vitocal200S", "Vitocal200S", "heating"),
        ("Vitocal222S", "Vitocal222S", "heating"),
        ("Vitocal250A", "Vitocal250A", "heating"),
        (
            "Vitocal333G-with-Vitovent300F",
            "Vitocal333G-with-Vitovent300F",
            "heating",
        ),
        ("Vitocharge03", "Vitocharge03", "battery"),
        ("Vitodens200W", "Vitodens200W", "heating"),
        ("Vitopure350", "Vitopure350", "ventilation"),
    ],
)
async def test_fixture_discovery_uses_each_fixture_metadata_definition(
    fixture_name, model_id, device_type
):
    """Fixture discovery should expose each catalogued model and device type."""
    # Arrange: Select one bundled fixture from the public fixture catalog.
    client = FixtureViClient(fixture_name)

    # Act: Discover the fixture through the public client workflow.
    devices = await client.get_devices("99999", "MOCK_GATEWAY_SERIAL")

    # Assert: The device identity comes from the fixture metadata definition.
    assert [(device.model_id, device.device_type) for device in devices] == [
        (model_id, device_type)
    ]


def test_fixture_device_catalog_lists_each_fixture_metadata_definition():
    """Fixture enumeration should use the same catalog as fixture discovery."""
    assert FixtureViClient.get_available_fixture_devices() == [
        "Vitocal200S",
        "Vitocal222S",
        "Vitocal250A",
        "Vitocal333G-with-Vitovent300F",
        "Vitocharge03",
        "Vitodens200W",
        "Vitopure350",
    ]


@pytest.mark.asyncio
async def test_fixture_update_device_returns_hydrated_copy_without_mutating_input():
    """Fixture refresh should have the same immutable public contract as live refresh."""
    # Arrange: Discover an unhydrated fixture device.
    client = FixtureViClient("Vitodens200W")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]

    # Act: Refresh through the public client workflow.
    refreshed_device = await client.update_device(device)

    # Assert: The input remains unhydrated and the returned copy has features.
    assert device.features == ()
    assert refreshed_device is not device
    assert refreshed_device.features


@pytest.mark.asyncio
async def test_fixture_gateway_refresh_uses_the_fixture_adapter_without_authentication():
    """Fixture gateway refresh should reuse the client workflow without credentials."""
    # Arrange: Discover the deterministic fixture device through an offline client.
    client = FixtureViClient("Vitodens200W")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]

    # Act: Refresh the discovered device through the inherited gateway operation.
    result = await client.update_gateway_devices([device])

    # Assert: The fixture refresh is complete and preserves the source device metadata.
    assert result.is_complete
    assert result.updated_devices[0].id == device.id
    assert result.updated_devices[0].model_id == device.model_id
    assert result.updated_devices[0].features


@pytest.mark.asyncio
async def test_fixture_feature_filters_apply_shared_enabled_and_name_semantics():
    """Fixture filtering should retain only requested enabled and ready features."""
    # Arrange: Select a fixture that includes disabled features.
    client = FixtureViClient("Vitocal200S")
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
async def test_fixture_client_rejects_malformed_fixture_feature_envelopes():
    """Fixture feature responses should use the same envelope validation as live ones."""
    # Arrange: Replace the cached fixture response with an invalid collection entry.
    client = FixtureViClient("Vitodens200W")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    fixture_adapter = client._discovery_adapter
    assert isinstance(fixture_adapter, _FixtureDiscoveryAdapter)
    fixture_adapter._feature_data = {"data": [None]}

    # Act and assert: The inherited public feature read exposes ViResponseError.
    with pytest.raises(ViResponseError, match="entries must be objects"):
        await client.get_features(device)


@pytest.mark.asyncio
async def test_fixture_execute_command_is_offline_and_stateless(capsys, caplog):
    """Fixture command execution should not alter the loaded fixture features."""
    # Arrange: Load a writable fixture feature through the public workflow.
    caplog.set_level(logging.DEBUG, logger="vi_api_client.fixture_client")
    client = FixtureViClient("Vitocal250A")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    device = await client.update_device(device)
    feature = device.get_feature("heating.circuits.0.heating.curve.slope")
    assert feature is not None

    # Act: Execute a command with an explicit payload.
    response = await client.execute_command(feature, {"slope": 0.7, "shift": 7.0})

    # Assert: The response is deterministic and the fixture-derived value is unchanged.
    assert response.success
    assert response.reason == "Fixture Execution Success"
    assert feature.value == 0.6
    assert capsys.readouterr().out == ""
    assert "Executing fixture command 'setCurve'" in caplog.text
