"""Tests for boolean features written through an on/off command pair."""

from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest
from builders import build_device, load_fixture_device, load_fixture_features
from yarl import URL

from vi_api_client import FixtureViClient, redact_device
from vi_api_client.const import API_BASE_URL, ENDPOINT_FEATURES
from vi_api_client.exceptions import ViCommandRejectedError
from vi_api_client.models import (
    CommandPairWriteBinding,
    Feature,
    FeatureConstraints,
    ParameterWriteBinding,
)
from vi_api_client.parsing import api_feature_to_flat_features, parse_api_features

FIXTURE_DEVICES = FixtureViClient.get_available_fixture_devices()

_FEATURES_URL = f"{API_BASE_URL}{ENDPOINT_FEATURES}/123/gateways/GW123/devices/0"


def _command_url(api_feature_name: str, command_name: str) -> str:
    """Return the absolute command URI the constructed API features report."""
    return f"{_FEATURES_URL}/features/{api_feature_name}/commands/{command_name}"


def _command(
    api_feature_name: str,
    command_name: str,
    *,
    params: dict[str, Any] | None = None,
    is_executable: bool = True,
) -> dict[str, Any]:
    """Build one reported command of a constructed API feature."""
    return {
        "isExecutable": is_executable,
        "name": command_name,
        "params": params or {},
        "uri": _command_url(api_feature_name, command_name),
    }


