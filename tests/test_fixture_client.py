"""Public workflow tests for the fixture-backed client."""

import logging
from typing import Any

import pytest

from vi_api_client import FixtureViClient, fixture_client
from vi_api_client.exceptions import ViResponseError
from vi_api_client.models import ParameterWriteBinding
from vi_api_client.utils import format_feature

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


async def test_fixture_refresh_device_returns_hydrated_copy_without_mutating_input():
    """Fixture refresh should have the same immutable public contract as live refresh."""
    # Arrange: Discover an unhydrated fixture device.
    client = FixtureViClient("Vitodens200W")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]

    # Act: Refresh through the public client workflow.
    refreshed_device = await client.refresh_device(device)

    # Assert: The input remains unhydrated and the returned copy has features.
    assert device.features == ()
    assert refreshed_device is not device
    assert refreshed_device.features


@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_full_installation_status_reads_one_gateway_and_one_device_at_a_time(
    monkeypatch,
):
    """Full status resolves features through one feature read per device."""
    # Arrange: Count every adapter-level read of the fixture workflow.
    client = FixtureViClient("Vitodens200W")
    adapter = client._discovery_adapter
    calls: list[str] = []
    for method_name in ("get_gateways", "get_devices", "get_features"):
        bound_read: Any = getattr(adapter, method_name)

        async def counted_read(
            *args: Any,
            _read: Any = bound_read,
            _name: str = method_name,
            **kwargs: Any,
        ) -> Any:
            calls.append(_name)
            return await _read(*args, **kwargs)

        monkeypatch.setattr(adapter, method_name, counted_read)

    # Act: Scan the fixture installation with the public workflow.
    devices = await client.get_full_installation_status("99999")

    # Assert: Gateways and the device list are each read once, and every
    # device gets exactly one feature read, so the composed workflow issues
    # no additional requests compared to discovery plus per-device refreshes.
    assert calls == ["get_gateways", "get_devices", "get_features"]
    assert len(devices) == 1
    hydrated = await client.refresh_device(devices[0])
    assert hydrated.features == devices[0].features


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
    client = FixtureViClient("Vitocal250A")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]

    # Act: Request features by one name, including unavailable ones.
    features = await client.get_features(
        device, only_available=False, feature_names=[requested_name]
    )

    assert sorted(feature.name for feature in features) == expected_names


async def test_fixture_feature_filters_apply_shared_enabled_and_name_semantics():
    """Fixture filtering should retain only requested enabled and ready features."""
    # Arrange: Select a fixture that includes disabled features.
    client = FixtureViClient("Vitocal200S")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]

    # Act: Request enabled and ready features by explicit names.
    features = await client.get_features(
        device,
        only_available=True,
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
async def test_fixture_run_command_is_offline_and_stateless(capsys, caplog):
    """Fixture command runs should not alter the loaded fixture features."""
    # Arrange: Load a writable fixture feature through the public workflow.
    caplog.set_level(logging.DEBUG, logger="vi_api_client.fixture_client")
    client = FixtureViClient("Vitocal250A")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    device = await client.refresh_device(device)

    # Act: Run a command with an explicit payload; a snapshot refresh keeps
    # the feature so the run cannot depend on a feature reference.
    await client.run_command(
        device,
        "heating.circuits.0.heating.curve",
        "setCurve",
        {"slope": 0.7, "shift": 7.0},
    )
    fresh_device = await client.refresh_device(device)

    # Assert: A fresh read still shows the fixture value, so the command did
    # not change the fixture state.
    refreshed_feature = fresh_device.get_feature(
        "heating.circuits.0.heating.curve.slope"
    )
    assert refreshed_feature is not None
    assert refreshed_feature.value == 0.6
    assert capsys.readouterr().out == ""
    assert "Executing fixture command at" in caplog.text
    assert "commands/setCurve" in caplog.text


@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_set_feature_accepts_every_valid_write():
    """Fixture writes accept a value and update no fixture data."""
    # Arrange: Load a fixture device with a writable feature.
    client = FixtureViClient("Vitocal250A")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    device = await client.refresh_device(device)

    # Act: Write through the public high-level operation.
    updated_device = await client.set_feature(
        device, "heating.circuits.0.heating.curve.slope", 0.9
    )

    # Assert: The write returns the locally updated snapshot while the
    # fixture state stays untouched.
    updated_feature = updated_device.get_feature(
        "heating.circuits.0.heating.curve.slope"
    )
    assert updated_feature is not None
    assert updated_feature.value == 0.9
    fresh_feature = (await client.refresh_device(device)).get_feature(
        "heating.circuits.0.heating.curve.slope"
    )
    assert fresh_feature is not None
    assert fresh_feature.value == 0.6


@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_comfort_temperature_is_written_with_set_temperature():
    """Comfort temperature should use setTemperature, not the program switch."""
    # Arrange: Load a fixture that also offers activate(temperature).
    client = FixtureViClient("Vitocal200S")
    (device,) = await client.get_devices("99999", "MOCK_GATEWAY_SERIAL")
    device = await client.refresh_device(device)

    # Act: Read the comfort temperature feature.
    feature = device.get_feature(
        "heating.circuits.0.operating.programs.comfort.temperature"
    )

    # Assert: The write targets setTemperature and its targetTemperature.
    assert feature is not None
    assert isinstance(feature.write_binding, ParameterWriteBinding)
    assert feature.write_binding.command_name == "setTemperature"
    assert feature.write_binding.parameter_name == "targetTemperature"


async def test_fixture_schedule_value_is_the_plan_and_renders_compactly():
    """A read schedule should be the plan itself and format per day."""
    # Arrange: Load a fixture device with a heating circuit schedule.
    client = FixtureViClient("Vitocal250A")
    (device,) = await client.get_devices("99999", "MOCK_GATEWAY_SERIAL")
    device = await client.refresh_device(device)

    # Act: Read the schedule and its status flag.
    schedule = device.get_feature("heating.circuits.0.heating.schedule")
    active = device.get_feature("heating.circuits.0.heating.schedule.active")

    # Assert: The value holds days, the status is read-only, and it formats.
    assert schedule is not None
    assert isinstance(schedule.value, dict)
    assert "mon" in schedule.value
    assert active is not None
    assert isinstance(active.value, bool)
    assert active.is_writable is False
    assert format_feature(schedule).startswith("Mo[")
