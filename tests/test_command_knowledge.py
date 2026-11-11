"""Command knowledge regression and snapshot contract tests.

The baseline in ``tests/fixtures/command_knowledge_baseline.json`` was
generated from the implementation before the FeatureConstraints rework, so
these tests pin the writable sets, their bindings, and the reported commands
of every bundled fixture.
"""

from copy import deepcopy
from dataclasses import replace

import pytest
from builders import build_device, load_fixture_device, load_fixture_json

from vi_api_client import (
    Feature,
    FeatureConstraints,
    FixtureViClient,
    fixture_client,
    redact_device,
)
from vi_api_client.const import API_BASE_URL, ENDPOINT_FEATURES
from vi_api_client.models import (
    CommandParameter,
    Device,
    DeviceCommand,
    ParameterWriteBinding,
)

BASELINE = load_fixture_json("command_knowledge_baseline.json")


@pytest.mark.parametrize("fixture_device", sorted(BASELINE), ids=sorted(BASELINE))
async def test_every_fixture_keeps_its_writable_set_and_write_bindings(fixture_device):
    """The writable features and their commands stay exactly as before."""
    # Arrange: Refresh one bundled fixture without availability filtering.
    client = FixtureViClient(fixture_device)
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    refreshed = await client.refresh_device(device, only_available=False)

    # Act: Collect every writable feature's binding.
    actual = {
        feature.name: {
            "command": binding.command_name,
            "parameter": binding.parameter_name,
            "uri": binding.uri,
            "required": list(binding.required_parameters),
            "api_feature": binding.api_feature_name,
        }
        for feature in refreshed.features
        if (binding := feature.write_binding) is not None
    }

    # Assert: The writable set, commands, parameters, and URIs are unchanged.
    assert actual == BASELINE[fixture_device]["write_bindings"]


@pytest.mark.parametrize("fixture_device", sorted(BASELINE), ids=sorted(BASELINE))
async def test_device_lists_every_reported_command_after_a_full_refresh(
    fixture_device,
):
    """A complete refresh carries every reported command per API feature."""
    # Arrange: Refresh one bundled fixture without availability filtering.
    client = FixtureViClient(fixture_device)
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    refreshed = await client.refresh_device(device, only_available=False)

    # Act: Read the reported command names of every API feature.
    baseline_commands = BASELINE[fixture_device]["api_feature_commands"]
    actual_commands = {
        api_feature_name: list(refreshed.get_command_names(api_feature_name))
        for api_feature_name in baseline_commands
    }

    # Assert: Every reported command, and only those, is listed per feature.
    assert actual_commands == baseline_commands


async def test_get_command_names_returns_the_reported_command_sets():
    """get_command_names lists commands of featureless API features too."""
    # Arrange: Refresh the fixtures named by the issue.
    client = FixtureViClient("Vitocal250A")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    holiday = await client.refresh_device(device, only_available=False)

    charge_client = FixtureViClient("Vitocharge03")
    charge_device = (await charge_client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    charge = await charge_client.refresh_device(charge_device, only_available=False)

    # Act and assert: The documented command sets are exposed.
    assert holiday.get_command_names("heating.operating.programs.holiday") == (
        "changeEndDate",
        "schedule",
        "unschedule",
    )
    assert charge.get_command_names("heating.device.time") == ("setTime",)
    assert holiday.get_command_names("no.such.api.feature") == ()


async def test_command_lists_survive_every_refresh_path():
    """Each snapshot-building path carries the device command list."""
    # Arrange: One fixture device and its raw hand-built counterpart.
    client = FixtureViClient("Vitodens200W")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    hand_built = Device(
        id="0",
        gateway_serial="MOCK_GW",
        installation_id="99999",
        model_id="Vitodens200W",
        device_type="heating",
        status="connected",
    )

    # Act: Load features through the bulk and composed refresh paths.
    bulk = await client.refresh_gateway_devices([hand_built])
    fallback = await client._refresh_devices_individually([hand_built])
    full = await client.get_full_installation_status("99999")

    # Assert: Each refresh path exposes the same command list.
    expected = (
        await client.refresh_device(device, only_available=False)
    ).get_command_names("heating.circuits.0.heating.curve")
    assert expected
    for refreshed in (
        *bulk.refreshed_devices,
        *fallback.refreshed_devices,
        *full,
    ):
        assert refreshed.get_command_names("heating.circuits.0.heating.curve") == (
            expected
        )


async def test_only_available_reads_keep_the_commands_of_available_api_features(
    vi_client, mock_responses
):
    """only_available reads carry available API features' commands only."""
    # Arrange: One disabled API feature with a command, beside an available one.
    # No bundled fixture reports commands on an unavailable API feature, so
    # the read is served from a synthetic response.
    payload = {
        "data": [
            {
                "feature": "heating.mode",
                "isEnabled": False,
                "properties": {"mode": {"value": "auto"}},
                "commands": {
                    "setMode": {
                        "uri": "/commands/setMode",
                        "params": {"mode": {"type": "string", "required": True}},
                    }
                },
            },
            {
                "feature": "heating.level",
                "properties": {"level": {"value": 1}},
                "commands": {
                    "setLevel": {
                        "uri": "/commands/setLevel",
                        "params": {"level": {"type": "number"}},
                    }
                },
            },
        ]
    }
    device = build_device("0")
    url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/{device.installation_id}/gateways/"
        f"{device.gateway_serial}/devices/{device.id}/features/filter"
    )
    mock_responses.post(url, payload=payload, repeat=True)

    # Act: Read once with each availability mode.
    available = await vi_client.refresh_device(device, only_available=True)
    unfiltered = await vi_client.refresh_device(device, only_available=False)

    # Assert: The disabled API feature lists its command only without the
    # availability filter; the available one lists it in both reads.
    assert available.get_command_names("heating.mode") == ()
    assert unfiltered.get_command_names("heating.mode") == ("setMode",)
    assert available.get_command_names("heating.level") == ("setLevel",)
    assert unfiltered.get_command_names("heating.level") == ("setLevel",)


