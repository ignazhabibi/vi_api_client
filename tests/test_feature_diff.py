"""Tests for comparing raw API features and the diff-features CLI command."""

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from builders import load_fixture_device

from vi_api_client._feature_diff import (
    diff_features,
    feature_entries,
    format_feature_diff,
)
from vi_api_client.const import API_BASE_URL, ENDPOINT_FEATURES, ENDPOINT_INSTALLATIONS


def _feature(name: str, **fields: object) -> dict[str, object]:
    """Return one enabled, ready raw API feature without properties or commands."""
    return {
        "feature": name,
        "isEnabled": True,
        "isReady": True,
        "properties": {},
        "commands": {},
        **fields,
    }


def _curve_command(**slope: object) -> dict[str, Any]:
    """Return a setCurve command whose slope parameter has the given fields."""
    return {
        "setCurve": {
            "isExecutable": True,
            "params": {
                "slope": {
                    "type": "number",
                    "required": True,
                    "constraints": {"min": 0.2, "max": 3.5, "stepping": 0.1},
                    **slope,
                }
            },
            "uri": "https://example/commands/setCurve",
        }
    }


def _diff(left: list[dict[str, object]], right: list[dict[str, object]]):
    """Compare two feature lists given as raw API features."""
    return diff_features(
        feature_entries({"data": left}), feature_entries({"data": right})
    )


@pytest.mark.parametrize(
    ("document", "message"),
    [
        pytest.param([], "'data' list", id="not-an-object"),
        pytest.param({"data": {}}, "'data' list", id="data-not-a-list"),
        pytest.param({"data": ["text"]}, "non-empty 'feature' name", id="entry-text"),
        pytest.param({"data": [{"feature": ""}]}, "non-empty", id="empty-name"),
        pytest.param(
            {"data": [{"feature": "a"}, {"feature": "a"}]},
            "Duplicate feature in document: a",
            id="duplicate",
        ),
    ],
)
def test_feature_entries_rejects_documents_without_usable_features(document, message):
    with pytest.raises(ValueError, match=message):
        feature_entries(document)


def test_diff_lists_added_and_removed_features_by_name():
    # Act: Compare two feature lists that share one feature.
    diff = _diff(
        [_feature("b.removed"), _feature("shared")],
        [_feature("shared"), _feature("z.added"), _feature("a.added")],
    )

    # Assert: Only the names that exist on one side are listed, sorted.
    assert diff.added == ["a.added", "z.added"]
    assert diff.removed == ["b.removed"]
    assert diff.structure == {}
    assert diff.state == {}
    assert (diff.left_count, diff.right_count) == (2, 3)


def test_diff_reports_a_disabled_feature_as_state_not_structure():
    # Arrange: The API returns the disabled feature without properties and
    # commands, as the bundled fixtures show.
    disabled = _feature("heating.dhw.pumps.circulation.schedule", isEnabled=False)
    enabled = _feature(
        "heating.dhw.pumps.circulation.schedule",
        properties={"active": {"type": "boolean", "value": True}},
        commands=_curve_command(),
    )

    # Act: Compare the disabled with the enabled feature.
    diff = _diff([disabled], [enabled])

    # Assert: Only the state section lists the feature.
    assert diff.structure == {}
    assert diff.state == {
        "heating.dhw.pumps.circulation.schedule": ["isEnabled: false → true"]
    }


def test_diff_names_property_changes_without_comparing_values():
    # Arrange: Change a unit and a type, add and remove a property, and change
    # every value.
    left = _feature(
        "heating.sensor",
        properties={
            "value": {"type": "number", "unit": "celsius", "value": 20.5},
            "status": {"type": "string", "value": "connected"},
            "removed": {"type": "string", "value": "x"},
        },
    )
    right = _feature(
        "heating.sensor",
        properties={
            "value": {"type": "number", "unit": "kelvin", "value": 293.6},
            "status": {"type": "boolean", "value": True},
            "added": {"type": "number", "value": 1},
        },
    )

    # Act: Compare the feature.
    diff = _diff([left], [right])

    # Assert: Structure changes are named; value changes are not reported.
    assert diff.structure == {
        "heating.sensor": [
            "+ property added",
            "- property removed",
            'property status type: "string" → "boolean"',
            'property value unit: "celsius" → "kelvin"',
        ]
    }


