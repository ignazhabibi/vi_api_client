"""Public workflow tests for the fixture-backed client."""

import logging

import pytest

from vi_api_client import FixtureViClient, fixture_client
from vi_api_client.exceptions import ViResponseError

# The bundled fixture devices and the device types their catalog entries declare.
EXPECTED_DEVICE_TYPES = {
    "Vitocal200S": "heating",
    "Vitocal222S": "heating",
    "Vitocal250A": "heating",
    "Vitocal333G-with-Vitovent300F": "heating",
    "Vitocharge03": "battery",
    "Vitodens200W": "heating",
    "Vitopure350": "ventilation",
}


def test_fixture_client_rejects_unknown_device_with_available_names():
    """Unknown fixture names should explain which fixture devices exist."""
    # Act and assert: The error names the unknown device and the catalog entries.
    with pytest.raises(
        ValueError, match=r"Unknown fixture device 'Nope'\. Available: .*Vitodens200W"
    ):
        FixtureViClient("Nope")


@pytest.mark.parametrize(
    ("fixture_name", "device_type"),
    EXPECTED_DEVICE_TYPES.items(),
    ids=list(EXPECTED_DEVICE_TYPES),
)
async def test_fixture_discovery_uses_each_fixture_metadata_definition(
    fixture_name, device_type
):
    """Fixture discovery should expose each catalogued model as one device."""
    # Arrange: Select one bundled fixture from the public fixture catalog.
    client = FixtureViClient(fixture_name)

    # Act: Discover the fixture through the public client workflow.
    devices = await client.get_devices("99999", "MOCK_GATEWAY_SERIAL")

    # Assert: One device carries the identity from the fixture metadata definition.
    assert [(device.id, device.model_id, device.device_type) for device in devices] == [
        ("0", fixture_name, device_type)
    ]


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


@pytest.mark.parametrize(
    ("requested_name", "expected_names"),
    [
        pytest.param(
            "heating.circuits.0.heating.curve",
            [
                "heating.circuits.0.heating.curve.shift",
                "heating.circuits.0.heating.curve.slope",
            ],
            id="api-feature-name",
        ),
        pytest.param(
            "heating.circuits.0.heating.curve.slope",
            ["heating.circuits.0.heating.curve.slope"],
            id="feature-name",
        ),
        pytest.param(
            "heating.circuits.0.name",
            ["heating.circuits.0.name", "heating.circuits.0.name.name"],
            id="feature-and-api-feature-name",
        ),
    ],
)
async def test_fixture_feature_names_match_feature_and_api_feature_names(
    requested_name, expected_names
):
    """Fixture filtering should select the same features as the live client."""
    # Arrange: Discover a fixture device with multi-property API features.
    client = FixtureViClient("Vitocal250A")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]

    # Act: Request features by one name.
    features = await client.get_features(device, feature_names=[requested_name])

    # Assert: Feature names and API feature names both select features.
    assert sorted(feature.name for feature in features) == expected_names


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


async def test_fixture_client_rejects_malformed_fixture_feature_responses(
    monkeypatch,
):
    """Fixture feature responses should use the same response validation as live ones."""
    # Arrange: Serve the fixture feature response with an invalid collection entry.
    client = FixtureViClient("Vitodens200W")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    monkeypatch.setattr(
        fixture_client, "_read_fixture_file", lambda file_name: {"data": [None]}
    )

    # Act and assert: The inherited public feature read exposes ViResponseError.
    with pytest.raises(ViResponseError, match="entries must be objects"):
        await client.get_features(device)


@pytest.mark.usefixtures("no_http_requests")
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

    # Assert: The response is deterministic and a fresh read still shows the
    # fixture value, so the command did not change the fixture state.
    assert response.success
    assert response.reason == "Fixture Execution Success"
    refreshed_feature = (await client.update_device(device)).get_feature(
        "heating.circuits.0.heating.curve.slope"
    )
    assert refreshed_feature is not None
    assert refreshed_feature.value == 0.6
    assert capsys.readouterr().out == ""
    assert "Executing fixture command 'setCurve'" in caplog.text
