"""Run every CLI command against every bundled fixture device."""

import asyncio
import json
import logging
from collections.abc import Callable

import pytest

from vi_api_client import FixtureViClient
from vi_api_client.cli import async_main
from vi_api_client.models import Feature

FIXTURE_DEVICES = FixtureViClient.get_available_fixture_devices()

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("no_http_requests")]

_for_every_fixture_device = pytest.mark.parametrize(
    "fixture_device", FIXTURE_DEVICES, ids=FIXTURE_DEVICES
)


async def _features_of(fixture_device: str) -> list[Feature]:
    """Return every feature of a fixture device through the public client."""
    client = FixtureViClient(fixture_device)
    device = (await client.get_devices("99999", "MOCK_GATEWAY"))[0]
    return await client.get_features(device)


async def _writable_features() -> list[tuple[str, Feature]]:
    """Return every writable feature of every fixture device."""
    return [
        (fixture_device, feature)
        for fixture_device in FIXTURE_DEVICES
        for feature in await _features_of(fixture_device)
        if feature.is_writable
    ]


# Parametrization needs the features at collection time, before any test
# event loop exists, so the bundled fixtures are read once synchronously.
_WRITABLE_FEATURES = asyncio.run(_writable_features())


def _writable_feature_cases(*, empty_value: bool) -> list:
    """Return one parameter set per writable feature with or without a value."""
    return [
        pytest.param(fixture_device, feature, id=f"{fixture_device}:{feature.name}")
        for fixture_device, feature in _WRITABLE_FEATURES
        if (feature.value == "") is empty_value
    ]


@pytest.fixture
def run_cli(monkeypatch, capsys, caplog):
    """Run one CLI invocation in-process and return its status and outputs."""
    caplog.set_level(logging.INFO)
    # async_main configures root logging for real processes; in-process runs
    # must not leave handlers behind for later tests.
    monkeypatch.setattr(logging, "basicConfig", lambda **_: None)

    async def _run(*arguments: str) -> tuple[int, str, str]:
        capsys.readouterr()
        caplog.clear()
        monkeypatch.setattr("sys.argv", ["vi-client", *arguments])
        exit_status = await async_main()
        captured = capsys.readouterr()
        return exit_status, captured.out, captured.err + caplog.text

    return _run


@_for_every_fixture_device
@pytest.mark.parametrize(
    ("command", "result_marker"),
    [
        pytest.param(["list-devices"], "Found 1 installations:", id="list-devices"),
        pytest.param(["list-features"], "Features for device 0:", id="list-features"),
        pytest.param(
            ["list-features", "--enabled"],
            "Features for device 0:",
            id="list-features --enabled",
        ),
        pytest.param(
            ["list-features", "--values"],
            "(* = writable)",
            id="list-features --values",
        ),
        pytest.param(["list-writable"], "writable features:", id="list-writable"),
        pytest.param(
            ["list-events", "--days", "7"],
            "event(s) for installation 99999 (last 7 days):",
            id="list-events --days 7",
        ),
    ],
)
@pytest.mark.asyncio
async def test_text_commands_print_their_result_for_every_fixture(
    run_cli, fixture_device: str, command: list[str], result_marker: str
):
    """Every text command prints its own result, not only the setup diagnostic."""
    # Act: Run the text command against the fixture device.
    exit_status, out, _ = await run_cli(*command, "--fixture-device", fixture_device)

    # Assert: The command succeeds and prints its command-specific result.
    assert exit_status == 0
    assert result_marker in out


def _element_types(document: list) -> set[str]:
    """Return the type names of a JSON array's elements."""
    return {type(element).__name__ for element in document}


def _element_keys(document: list[dict]) -> set[tuple[str, ...]]:
    """Return the distinct sorted key sets of a JSON array of objects."""
    return {tuple(sorted(element)) for element in document}


def _document_keys(document: dict) -> tuple[str, ...]:
    """Return the sorted top-level keys of a JSON object."""
    return tuple(sorted(document))