def test_diff_names_command_and_parameter_changes():
    # Arrange: Change constraints and the type, add a parameter, and add and
    # remove commands.
    left_commands = {
        **_curve_command(),
        "setOld": {"isExecutable": True, "params": {}},
    }
    right_commands = _curve_command(
        type="integer",
        constraints={"min": 0.2, "max": 4.0, "enum": [1, 2]},
    )
    right_commands["setCurve"]["params"]["shift"] = {"type": "number"}
    right_commands["setNew"] = {"isExecutable": True, "params": {}}

    # Act: Compare the feature.
    diff = _diff(
        [_feature("heating.curve", commands=left_commands)],
        [_feature("heating.curve", commands=right_commands)],
    )

    # Assert: Every change is named with its command and parameter.
    assert diff.structure == {
        "heating.curve": [
            "+ command setNew",
            "- command setOld",
            "+ setCurve.shift",
            'setCurve.slope type: "number" → "integer"',
            "setCurve.slope enum: + [1, 2]",
            "setCurve.slope max: 3.5 → 4.0",
            "setCurve.slope stepping: - 0.1",
        ]
    }


def test_diff_reports_deprecation_even_for_a_disabled_feature():
    # Arrange: Deprecate a feature that is disabled on both sides.
    deprecation = {"removalDate": "2027-01-31", "info": "replaced by x"}
    left = _feature("device.old", isEnabled=False)
    right = _feature("device.old", isEnabled=False, deprecated=deprecation)

    # Act: Compare the feature.
    diff = _diff([left], [right])

    # Assert: The deprecation is a structure change.
    assert diff.structure == {
        "device.old": [
            'deprecated: none → {"info": "replaced by x", "removalDate": "2027-01-31"}'
        ]
    }


def test_diff_reports_ready_and_executable_state_changes():
    # Arrange: The feature turns ready, and its command turns executable.
    left = _feature("heating.curve", isReady=False, commands=_curve_command())
    right_commands = _curve_command()
    right_commands["setCurve"]["isExecutable"] = False
    right = _feature("heating.curve", commands=right_commands)

    # Act: Compare the feature.
    diff = _diff([left], [right])

    # Assert: The state section names both changes.
    assert diff.state == {
        "heating.curve": [
            "isReady: false → true",
            "setCurve.isExecutable: true → false",
        ]
    }


def test_format_reports_no_differences():
    diff = _diff([_feature("a")], [_feature("a")])

    assert format_feature_diff(diff, "left.json", "right.json") == [
        "Comparing left.json (1 features) with right.json (1 features)",
        "No differences.",
    ]


def test_format_lists_every_section_in_order():
    # Arrange: One added, removed, structure-changed, and state-changed feature.
    diff = _diff(
        [
            _feature("removed"),
            _feature("changed", properties={"a": {"type": "number"}}),
            _feature("toggled", isEnabled=False),
        ],
        [
            _feature("added"),
            _feature("changed", properties={"a": {"type": "string"}}),
            _feature("toggled"),
        ],
    )

    # Act: Format the comparison.
    lines = format_feature_diff(diff, "fixture:A", "live")

    # Assert: The sections appear in order with indented details.
    assert lines == [
        "Comparing fixture:A (3 features) with live (3 features)",
        "",
        "Added (1):",
        "  + added",
        "",
        "Removed (1):",
        "  - removed",
        "",
        "Structure changed (1):",
        "  changed",
        '    property a type: "number" → "string"',
        "",
        "State changed (1):",
        "  toggled",
        "    isEnabled: false → true",
    ]


async def test_diff_features_compares_two_bundled_fixtures(run_cli):
    # Act: Compare two heat pump fixtures.
    exit_status, out, _ = await run_cli(
        "diff-features", "fixture:Vitocal222S", "fixture:Vitocal250A"
    )

    # Assert: The report lists real differences, and the disabled schedule
    # appears as a state change only.
    assert exit_status == 0
    assert (
        "Comparing fixture:Vitocal222S (319 features) with fixture:Vitocal250A" in out
    )
    assert "  + device.brand" in out
    assert "  - heating.dhw.scaldProtection" in out
    assert "efficientUpperBorder: 53 → 55" in out
    assert "+ command setSchedule" not in out
    state_section = out.split("State changed")[1]
    assert "heating.dhw.pumps.circulation.schedule" in state_section