def _api_feature(
    name: str,
    properties: dict[str, Any],
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build one constructed API feature with its commands."""
    return {
        "feature": name,
        "isEnabled": True,
        "isReady": True,
        "properties": properties,
        "commands": {command["name"]: command for command in commands},
    }


def _hygiene_api_feature(*, is_enabled: bool = False) -> dict[str, Any]:
    """Build the hygiene API feature of a device without `setEnabled`."""
    name = "heating.dhw.hygiene"
    return _api_feature(
        name,
        {"enabled": {"type": "boolean", "value": is_enabled}},
        [_command(name, "enable"), _command(name, "disable")],
    )


def _program_api_feature(
    program: str = "comfort",
    *,
    is_on_executable: bool = True,
    is_off_executable: bool = True,
) -> dict[str, Any]:
    """Build a heating program API feature as devices report it.

    The on command takes an optional temperature; the program has no
    parameter setter for its `active` property.
    """
    name = f"heating.circuits.0.operating.programs.{program}"
    return _api_feature(
        name,
        {
            "active": {"type": "boolean", "value": False},
            "temperature": {"type": "number", "value": 25, "unit": "celsius"},
        },
        [
            _command(
                name,
                "activate",
                params={
                    "temperature": {
                        "constraints": {"max": 37, "min": 3, "stepping": 1},
                        "required": False,
                        "type": "number",
                    }
                },
                is_executable=is_on_executable,
            ),
            _command(name, "deactivate", is_executable=is_off_executable),
        ],
    )


def _feature(api_feature: dict[str, Any], feature_name: str) -> Feature:
    """Parse one API feature and return one of its flat features."""
    return next(
        feature
        for feature in api_feature_to_flat_features(api_feature)
        if feature.name == feature_name
    )


@pytest.mark.parametrize("fixture_device", FIXTURE_DEVICES, ids=FIXTURE_DEVICES)
def test_pair_written_fixture_features_are_boolean(fixture_device):
    # Act: Parse every feature of one bundled fixture. Which features a
    # fixture writes through a pair is recorded in the command knowledge
    # baseline; this test only checks what holds for all of them.
    pair_written = [
        feature
        for feature in load_fixture_features(fixture_device)
        if isinstance(feature.write_binding, CommandPairWriteBinding)
    ]

    # Assert: Every pair-written feature is a boolean with the boolean rule.
    for feature in pair_written:
        assert isinstance(feature.value, bool)
        assert feature.constraints == FeatureConstraints(value_type="boolean")


def test_pair_binding_knows_both_commands_and_their_uris():
    # Act: Parse a hygiene API feature that offers only the command pair.
    feature = _feature(_hygiene_api_feature(), "heating.dhw.hygiene.enabled")

    # Assert: The feature is writable through both commands.
    assert feature.is_writable
    assert feature.write_binding == CommandPairWriteBinding(
        api_feature_name="heating.dhw.hygiene",
        on_command_name="enable",
        off_command_name="disable",
        on_uri=_command_url("heating.dhw.hygiene", "enable"),
        off_uri=_command_url("heating.dhw.hygiene", "disable"),
    )


def test_parameter_setter_wins_over_the_command_pair():
    # Arrange: A hygiene API feature that reports the pair and the
    # setEnabled parameter setter, as newer devices do.
    name = "heating.dhw.hygiene"
    api_feature = _api_feature(
        name,
        {"enabled": {"type": "boolean", "value": False}},
        [
            _command(name, "enable"),
            _command(name, "disable"),
            _command(
                name,
                "setEnabled",
                params={"enabled": {"type": "boolean", "required": True}},
            ),
        ],
    )

    # Act: Parse the API feature.
    feature = _feature(api_feature, "heating.dhw.hygiene.enabled")

    # Assert: The setter writes the feature.
    binding = feature.write_binding
    assert isinstance(binding, ParameterWriteBinding)
    assert binding.command_name == "setEnabled"


def test_required_parameter_excludes_the_command_pair():
    # Arrange: The on command requires a parameter the pair cannot supply.
    name = "ventilation.quickmodes.comfort"
    api_feature = _api_feature(
        name,
        {"active": {"type": "boolean", "value": False}},
        [
            _command(
                name,
                "activate",
                params={"runtime": {"type": "number", "required": True}},
            ),
            _command(name, "deactivate"),
        ],
    )

    # Act and assert: The feature stays read-only.
    assert not _feature(api_feature, f"{name}.active").is_writable


def test_optional_parameter_keeps_the_command_pair():
    # Arrange: The comfort program's on command takes an optional temperature.
    api_feature = _program_api_feature("comfort")

    # Act: Parse the API feature.
    feature = _feature(
        api_feature, "heating.circuits.0.operating.programs.comfort.active"
    )

    # Assert: The optional parameter does not exclude the pair.
    assert isinstance(feature.write_binding, CommandPairWriteBinding)


def test_missing_counterpart_excludes_the_command_pair():
    # Arrange: Hygiene reports `activate` without `deactivate` next to the
    # enable/disable pair; no table row covers its `active` property.
    name = "heating.dhw.hygiene"
    api_feature = _api_feature(
        name,
        {
            "active": {"type": "boolean", "value": False},
            "enabled": {"type": "boolean", "value": True},
        },
        [
            _command(name, "activate"),
            _command(name, "enable"),
            _command(name, "disable"),
        ],
    )

    # Act and assert: Only `enabled` is pair-written.
    assert not _feature(api_feature, "heating.dhw.hygiene.active").is_writable
    assert _feature(api_feature, "heating.dhw.hygiene.enabled").is_writable


def test_table_command_without_its_counterpart_stays_read_only():
    # Arrange: A quick mode that reports only the on command.
    name = "ventilation.quickmodes.silent"
    api_feature = _api_feature(
        name,
        {"active": {"type": "boolean", "value": False}},
        [_command(name, "activate")],
    )

    # Act and assert: Without the off command nothing is bound.
    assert not _feature(api_feature, f"{name}.active").is_writable


def test_api_feature_outside_the_table_stays_read_only():
    # Arrange: normalHeating reports an activate/deactivate pair like the
    # programs in the table.
    api_feature = _program_api_feature("normalHeating")

    # Act and assert: The table does not list the program, so it is read-only.
    feature = _feature(
        api_feature, "heating.circuits.0.operating.programs.normalHeating.active"
    )
    assert not feature.is_writable


def test_non_boolean_property_is_not_pair_written():
    # Arrange: A table property that reports text instead of a boolean.
    name = "heating.dhw.hygiene"
    api_feature = _api_feature(
        name,
        {"enabled": {"type": "string", "value": "on"}},
        [_command(name, "enable"), _command(name, "disable")],
    )

    # Act and assert: Only a reported boolean is pair-written.
    assert not _feature(api_feature, f"{name}.enabled").is_writable


@pytest.mark.parametrize(
    ("is_on_executable", "is_off_executable"),
    [
        pytest.param(False, False, id="neither-executable"),
        pytest.param(False, True, id="only-off-executable"),
        pytest.param(True, False, id="only-on-executable"),
        pytest.param(True, True, id="both-executable"),
    ],
)
def test_reported_executability_does_not_decide_pair_writability(
    is_on_executable, is_off_executable
):
    # Arrange: A pair with the given reported executability; devices report
    # all four combinations depending on their state.
    api_feature = _program_api_feature(
        "eco",
        is_on_executable=is_on_executable,
        is_off_executable=is_off_executable,
    )

    # Act and assert: The feature is pair-written whatever the flags say.
    feature = _feature(api_feature, "heating.circuits.0.operating.programs.eco.active")
    assert isinstance(feature.write_binding, CommandPairWriteBinding)


def test_one_time_charge_without_a_setter_is_pair_written():
    # Arrange: No bundled fixture reports the one-time charge without
    # setActive, so this constructed API feature is the only coverage of
    # that table row.
    name = "heating.dhw.oneTimeCharge"
    api_feature = _api_feature(
        name,
        {"active": {"type": "boolean", "value": False}},
        [_command(name, "activate"), _command(name, "deactivate")],
    )

    # Act: Parse the API feature.
    feature = _feature(api_feature, f"{name}.active")

    # Assert: The pair writes the feature.
    assert feature.write_binding == CommandPairWriteBinding(
        api_feature_name=name,
        on_command_name="activate",
        off_command_name="deactivate",
        on_uri=_command_url(name, "activate"),
        off_uri=_command_url(name, "deactivate"),
    )


@pytest.mark.parametrize("fixture_device", FIXTURE_DEVICES, ids=FIXTURE_DEVICES)
def test_snapshots_differing_only_in_pair_executability_are_equally_writable(
    fixture_device,
):
    # Arrange: A fixture payload and a copy whose pair commands report the
    # opposite executability.
    api_features = load_fixture_device(fixture_device)["data"]
    flipped_api_features = deepcopy(api_features)
    for api_feature in flipped_api_features:
        for command_name in ("activate", "deactivate", "enable", "disable"):
            command = api_feature.get("commands", {}).get(command_name)
            if command is not None:
                command["isExecutable"] = not command["isExecutable"]

    # Act: Parse both payloads.
    features, _ = parse_api_features(api_features, only_available=False)
    flipped_features, _ = parse_api_features(flipped_api_features, only_available=False)

    # Assert: Both snapshots report the same pair-written features.
    def pair_written_names(parsed: list[Feature]) -> list[str]:
        return sorted(
            feature.name
            for feature in parsed
            if isinstance(feature.write_binding, CommandPairWriteBinding)
        )

    assert pair_written_names(features) == pair_written_names(flipped_features)


@pytest.mark.parametrize(
    ("target_value", "command_name"),
    [
        pytest.param(True, "enable", id="on"),
        pytest.param(False, "disable", id="off"),
    ],
)
async def test_set_feature_sends_the_matching_pair_command(
    vi_client, mock_responses, target_value, command_name
):
    # Arrange: Refresh a device whose hygiene function reports the opposite
    # state and only the command pair.
    device = build_device("0", "123", "GW123")
    mock_responses.post(
        f"{_FEATURES_URL}/features/filter",
        payload={"data": [_hygiene_api_feature(is_enabled=not target_value)]},
    )
    command_url = _command_url("heating.dhw.hygiene", command_name)
    mock_responses.post(command_url, payload={"data": {"success": True}})
    device = await vi_client.refresh_device(device)

    # Act: Write the boolean through the public operation.
    updated_device = await vi_client.set_feature(
        device, "heating.dhw.hygiene.enabled", target_value
    )

    # Assert: Exactly the matching command was sent with an empty JSON
    # object, and the returned snapshot carries the new value.
    command_requests = [
        key for key in mock_responses.requests if "/commands/" in str(key[1])
    ]
    assert command_requests == [("POST", URL(command_url))]
    (request,) = mock_responses.requests[("POST", URL(command_url))]
    assert await request.json() == {}
    updated_feature = updated_device.get_feature("heating.dhw.hygiene.enabled")
    assert updated_feature is not None
    assert updated_feature.value is target_value
    input_feature = device.get_feature("heating.dhw.hygiene.enabled")
    assert input_feature is not None
    assert input_feature.value is (not target_value)


async def test_set_feature_does_not_send_the_optional_on_parameter(
    vi_client, mock_responses
):
    # Arrange: The comfort program's on command takes an optional temperature.
    name = "heating.circuits.0.operating.programs.comfort"
    api_feature = _api_feature(
        name,
        {
            "active": {"type": "boolean", "value": False},
            "temperature": {"type": "number", "value": 25, "unit": "celsius"},
        },
        [
            _command(
                name,
                "activate",
                params={"temperature": {"type": "number", "required": False}},
                is_executable=False,
            ),
            _command(name, "deactivate", is_executable=False),
        ],
    )
    device = build_device("0", "123", "GW123")
    mock_responses.post(
        f"{_FEATURES_URL}/features/filter", payload={"data": [api_feature]}
    )
    activate_url = _command_url(name, "activate")
    mock_responses.post(activate_url, payload={"data": {"success": True}})
    device = await vi_client.refresh_device(device)

    # Act: Switch the program on.
    await vi_client.set_feature(device, f"{name}.active", True)

    # Assert: The on command is sent without the optional temperature, even
    # though the API reported it as non-executable.
    (request,) = mock_responses.requests[("POST", URL(activate_url))]
    assert await request.json() == {}


@pytest.mark.parametrize(
    "target_value",
    [
        pytest.param("on", id="text"),
        pytest.param(1, id="number"),
        pytest.param(None, id="null"),
    ],
)
async def test_set_feature_rejects_non_boolean_pair_values_without_a_request(
    vi_client, mock_responses, target_value
):
    # Arrange: Refresh a device with a pair-written hygiene feature.
    device = build_device("0", "123", "GW123")
    mock_responses.post(
        f"{_FEATURES_URL}/features/filter",
        payload={"data": [_hygiene_api_feature()]},
    )
    device = await vi_client.refresh_device(device)
    request_count = len(mock_responses.requests)

    # Act and assert: A non-boolean value fails before any command request.
    with pytest.raises(ValueError, match="is not of type 'boolean'"):
        await vi_client.set_feature(device, "heating.dhw.hygiene.enabled", target_value)
    assert len(mock_responses.requests) == request_count


async def test_set_feature_reports_a_pair_command_the_device_rejects(
    vi_client, mock_responses
):
    # Arrange: The device rejects `disable` while the function is off.
    device = build_device("0", "123", "GW123")
    mock_responses.post(
        f"{_FEATURES_URL}/features/filter",
        payload={"data": [_hygiene_api_feature(is_enabled=False)]},
    )
    mock_responses.post(
        _command_url("heating.dhw.hygiene", "disable"),
        status=400,
        payload={
            "viErrorId": "trace-1",
            "statusCode": 400,
            "errorType": "DEVICE_COMMUNICATION_ERROR",
            "message": "Device communication error",
            "extendedPayload": {"reason": "COMMAND_NOT_EXECUTABLE"},
        },
    )
    device = await vi_client.refresh_device(device)

    # Act and assert: The direction is not checked locally; the API's
    # rejection surfaces as the command rejection.
    with pytest.raises(ViCommandRejectedError) as raised_error:
        await vi_client.set_feature(device, "heating.dhw.hygiene.enabled", False)
    assert raised_error.value.reason == "COMMAND_NOT_EXECUTABLE"


@pytest.mark.usefixtures("no_http_requests")
@pytest.mark.parametrize("fixture_device", FIXTURE_DEVICES, ids=FIXTURE_DEVICES)
async def test_fixture_client_accepts_pair_writes(fixture_device):
    # Arrange: Refresh one bundled fixture.
    client = FixtureViClient(fixture_device)
    device = (await client.get_devices("99999", "MOCK_GATEWAY_SERIAL"))[0]
    device = await client.refresh_device(device, only_available=False)

    # A fixture without a pair-written feature has nothing to write here.
    pair_written = [
        feature
        for feature in device.features
        if isinstance(feature.write_binding, CommandPairWriteBinding)
    ]
    for feature in pair_written:
        # Act: Write the opposite of the reported value.
        updated_device = await client.set_feature(device, feature, not feature.value)

        # Assert: The returned snapshot carries the written boolean.
        updated_feature = updated_device.get_feature(feature.name)
        assert updated_feature is not None
        assert updated_feature.value is (not feature.value)


def test_redaction_covers_both_uris_of_a_pair_binding():
    # Arrange: A device whose pair command URIs carry real identifiers.
    api_feature = _hygiene_api_feature()
    for command in api_feature["commands"].values():
        command["uri"] = (
            "https://api.viessmann-climatesolutions.com/iot/v2/features/"
            "installations/1234567/gateways/7630175843100101/devices/0/"
            f"features/heating.dhw.hygiene/commands/{command['name']}"
        )
    features, commands = parse_api_features([api_feature], only_available=False)
    device = replace(
        build_device("0", "1234567", "7630175843100101"),
        features=features,
        commands_by_api_feature=commands,
    )

    # Act: Redact the device.
    redacted = redact_device(device)

    # Assert: Neither URI of the pair binding keeps an identifier.
    redacted_feature = redacted.get_feature("heating.dhw.hygiene.enabled")
    assert redacted_feature is not None
    binding = redacted_feature.write_binding
    assert isinstance(binding, CommandPairWriteBinding)
    for uri in (binding.on_uri, binding.off_uri):
        assert "1234567" not in uri
        assert "7630175843100101" not in uri
        assert "#" in uri
    assert binding.on_uri.endswith("/commands/enable")
    assert binding.off_uri.endswith("/commands/disable")