@_for_every_fixture_device
@pytest.mark.parametrize(
    ("command", "shape", "expected_shape"),
    [
        pytest.param(
            ["list-features", "--json"],
            _element_types,
            {"str"},
            id="list-features --json",
        ),
        pytest.param(
            ["list-features", "--values", "--json"],
            _element_keys,
            {("formatted", "name", "unit", "value", "writable")},
            id="list-features --values --json",
        ),
        pytest.param(
            ["list-events", "--days", "7", "--json"],
            _document_keys,
            (
                "earliestEventTimestamp",
                "eventCount",
                "events",
                "installationId",
                "latestEventTimestamp",
                "nextCursor",
                "pagesFetched",
                "paginationComplete",
            ),
            id="list-events --days 7 --json",
        ),
    ],
)
@pytest.mark.asyncio
async def test_json_commands_print_exactly_one_document_for_every_fixture(
    run_cli,
    fixture_device: str,
    command: list[str],
    shape: Callable[[object], object],
    expected_shape: object,
):
    """JSON commands print one document of their shape and keep stdout clean."""
    # Act: Run the JSON command against the fixture device.
    exit_status, out, err = await run_cli(*command, "--fixture-device", fixture_device)

    # Assert: stdout is one JSON document of the command's shape and
    # diagnostics stay on stderr.
    assert exit_status == 0
    assert shape(json.loads(out)) == expected_shape
    assert f"Using Fixture Device: {fixture_device}" in err


@_for_every_fixture_device
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
        document = next((doc for doc in documents if doc["name"] == feature.name), None)
        assert document is not None, feature.name
        assert document["value"] == feature.value
        assert (document["control"] is not None) == feature.is_writable


def _cli_text(value: object) -> str:
    """Return the command line text that sets a feature to the given value."""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


@pytest.mark.parametrize(
    ("fixture_device", "feature"), _writable_feature_cases(empty_value=False)
)
@pytest.mark.asyncio
async def test_set_accepts_the_current_value_of_a_writable_feature(
    run_cli, fixture_device: str, feature: Feature
):
    """Writing a feature's own non-empty value passes every client constraint."""
    # Act: Set the feature to the value it already has.
    exit_status, out, err = await run_cli(
        "set",
        feature.name,
        _cli_text(feature.value),
        "--fixture-device",
        fixture_device,
    )

    # Assert: The fixture value satisfies its own command constraints.
    assert exit_status == 0, err
    assert "Success!" in out


@pytest.mark.parametrize(
    ("fixture_device", "feature"), _writable_feature_cases(empty_value=True)
)
@pytest.mark.asyncio
async def test_set_rejects_an_empty_current_value_by_its_constraint(
    run_cli, fixture_device: str, feature: Feature
):
    """Empty fixture values violate their length or pattern constraint."""
    # Arrange: Name the constraint the empty value violates; fixtures pair
    # empty strings with either a minimum length or a date pattern.
    control = feature.control
    assert control is not None
    violation = (
        f"Value length 0 < min_length ({control.min_length})"
        if control.min_length
        else f"Value '' does not match pattern '{control.pattern}'"
    )

    # Act: Set the feature to its empty current value.
    exit_status, _, err = await run_cli(
        "set", feature.name, "", "--fixture-device", fixture_device
    )

    # Assert: The client rejects the write with the specific violation.
    assert exit_status == 1
    assert f"Error setting feature: {violation}" in err


_CURVE_SLOPE = "heating.circuits.0.heating.curve.slope"


@pytest.mark.asyncio
async def test_exec_succeeds_with_every_required_parameter(run_cli):
    """A command executes when every required parameter is supplied."""
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
    """exec does not fill parameters from sibling features."""
    # Act: Execute the curve command without its 'shift' parameter.
    exit_status, _, err = await run_cli(
        "exec",
        _CURVE_SLOPE,
        "setCurve",
        json.dumps({"slope": 0.6}),
        "--fixture-device",
        "Vitocal250A",
    )

    # Assert: The command fails and names the missing parameter.
    assert exit_status == 1
    assert "missing required parameter 'shift'" in err