async def test_diff_features_compares_an_export_file_with_its_fixture(
    run_cli, tmp_path
):
    # Arrange: Export a fixture device into a file.
    _, export, _ = await run_cli("dump-device", "--fixture-device", "Vitodens200W")
    export_path = tmp_path / "export.json"
    export_path.write_text(export, encoding="utf-8")

    # Act: Compare the export with the bundled fixture.
    exit_status, out, _ = await run_cli(
        "diff-features", str(export_path), "fixture:Vitodens200W"
    )

    # Assert: Both sides hold the same features.
    assert exit_status == 0
    assert out.splitlines()[-1] == "No differences."


async def test_diff_features_compares_the_live_device(
    run_cli, vi_client, mock_responses, tmp_path
):
    # Arrange: Serve one device and the Vitocal250A features over HTTP with a
    # changed constraint.
    devices_url = (
        f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/1234567/gateways/"
        "7654321098765432/devices"
    )
    features_url = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/1234567/gateways/"
        "7654321098765432/devices/0/features/filter"
    )
    features = load_fixture_device("Vitocal250A")
    for api_feature in features["data"]:
        if api_feature["feature"] == "heating.dhw.temperature.main":
            params = api_feature["commands"]["setTargetTemperature"]["params"]
            params["temperature"]["constraints"]["max"] = 65
    mock_responses.get(
        devices_url,
        payload={
            "data": [
                {
                    "id": "0",
                    "modelId": "E3_Vitocal",
                    "deviceType": "heating",
                    "status": "connected",
                }
            ]
        },
    )
    mock_responses.post(features_url, payload=features)

    # Act: Compare the bundled fixture with the live device.
    with (
        patch("vi_api_client.cli.ViClient", return_value=vi_client),
        patch("vi_api_client.cli.OAuth"),
        patch("vi_api_client.cli.create_session") as mock_session,
    ):
        mock_session.return_value.__aenter__.return_value = MagicMock()
        exit_status, out, _ = await run_cli(
            "diff-features",
            "fixture:Vitocal250A",
            "live",
            "--client-id",
            "client",
            "--token-file",
            str(tmp_path / "tokens.json"),
            "--installation-id",
            "1234567",
            "--gateway-serial",
            "7654321098765432",
            "--device-id",
            "0",
        )

    # Assert: The live export differs from the fixture only by the constraint.
    assert exit_status == 0
    assert out.split("Structure changed (1):\n")[1].splitlines()[:2] == [
        "  heating.dhw.temperature.main",
        "    setTargetTemperature.temperature max: 60 → 65",
    ]
    assert "Added" not in out
    assert "State changed" not in out


@pytest.mark.parametrize(
    ("source", "message"),
    [
        pytest.param("fixture:Nope", "Unknown fixture device 'Nope'", id="fixture"),
        pytest.param("missing.json", "No such file", id="file"),
    ],
)
async def test_diff_features_reports_an_unusable_source(run_cli, source, message):
    # Act: Compare with a source that cannot be read.
    exit_status, out, err = await run_cli(
        "diff-features", source, "fixture:Vitodens200W"
    )

    # Assert: The command fails with the reason and prints no report.
    assert exit_status == 1
    assert "Comparing" not in out
    assert message in err


async def test_diff_features_reports_a_file_without_features(run_cli, tmp_path):
    # Arrange: Write a JSON file that is not a feature document.
    path = tmp_path / "other.json"
    path.write_text(json.dumps({"devices": []}), encoding="utf-8")

    # Act: Compare the file with a fixture.
    exit_status, _, err = await run_cli(
        "diff-features", str(path), "fixture:Vitodens200W"
    )

    # Assert: The command fails and explains the expected document.
    assert exit_status == 1
    assert "'data' list" in err
