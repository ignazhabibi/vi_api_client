"""Command knowledge snapshot and device command list contract tests.

``tests/fixtures/command_knowledge_baseline.json`` records how the library
writes every writable feature of every bundled fixture and which commands
every API feature reports. ``scripts/update_command_knowledge_baseline.py``
regenerates it. ``scripts/add_fixture.py`` runs that script after adding or
refreshing a fixture; run it yourself after an intended parser change, and
review the diff.
"""

import importlib.util
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from builders import (
    build_device,
    bundled_feature_files,
    load_fixture_device,
    load_fixture_json,
)

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
    ParameterWriteCommand,
)

SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "update_command_knowledge_baseline.py"
)
FEATURE_FILES = bundled_feature_files()


def _load_script() -> ModuleType:
    """Import the baseline script as a module so its functions run in-process."""
    spec = importlib.util.spec_from_file_location(
        "update_command_knowledge_baseline", SCRIPT_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


baseline_script = _load_script()


@pytest.mark.parametrize("fixture_device", FEATURE_FILES, ids=FEATURE_FILES)
def test_every_fixture_matches_the_command_knowledge_baseline(fixture_device):
    """An unintended change of write_commands or reported commands shows as a diff."""
    # Arrange: Load the recorded command knowledge of every bundled fixture.
    baseline = load_fixture_json("command_knowledge_baseline.json")

    # Act: Build the command knowledge of one fixture from the current parser.
    actual = baseline_script.build_fixture_baseline(fixture_device)

    # Assert: The write commands and reported commands match the record.
    assert actual == baseline.get(fixture_device), (
        "The command knowledge changed. If that is intended, run "
        "'python scripts/update_command_knowledge_baseline.py' and review the diff."
    )


def test_baseline_covers_exactly_the_bundled_fixtures():
    """A removed fixture leaves no stale record behind."""
    baseline = load_fixture_json("command_knowledge_baseline.json")

    assert sorted(baseline) == FEATURE_FILES


def test_baseline_script_writes_the_current_command_knowledge(tmp_path, monkeypatch):
    """The script writes one sorted JSON document for every bundled fixture."""
    # Arrange: Point the script at a temporary baseline file.
    baseline_path = tmp_path / "tests" / "fixtures" / "baseline.json"
    baseline_path.parent.mkdir(parents=True)
    monkeypatch.setattr(baseline_script, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(baseline_script, "PROJECT_ROOT", tmp_path)

    # Act: Run the script.
    assert baseline_script.main() == 0

    # Assert: The written file equals the bundled record byte for byte.
    assert baseline_path.read_text(encoding="utf-8") == (
        Path(__file__).resolve().parent / "fixtures" / "command_knowledge_baseline.json"
    ).read_text(encoding="utf-8")


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
    assert replace(unchanged, _commands_by_api_feature={}) != unchanged


async def test_copied_features_stay_writable_with_the_same_command():
    """replace keeps the write command, so copies stay writable."""
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
    assert copied._write_command == feature._write_command


def test_redaction_leaves_no_identifier_in_write_commands_or_commands():
    """redact_device masks identifiers in write_commands, constraints, and commands."""
    # Arrange: A device whose every command surface carries real identifiers.
    write_command = ParameterWriteCommand(
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
        _write_command=write_command,
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
        _commands_by_api_feature={"device.serial": (command,)},
    )

    # Act: Redact the device.
    redacted = redact_device(device)

    # Assert: No identifier survives in any command surface.
    redacted_feature = redacted.get_feature("device.serial")
    assert redacted_feature is not None
    assert isinstance(redacted_feature._write_command, ParameterWriteCommand)
    assert "1234567" not in redacted_feature._write_command.uri
    assert "#" in redacted_feature._write_command.uri
    assert redacted_feature.constraints is not None
    assert redacted_feature.constraints.options == (
        "################",
        {"access_token": "<redacted>"},
    )
    (redacted_command,) = redacted._commands_by_api_feature["device.serial"]
    assert "7654321" not in redacted_command.uri
    assert "#" in redacted_command.uri
    (redacted_parameter,) = redacted_command.parameters
    assert redacted_parameter.constraints is not None
    assert redacted_parameter.constraints.options == (
        "################",
        {"access_token": "<redacted>"},
    )
    assert redacted_parameter.name == "serial"


async def test_get_command_parameters_returns_the_reported_parameters():
    # Arrange: Refresh the Vitoair fixture, whose ventilation level is set
    # through a command of an API feature without a level property.
    client = FixtureViClient("VitoairFs300E")
    (device,) = await client.get_devices("99999", "MOCK_GATEWAY_SERIAL")
    device = await client.refresh_device(device, only_available=False)

    # Act: Read the parameters of the level command.
    parameters = device.get_command_parameters(
        "ventilation.operating.modes.permanent", "setLevel"
    )

    # Assert: The parameter carries the reported name, requirement, type, and
    # allowed values unchanged.
    assert parameters is not None
    (level,) = parameters
    assert (level.name, level.required) == ("level", True)
    assert level.constraints is not None
    assert level.constraints.value_type == "string"
    assert level.constraints.options == (
        "levelOne",
        "levelTwo",
        "levelThree",
        "levelFour",
    )


async def test_get_command_parameters_distinguishes_missing_and_parameterless_commands():
    client = FixtureViClient("Vitocal250A")
    (device,) = await client.get_devices("99999", "MOCK_GATEWAY_SERIAL")
    device = await client.refresh_device(device, only_available=False)

    holiday = "heating.operating.programs.holiday"
    assert device.get_command_parameters(holiday, "unschedule") == ()
    assert device.get_command_parameters(holiday, "noSuchCommand") is None
    assert device.get_command_parameters("no.such.api.feature", "setLevel") is None
