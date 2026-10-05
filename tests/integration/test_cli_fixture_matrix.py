"""Run every CLI command against every bundled fixture device."""

import json
import logging

import pytest

from vi_api_client import FixtureViClient
from vi_api_client.cli import async_main

FIXTURE_DEVICES = FixtureViClient.get_available_fixture_devices()

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_http_requests")]


@pytest.fixture
def run_cli(monkeypatch, capsys, caplog):
    """Run one CLI invocation in-process and return its status and outputs."""
    caplog.set_level(logging.INFO)

    async def _run(*arguments: str) -> tuple[int, str, str]:
        capsys.readouterr()
        caplog.clear()
        monkeypatch.setattr("sys.argv", ["vi-client", *arguments])
        exit_status = await async_main()
        captured = capsys.readouterr()
        return exit_status, captured.out, captured.err + caplog.text

    return _run


async def _features_of(fixture_device: str):
    """Return every feature of a fixture device through the public client."""
    client = FixtureViClient(fixture_device)
    device = (await client.get_devices("99999", "MOCK_GATEWAY"))[0]
    return await client.get_features(device)


@pytest.mark.parametrize("fixture_device", FIXTURE_DEVICES)
@pytest.mark.parametrize(
    "command",
    [
        ["list-devices"],
        ["list-features"],
        ["list-features", "--enabled"],
        ["list-features", "--values"],
        ["list-writable"],
        ["list-events", "--days", "7"],
    ],
    ids=" ".join,
)
@pytest.mark.asyncio
async def test_text_commands_succeed_for_every_fixture(
    run_cli, fixture_device: str, command: list[str]
):
    # Act: Run the text command against the fixture device.
    exit_status, out, _ = await run_cli(*command, "--fixture-device", fixture_device)

    # Assert: The command succeeds and prints a result.
    assert exit_status == 0
    assert out.strip()


@pytest.mark.parametrize("fixture_device", FIXTURE_DEVICES)
@pytest.mark.parametrize(
    "command",
    [
        ["list-features", "--json"],
        ["list-features", "--values", "--json"],
        ["list-events", "--days", "7", "--json"],
    ],
    ids=" ".join,
)
@pytest.mark.asyncio
async def test_json_commands_print_exactly_one_document_for_every_fixture(
    run_cli, fixture_device: str, command: list[str]
):
    # Act: Run the JSON command against the fixture device.
    exit_status, out, err = await run_cli(*command, "--fixture-device", fixture_device)

    # Assert: stdout is one JSON document and diagnostics stay on stderr.
    assert exit_status == 0
    json.loads(out)
    assert f"Using Fixture Device: {fixture_device}" in err


@pytest.mark.parametrize("fixture_device", FIXTURE_DEVICES)
@pytest.mark.asyncio
async def test_get_feature_json_serializes_writable_and_structured_features(
    run_cli, fixture_device: str
):
    """Command metadata and structured values must serialize for every fixture."""
    # Arrange: Select the features whose JSON output is not a plain scalar.
    features = [
        feature
        for feature in await _features_of(fixture_device)
        if feature.is_writable or isinstance(feature.value, (dict, list))
    ]
    assert features

    for feature in features:
        # Act: Read the feature as JSON through the CLI.
        exit_status, out, _ = await run_cli(
            "get-feature", feature.name, "--json", "--fixture-device", fixture_device
        )

        # Assert: The document for this feature carries its value and control.
        assert exit_status == 0, feature.name
        documents = json.loads(out)
        documents = documents if isinstance(documents, list) else [documents]
        document = next(doc for doc in documents if doc["name"] == feature.name)
        assert document["value"] == feature.value
        assert (document["control"] is not None) == feature.is_writable


def _cli_text(value: object) -> str:
    """Return the command line text that sets a feature to the given value."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


@pytest.mark.parametrize("fixture_device", FIXTURE_DEVICES)
@pytest.mark.asyncio
async def test_set_accepts_the_current_value_of_every_writable_feature(
    run_cli, fixture_device: str
):
    """Writing a feature's own value succeeds unless the fixture value is empty."""
    # Arrange: Collect every writable feature with its current value.
    writable_features = [
        feature for feature in await _features_of(fixture_device) if feature.is_writable
    ]

    for feature in writable_features:
        # Act: Set the feature to the value it already has.
        exit_status, out, err = await run_cli(
            "set",
            feature.name,
            _cli_text(feature.value),
            "--fixture-device",
            fixture_device,
        )

        # Assert: Empty fixture values violate their own constraints; all
        # other current values are accepted.
        if feature.value == "":
            assert exit_status == 1, feature.name
            assert "Error setting feature:" in err, feature.name
        else:
            assert exit_status == 0, (feature.name, err)
            assert "Success!" in out, feature.name


_CURVE_SLOPE = "heating.circuits.0.heating.curve.slope"


@pytest.mark.asyncio
async def test_exec_succeeds_with_every_required_parameter(run_cli):
    # Act: Execute the two-parameter curve command with both parameters.
    exit_status, out, _ = await run_cli(
        "exec",
        _CURVE_SLOPE,
        "setCurve",
        json.dumps({"slope": 0.6, "shift": 4}),
        "--fixture-device",
        "Vitocal250A",
    )

    # Assert: The fixture command succeeds.
    assert exit_status == 0
    assert "Success!" in out


@pytest.mark.asyncio
async def test_exec_names_a_missing_required_parameter(run_cli):
    # Act: Execute the curve command without its 'shift' parameter.
    exit_status, _, err = await run_cli(
        "exec",
        _CURVE_SLOPE,
        "setCurve",
        json.dumps({"slope": 0.6}),
        "--fixture-device",
        "Vitocal250A",
    )

    # Assert: exec does not fill parameters and names the missing one.
    assert exit_status == 1
    assert "missing required parameter 'shift'" in err