@pytest.mark.usefixtures("no_http_requests")
async def test_is_executable_only_differences_keep_snapshots_equal(monkeypatch):
    """An unbound command's isExecutable flag is not part of the snapshot."""
    # Arrange: Serve the Vitocal250A fixture responses; the fixture adapter
    # reads the file on every request, so later mutations take effect.
    raw = deepcopy(load_fixture_device("Vitocal250A"))
    original_read = fixture_client._read_fixture_file
    monkeypatch.setattr(
        fixture_client,
        "_read_fixture_file",
        (
            lambda file_name: (
                raw if file_name == "Vitocal250A.json" else original_read(file_name)
            )
        ),
    )
    client = FixtureViClient("Vitocal250A")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]

    # Act: Build one snapshot from the plain fixture, one with the holiday
    # command's isExecutable flag flipped, which the parser binds to no
    # feature.
    unchanged = await client.refresh_device(device, only_available=False)
    holiday = next(
        entry
        for entry in raw["data"]
        if entry["feature"] == "heating.operating.programs.holiday"
    )
    holiday["commands"]["changeEndDate"]["isExecutable"] = False
    changed = await client.refresh_device(device, only_available=False)

    # Assert: The flag the library does not model never changes equality,
    # and both snapshots list the same commands.
    assert changed == unchanged
    assert changed.get_command_names("heating.operating.programs.holiday") == (
        "changeEndDate",
        "schedule",
        "unschedule",
    )

    # The command list itself is part of the snapshot, so a stale snapshot
    # without it no longer compares equal.
    assert replace(unchanged, commands_by_api_feature={}) != unchanged


async def test_copied_features_stay_writable_with_the_same_command():
    """replace keeps the write binding, so copies stay writable."""
    # Arrange: One fixture-parsed writable feature.
    client = FixtureViClient("Vitodens200W")
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    refreshed = await client.refresh_device(device, only_available=False)
    feature = refreshed.get_feature("heating.circuits.0.heating.curve.slope")
    assert feature is not None
    assert feature.is_writable

    # Act: Copy the feature with a new value, like the local update does.
    copied = replace(feature, value=2.0)

    # Assert: The copy stays writable through the same command.
    assert copied.is_writable
    assert copied.write_binding == feature.write_binding


def test_redaction_leaves_no_identifier_in_bindings_or_commands():
    """redact_device masks identifiers in bindings, constraints, and commands."""
    # Arrange: A device whose every command surface carries real identifiers.
    binding = ParameterWriteBinding(
        api_feature_name="device.serial",
        command_name="setSerial",
        parameter_name="serial",
        required_parameters=["serial"],
        uri="/iot/v2/features/installations/1234567/gateways/"
        "7630175843100101/devices/7654321/features/device.serial/commands/setSerial",
    )
    feature = Feature(
        name="device.serial",
        value="7630175843100101",
        unit=None,
        is_enabled=True,
        is_ready=True,
        constraints=FeatureConstraints(
            options=["7630175843100101", {"access_token": "secret"}]
        ),
        write_binding=binding,
    )
    command = DeviceCommand(
        name="setSerial",
        uri="/iot/v2/features/installations/1234567/gateways/"
        "7630175843100101/devices/7654321/features/device.serial/commands/setSerial",
        parameters=(
            CommandParameter(
                name="serial",
                required=True,
                constraints=FeatureConstraints(
                    options=["7630175843100101", {"access_token": "secret"}]
                ),
            ),
        ),
    )
    device = Device(
        id="7654321",
        gateway_serial="7630175843100101",
        installation_id="1234567",
        model_id="Vitocal",
        device_type="heating",
        status="connected",
        features=[feature],
        commands_by_api_feature={"device.serial": (command,)},
    )

    # Act: Redact the device.
    redacted = redact_device(device)

    # Assert: No identifier survives in any command surface.
    redacted_feature = redacted.get_feature("device.serial")
    assert redacted_feature is not None
    assert redacted_feature.write_binding is not None
    assert "1234567" not in redacted_feature.write_binding.uri
    assert "#" in redacted_feature.write_binding.uri
    assert redacted_feature.constraints is not None
    assert redacted_feature.constraints.options == (
        "################",
        {"access_token": "<redacted>"},
    )
    (redacted_command,) = redacted.commands_by_api_feature["device.serial"]
    assert "7654321" not in redacted_command.uri
    assert "#" in redacted_command.uri
    (redacted_parameter,) = redacted_command.parameters
    assert redacted_parameter.constraints is not None
    assert redacted_parameter.constraints.options == (
        "################",
        {"access_token": "<redacted>"},
    )
    assert redacted_parameter.name == "serial"
