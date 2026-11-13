"""Tests for the CLI command handlers and the dispatch entry path."""

import argparse
import importlib.metadata
import json
import logging
import subprocess
import sys
from argparse import Namespace
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from builders import (
    build_device,
    build_feature,
    build_gateway,
    build_installation,
    load_fixture_device,
    load_fixture_json,
)

from vi_api_client import (
    EventHistoryWindow,
    FeatureDeprecation,
    FeatureValue,
    FixtureViClient,
    InstallationEvent,
    ViClient,
    ViCommandRejectedError,
)
from vi_api_client.cli import (
    _EVENT_LINE_WIDTH,
    CLIContext,
    _close_name_hint,
    _deprecation_document,
    _deprecation_text,
    _dispatch_command,
    _event_detail_lines,
    _infer_feature_value_type,
    _print_event_summary,
    _removal_suffix,
    async_main,
    build_parser,
    cmd_exec,
    cmd_get_feature,
    cmd_list_devices,
    cmd_list_events,
    cmd_list_features,
    cmd_list_fixture_devices,
    cmd_list_writable,
    cmd_login,
    cmd_set,
    get_client_config,
    main,
)
from vi_api_client.exceptions import (
    ViNotFoundError,
    ViValidationError,
)
from vi_api_client.models import (
    CommandParameter,
    Device,
    DeviceCommand,
    Feature,
    FeatureConstraints,
    ParameterWriteBinding,
    ScheduleConstraints,
)
from vi_api_client.parsing import api_feature_to_flat_features


def _cli_args(*argv: str) -> Namespace:
    """Parse a command line through the real CLI parser."""
    return build_parser().parse_args(argv)


def _binding(**overrides: Any) -> ParameterWriteBinding:
    """Build one internal write binding for a ``heating.curve.slope`` target."""
    fields: dict[str, Any] = {
        "command_name": "setCurve",
        "parameter_name": "slope",
        "required_parameters": ["slope"],
        "api_feature_name": "heating.curve",
        "uri": "uri",
    }
    fields.update(overrides)
    return ParameterWriteBinding(**fields)


def _constraints(**overrides: Any) -> FeatureConstraints:
    """Build the constraint rules of one writable test feature."""
    return FeatureConstraints(**overrides)


def _curve_command() -> DeviceCommand:
    """Build the reported setCurve command of the ``heating.curve`` API feature."""
    return DeviceCommand(
        name="setCurve",
        uri="uri",
        parameters=(
            CommandParameter(
                name="shift",
                required=True,
                constraints=FeatureConstraints(value_type="number"),
            ),
            CommandParameter(
                name="slope",
                required=True,
                constraints=FeatureConstraints(
                    value_type="number", min=0.2, max=3.5, step=0.1
                ),
            ),
        ),
    )


def _snapshot_device(
    *features: Feature,
    commands: dict[str, tuple[DeviceCommand, ...]] | None = None,
) -> Device:
    """Build the refreshed device snapshot the CLI commands work on."""
    return replace(
        build_device("DEV1"),
        features=features,
        commands_by_api_feature=commands
        if commands is not None
        else {"heating.curve": (_curve_command(),)},
    )


@pytest.fixture
def mock_cli_context() -> Iterator[CLIContext]:
    """Make commands use a context whose client offers real ViClient methods."""
    context = CLIContext(None, AsyncMock(spec=ViClient), "99", "GW1", "DEV1")
    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = context
        yield context


async def test_cmd_set_writes_the_parsed_value(mock_cli_context, capsys):
    """Successful writes should confirm the command, param, and result."""
    # Arrange: Provide a writable numeric feature and a successful write.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    feature = build_feature(
        binding=_binding(), constraints=FeatureConstraints(value_type="number")
    )
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: Set the slope through the CLI.
    assert await cmd_set(args) is True

    # Assert: The write receives the hydrated device, feature, and parsed value.
    mock_cli_context.client.set_feature.assert_awaited_once()
    call_args = mock_cli_context.client.set_feature.call_args
    assert call_args.args[0].get_feature("heating.curve.slope") is feature
    assert call_args.args[2] == 1.4
    assert "Success!" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("value_type", "raw_value", "expected_value"),
    [
        pytest.param("string", "01", "01", id="string"),
        pytest.param("boolean", "TRUE", True, id="boolean-true"),
        pytest.param("boolean", "false", False, id="boolean-false"),
        pytest.param("integer", "2", 2, id="integer"),
        pytest.param(
            "Schedule",
            '{"mon": [{"start": "06:00", "end": "22:00", "mode": "on"}]}',
            {"mon": [{"start": "06:00", "end": "22:00", "mode": "on"}]},
            id="schedule-json",
        ),
    ],
)
async def test_cmd_set_converts_typed_command_values(
    mock_cli_context, value_type: str, raw_value: str, expected_value: Any
):
    """CLI writes should convert values to the target command parameter type."""
    # Arrange: Provide a writable feature of the parameter's declared type.
    args = _cli_args("set", "heating.curve.slope", raw_value)
    feature = build_feature(
        binding=_binding(), constraints=FeatureConstraints(value_type=value_type)
    )
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: Submit the raw command line value.
    assert await cmd_set(args) is True

    # Assert: The command receives the converted typed value.
    assert mock_cli_context.client.set_feature.call_args.args[2] == expected_value


@pytest.mark.parametrize(
    ("value_type", "raw_value", "message"),
    [
        pytest.param("number", "automatic", "must be a number", id="number-text"),
        pytest.param("number", "inf", "must be finite", id="number-infinite"),
        pytest.param("boolean", "maybe", "must be true or false", id="boolean-text"),
        pytest.param("integer", "1.5", "must be an integer", id="integer-fraction"),
    ],
)
async def test_cmd_set_rejects_malformed_typed_values(
    mock_cli_context, capsys, value_type: str, raw_value: str, message: str
):
    """Malformed typed values should explain the failure without a write."""
    # Arrange: Provide a writable feature whose declared type rejects the value.
    args = _cli_args("set", "heating.curve.slope", raw_value)
    feature = build_feature(
        binding=_binding(), constraints=FeatureConstraints(value_type=value_type)
    )
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: Submit the malformed command line value.
    assert await cmd_set(args) is False

    # Assert: The validation failure is printed and no write is sent.
    captured = capsys.readouterr()
    assert f"Error: Value '{raw_value}'" in captured.out
    assert message in captured.out
    mock_cli_context.client.set_feature.assert_not_called()


async def test_cmd_set_rejects_schedules_that_are_not_json(mock_cli_context, capsys):
    """A schedule value that is not JSON should be rejected without a write."""
    # Arrange: Provide a writable schedule feature.
    args = _cli_args("set", "heating.curve.slope", "mon 06:00-22:00")
    feature = build_feature(
        binding=_binding(), constraints=FeatureConstraints(value_type="Schedule")
    )
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: Submit a schedule that is not JSON.
    assert await cmd_set(args) is False

    # Assert: The validation failure asks for JSON and no write is sent.
    assert "must be a JSON schedule object" in capsys.readouterr().out
    mock_cli_context.client.set_feature.assert_not_called()


async def test_cmd_set_infers_legacy_feature_types(mock_cli_context):
    """Legacy features without metadata should infer their write type."""
    # Arrange: Provide a valueless-type control whose value hints the type.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    feature = build_feature(value=1.0, binding=_binding())
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: Set a numeric current value through the CLI.
    assert await cmd_set(args) is True

    # Assert: The numeric feature value infers the number conversion.
    assert mock_cli_context.client.set_feature.call_args.args[2] == 1.4


async def test_cmd_set_reports_failed_command_results(mock_cli_context, capsys):
    """Rejected API writes should print the rejection message and reason."""
    # Arrange: Make the API reject the command with its details.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    feature = build_feature(
        binding=_binding(), constraints=FeatureConstraints(value_type="number")
    )
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)
    mock_cli_context.client.set_feature.side_effect = ViCommandRejectedError(
        "API rejected", "out of range"
    )

    # Act: Attempt the write through the CLI.
    assert await cmd_set(args) is False

    # Assert: The failure surfaces the rejection details.
    captured = capsys.readouterr()
    assert "Failed!" in captured.out
    assert "Message: API rejected" in captured.out
    assert "Reason: out of range" in captured.out


async def test_cmd_set_reports_failed_results_without_details(mock_cli_context, capsys):
    """Absent rejection details must not print empty Message or Reason lines."""
    # Arrange: Make the API reject the command without a message or reason.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    feature = build_feature(
        binding=_binding(), constraints=FeatureConstraints(value_type="number")
    )
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)
    mock_cli_context.client.set_feature.side_effect = ViCommandRejectedError()

    # Act: Attempt the write through the CLI.
    assert await cmd_set(args) is False

    # Assert: The failure prints without message or reason lines.
    captured = capsys.readouterr()
    assert "Failed!" in captured.out
    assert "Message:" not in captured.out
    assert "Reason:" not in captured.out


async def test_cmd_set_reports_missing_device_context(mock_cli_context, caplog):
    """Writes without a device context should fail gracefully."""
    # Arrange: Strip the device identifier from the CLI context.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    mock_cli_context.device_id = None

    # Act: Attempt the write without a device context.
    result = await cmd_set(args)

    # Assert: The command names the missing context and sends nothing.
    assert result is False
    assert "A device context is required" in caplog.text
    mock_cli_context.client.refresh_device.assert_not_called()
    mock_cli_context.client.set_feature.assert_not_called()


@pytest.mark.parametrize(
    ("argv", "failure"),
    [
        pytest.param(
            ["set", "heating.curve.slope", "1.4"],
            ("set_feature", "setting feature"),
            id="set",
        ),
        pytest.param(
            ["get-feature", "heating.curve.slope"],
            ("get_features", "fetching feature"),
            id="get-feature",
        ),
        pytest.param(
            ["exec", "heating.curve", "setCurve", "slope=1.4"],
            ("run_command", "executing command"),
            id="exec",
        ),
        pytest.param(
            ["list-features"], ("get_features", "listing features"), id="list-features"
        ),
        pytest.param(
            ["list-devices"],
            ("get_installations", "listing devices"),
            id="list-devices",
        ),
        pytest.param(
            ["list-writable"],
            ("refresh_device", "listing writable features"),
            id="list-writable",
        ),
        pytest.param(
            ["list-events", "--days", "7"],
            ("get_event_history_window", "listing events"),
            id="list-events",
        ),
    ],
)
async def test_commands_log_unexpected_client_errors_and_fail(
    argv: list[str], failure: tuple[str, str], mock_cli_context, caplog
):
    """Unexpected client errors should fail the command with a logged reason."""
    # Arrange: Offer one writable feature and make one client call fail.
    failing_method, action = failure
    feature = build_feature(
        binding=_binding(), constraints=FeatureConstraints(value_type="number")
    )
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)
    getattr(mock_cli_context.client, failing_method).side_effect = RuntimeError("boom")

    # Act: Run the command against the failing client.
    args = _cli_args(*argv)
    result = await args.handler(args)

    # Assert: The command fails and logs which action failed and why.
    assert result is False
    assert f"Error {action}: boom" in caplog.text


async def test_cmd_set_rejects_read_only_features(mock_cli_context, capsys):
    """Read-only features should reject CLI writes before any request."""
    # Arrange: Provide a feature without command metadata.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(
        build_feature()
    )

    # Act: Attempt to set the read-only feature.
    assert await cmd_set(args) is False

    # Assert: The CLI explains the read-only state without a write.
    assert "is read-only" in capsys.readouterr().out
    mock_cli_context.client.set_feature.assert_not_called()


async def test_cmd_set_reports_missing_features(mock_cli_context, capsys):
    """Unknown feature names should reject CLI writes before any request."""
    # Arrange: Return no features for the requested name.
    args = _cli_args("set", "missing.feature", "1.4")
    mock_cli_context.client.refresh_device.return_value = _snapshot_device()

    # Act: Attempt to set an absent feature.
    assert await cmd_set(args) is False

    # Assert: The CLI names the missing feature.
    assert "Error: Feature 'missing.feature' not found." in capsys.readouterr().out
    mock_cli_context.client.set_feature.assert_not_called()


async def test_cmd_set_reports_not_found_errors(mock_cli_context, capsys):
    """Not-found write failures should surface the API message."""
    # Arrange: Make the write fail with a not-found API error.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    feature = build_feature(
        binding=_binding(), constraints=FeatureConstraints(value_type="number")
    )
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)
    mock_cli_context.client.set_feature.side_effect = ViNotFoundError("device gone")

    # Act: Attempt the write.
    assert await cmd_set(args) is False

    # Assert: The CLI reports the not-found failure.
    assert "Not found: device gone" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("feature_value", "numeric_constraints", "expected_type"),
    [
        pytest.param(True, False, "boolean", id="boolean"),
        pytest.param(1.4, False, "number", id="float"),
        pytest.param(3, False, "number", id="integer"),
        pytest.param("text", True, "number", id="text-with-numeric-constraints"),
        pytest.param("text", False, "string", id="text"),
    ],
)
def test_infer_feature_value_type_uses_value_shape_and_constraints(
    feature_value: FeatureValue, numeric_constraints: bool, expected_type: str
):
    """Legacy feature values should infer their command parameter type."""
    # Arrange: Build a legacy feature with or without numeric constraints.
    constraints = FeatureConstraints()
    if numeric_constraints:
        constraints = FeatureConstraints(min=0.2, max=3.5, step=0.1)
    feature = build_feature(
        value=feature_value, binding=_binding(), constraints=constraints
    )

    # Act and assert: The value shape and constraints determine the type
    # inferred for a command without metadata.
    assert _infer_feature_value_type(feature) == expected_type


async def test_cmd_get_feature_prints_value_control_details_and_commands(
    mock_cli_context, capsys
):
    """A writable feature should print its value, command details, and commands."""
    # Arrange: Return one constrained writable feature and the snapshot with
    # the reported command.
    args = _cli_args("get-feature", "heating.curve.slope")
    feature = build_feature(
        "heating.curve.slope",
        1.4,
        _binding(),
        constraints=FeatureConstraints(
            min=0.2, max=3.5, step=0.1, options=["low", "high"]
        ),
        unit="celsius",
    )
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: Read the feature through the CLI.
    assert await cmd_get_feature(args) is True

    # Assert: The output includes the formatted value, control details, and
    # the commands of the feature's API feature with parameter rules.
    captured = capsys.readouterr()
    assert "- heating.curve.slope: 1.4 celsius" in captured.out
    assert "Writable via command: setCurve" in captured.out
    assert "Target param: slope" in captured.out
    assert "Constraints: min=0.2, max=3.5, step=0.1" in captured.out
    assert "Options: ('low', 'high')" in captured.out
    assert "  Commands of heating.curve:" in captured.out
    assert "  - setCurve: shift*, slope*" in captured.out
    assert "      shift: number" in captured.out
    assert "      slope: number, min: 0.2, max: 3.5, step: 0.1" in captured.out


async def test_cmd_get_feature_prints_read_only_values_without_commands(
    mock_cli_context, capsys
):
    """A read-only feature outside any command list prints only its value."""
    # Arrange: Return one read-only feature no command-reporting API feature
    # owns.
    args = _cli_args("get-feature", "heating.status")
    feature = build_feature("heating.status", "ready")
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: Read the feature through the CLI.
    assert await cmd_get_feature(args) is True

    # Assert: The value prints without writable or command details.
    captured = capsys.readouterr()
    assert "- heating.status: ready" in captured.out
    assert "Writable via command" not in captured.out
    assert "Commands of" not in captured.out


async def test_cmd_get_feature_omits_the_section_for_commandless_api_features(
    mock_cli_context, capsys
):
    """An API feature that reports no commands adds no command section."""
    # Arrange: Return one read-only feature whose API feature is in the
    # snapshot's command list with an empty command tuple.
    args = _cli_args("get-feature", "heating.status")
    feature = build_feature("heating.status", "ready")
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(
        feature, commands={"heating.status": ()}
    )

    # Act: Read the feature through the CLI.
    assert await cmd_get_feature(args) is True

    # Assert: The feature prints and no empty command section appears.
    captured = capsys.readouterr()
    assert "- heating.status: ready" in captured.out
    assert "Commands of" not in captured.out


@pytest.mark.parametrize(
    ("constraint_overrides", "expect_constraints"),
    [
        pytest.param({"options": ["low", "high"]}, False, id="options-only"),
        pytest.param({"min": 0.2}, True, id="min-only"),
    ],
)
async def test_cmd_get_feature_prints_only_present_control_details(
    mock_cli_context, capsys, constraint_overrides: dict, expect_constraints: bool
):
    """Control details should print only the constraints and options that exist."""
    # Arrange: Return a writable feature with only one kind of control detail.
    args = _cli_args("get-feature", "heating.curve.slope")
    feature = build_feature(
        "heating.curve.slope",
        1.4,
        _binding(),
        constraints=_constraints(**constraint_overrides),
        unit="celsius",
    )
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: Read the feature through the CLI.
    assert await cmd_get_feature(args) is True

    # Assert: Only the present detail kind prints.
    captured = capsys.readouterr()
    assert ("Constraints:" in captured.out) is expect_constraints
    assert ("Options:" in captured.out) is not expect_constraints


async def test_cmd_get_feature_json_prints_machine_readable_document(
    mock_cli_context, capsys
):
    """The JSON flag should print the feature with its constraints and command."""
    # Arrange: Return one writable feature with a unit for the requested name.
    args = _cli_args("get-feature", "heating.curve.slope", "--json")
    feature = build_feature("heating.curve.slope", 1.4, _binding(), unit="celsius")
    mock_cli_context.client.get_features.return_value = [feature]

    # Act: Read the feature in JSON mode.
    assert await cmd_get_feature(args) is True

    # Assert: The document carries the feature fields, the constraint rules,
    # and the bound command without its URI.
    document = json.loads(capsys.readouterr().out)
    assert document["name"] == "heating.curve.slope"
    assert document["value"] == 1.4
    assert document["unit"] == "celsius"
    assert document["constraints"] == {
        "value_type": None,
        "min": None,
        "max": None,
        "step": None,
        "options": None,
        "min_length": None,
        "max_length": None,
        "pattern": None,
        "schedule": None,
    }
    assert document["command"] == {"name": "setCurve", "parameter": "slope"}


def _curve_features() -> list[Feature]:
    """Return the two features parsed from one heating curve API feature."""
    return [
        build_feature(f"heating.curve.{name}", value)
        for name, value in (("shift", 0), ("slope", 1.4))
    ]


async def test_cmd_get_feature_json_marks_read_only_features_with_null_command(
    mock_cli_context, capsys
):
    """Read-only features have no constraints or command in the JSON document."""
    # Arrange: Return one read-only feature.
    args = _cli_args("get-feature", "heating.curve.slope", "--json")
    mock_cli_context.client.get_features.return_value = [build_feature()]

    # Act: Read the feature in JSON mode.
    assert await cmd_get_feature(args) is True

    # Assert: The command and constraints are JSON null, not absent keys, and
    # the JSON mode reads no snapshot for the command list.
    document = json.loads(capsys.readouterr().out)
    assert document["command"] is None
    assert document["constraints"] is None
    mock_cli_context.client.refresh_device.assert_not_called()


async def test_cmd_get_feature_prints_every_feature_of_an_api_feature(
    mock_cli_context, capsys
):
    """An API feature name should print every feature parsed from it."""
    # Arrange: Return both features of the requested API feature.
    args = _cli_args("get-feature", "heating.curve")
    mock_cli_context.client.get_features.return_value = _curve_features()
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(
        *_curve_features()
    )

    # Act: Read the API feature through the CLI.
    assert await cmd_get_feature(args) is True

    # Assert: Both matching features print, and the command list prints once.
    captured = capsys.readouterr()
    assert "- heating.curve.shift: 0" in captured.out
    assert "- heating.curve.slope: 1.4" in captured.out
    assert captured.out.count("Commands of heating.curve:") == 1


async def test_cmd_get_feature_json_prints_one_array_for_several_features(
    mock_cli_context, capsys
):
    """Several JSON matches should print one JSON array document."""
    # Arrange: Return both features of the requested API feature.
    args = _cli_args("get-feature", "heating.curve", "--json")
    mock_cli_context.client.get_features.return_value = _curve_features()

    # Act: Read the API feature in JSON mode.
    assert await cmd_get_feature(args) is True

    # Assert: The output is one JSON array with one document per feature.
    documents = json.loads(capsys.readouterr().out)
    assert [document["name"] for document in documents] == [
        "heating.curve.shift",
        "heating.curve.slope",
    ]


async def test_cmd_get_feature_reports_unknown_feature_names(mock_cli_context, capsys):
    """An unknown name fails the read rather than printing an empty result."""
    args = _cli_args("get-feature", "missing.feature")
    mock_cli_context.client.get_features.return_value = []
    mock_cli_context.client.refresh_device.return_value = build_device("DEV1")

    assert await cmd_get_feature(args) is False

    assert capsys.readouterr().out == "Feature 'missing.feature' not found.\n"


async def test_cmd_login_uses_environment_config_and_persists_it(
    monkeypatch, tmp_path, capsys
):
    """Login should reuse environment credentials and save them for later commands."""
    # Arrange: Seed a token file and provide client settings through the environment.
    token_file = tmp_path / "tokens.json"
    token_file.write_text(
        '{"access_token": "existing-token", "future_field": "preserve-me"}',
        encoding="utf-8",
    )
    args = _cli_args("login", "--token-file", str(token_file))
    monkeypatch.setenv("VIESSMANN_CLIENT_ID", "environment-client-id")
    monkeypatch.setenv("VIESSMANN_REDIRECT_URI", "http://localhost:8123/auth")
    mock_auth = MagicMock()
    mock_auth.get_authorization_url.return_value = "https://example.invalid/authorize"
    mock_auth.async_exchange_code_for_tokens = AsyncMock()

    with (
        patch("builtins.input", return_value="authorization-code"),
        patch("vi_api_client.cli.OAuth", return_value=mock_auth) as mock_oauth,
        patch("vi_api_client.cli.create_session") as mock_create_session,
    ):
        mock_session = MagicMock()
        mock_create_session.return_value.__aenter__.return_value = mock_session

        # Act: Complete the CLI login flow without explicit command-line settings.
        result = await cmd_login(args)

    # Assert: The resolved configuration should be used and stored with tokens.
    assert result is True
    assert "Successfully authenticated!" in capsys.readouterr().out
    mock_auth.async_exchange_code_for_tokens.assert_awaited_once_with(
        "authorization-code"
    )
    saved_config = json.loads(token_file.read_text(encoding="utf-8"))
    mock_oauth.assert_called_once_with(
        "environment-client-id",
        "http://localhost:8123/auth",
        str(token_file),
        websession=mock_session,
    )
    assert saved_config == {
        "access_token": "existing-token",
        "client_id": "environment-client-id",
        "redirect_uri": "http://localhost:8123/auth",
        "future_field": "preserve-me",
    }


async def test_cmd_login_requires_a_client_id(monkeypatch, tmp_path):
    """Login without any configured client ID should fail with guidance."""
    # Arrange: Provide neither an argument, environment, nor stored client ID.
    token_file = tmp_path / "tokens.json"
    args = _cli_args("login", "--token-file", str(token_file))
    monkeypatch.delenv("VIESSMANN_CLIENT_ID", raising=False)
    monkeypatch.delenv("VIESSMANN_REDIRECT_URI", raising=False)

    # Act and assert: The login command fails with a configuration hint.
    with pytest.raises(ValueError, match=r"Client ID not found\..*run 'login'"):
        await cmd_login(args)


def test_get_client_config_prefers_arguments_then_environment_then_document(
    monkeypatch, tmp_path
):
    """Client settings should resolve in documented priority order."""
    # Arrange: Store fallback settings and configure conflicting higher priorities.
    token_file = tmp_path / "tokens.json"
    token_file.write_text(
        '{"client_id": "saved-client", "redirect_uri": "http://saved"}',
        encoding="utf-8",
    )
    args = _cli_args(
        "login", "--client-id", "argument-client", "--token-file", str(token_file)
    )
    monkeypatch.setenv("VIESSMANN_CLIENT_ID", "environment-client")
    monkeypatch.setenv("VIESSMANN_REDIRECT_URI", "http://environment")

    # Act: Resolve the settings for a CLI invocation.
    client_id, redirect_uri = get_client_config(args)

    # Assert: Arguments override the environment, which overrides the document.
    assert (client_id, redirect_uri) == ("argument-client", "http://environment")


def test_get_client_config_uses_document_then_default_redirect(monkeypatch, tmp_path):
    """Saved settings should supply the client ID and default redirect URI."""
    # Arrange: Store only a saved client ID with no higher-priority settings.
    token_file = tmp_path / "tokens.json"
    token_file.write_text('{"client_id": "saved-client"}', encoding="utf-8")
    args = _cli_args("login", "--token-file", str(token_file))
    monkeypatch.delenv("VIESSMANN_CLIENT_ID", raising=False)
    monkeypatch.delenv("VIESSMANN_REDIRECT_URI", raising=False)

    # Act: Resolve settings for a command without arguments or environment values.
    client_id, redirect_uri = get_client_config(args)

    # Assert: The document supplies the client ID and the redirect falls back.
    assert (client_id, redirect_uri) == ("saved-client", "http://localhost:4200/")


def test_get_client_config_uses_saved_redirect_uri(monkeypatch, tmp_path):
    """Saved redirect URIs should be reused for later commands."""
    # Arrange: Store both settings in the credential document.
    token_file = tmp_path / "tokens.json"
    token_file.write_text(
        '{"client_id": "saved-client", "redirect_uri": "http://saved"}',
        encoding="utf-8",
    )
    args = _cli_args("login", "--token-file", str(token_file))
    monkeypatch.delenv("VIESSMANN_CLIENT_ID", raising=False)
    monkeypatch.delenv("VIESSMANN_REDIRECT_URI", raising=False)

    # Act: Resolve settings for a command without arguments or environment values.
    client_id, redirect_uri = get_client_config(args)

    # Assert: The document supplies both stored settings.
    assert (client_id, redirect_uri) == ("saved-client", "http://saved")


async def test_async_main_rejects_malformed_credential_document(monkeypatch, tmp_path):
    """Malformed credentials should produce a failing CLI status without rewrites."""
    # Arrange: Point an initial login command at malformed credential data.
    token_file = tmp_path / "tokens.json"
    invalid_content = "{not-json"
    token_file.write_text(invalid_content, encoding="utf-8")

    # Act: Invoke the command through its process-status boundary.
    exit_status = await async_main(["login", "--token-file", str(token_file)])

    # Assert: The command fails and leaves the malformed source untouched.
    assert exit_status == 1
    assert token_file.read_text(encoding="utf-8") == invalid_content


async def test_cmd_exec_runs_any_reported_command_of_an_api_feature(
    mock_cli_context, capsys
):
    """CLI exec runs any command the API feature reports through run_command."""
    # Arrange: Provide a refreshed snapshot that reports the setCurve command.
    args = _cli_args("exec", "heating.curve", "setCurve", "slope=1.4", "shift=0")
    feature = build_feature(binding=_binding())
    snapshot = _snapshot_device(feature)
    mock_cli_context.client.refresh_device.return_value = snapshot

    # Act: Execute the explicit command through the CLI.
    assert await cmd_exec(args) is True

    # Assert: The client receives the refreshed device with its commands.
    mock_cli_context.client.refresh_device.assert_awaited_once()
    assert mock_cli_context.client.refresh_device.call_args.kwargs == {
        "only_available": False
    }
    mock_cli_context.client.run_command.assert_awaited_once_with(
        snapshot, "heating.curve", "setCurve", {"slope": 1.4, "shift": 0}
    )
    assert "Success!" in capsys.readouterr().out


async def test_cmd_exec_reports_rejections(mock_cli_context, capsys):
    """Rejected commands print the failure line with the rejection details."""
    # Arrange: Make the API reject the command with its details.
    args = _cli_args("exec", "heating.curve", "setCurve", "slope=1.4")
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(
        build_feature()
    )
    mock_cli_context.client.run_command.side_effect = ViCommandRejectedError(
        "API rejected", "out of range"
    )

    # Act: Run the command through the CLI.
    assert await cmd_exec(args) is False

    # Assert: The failure surfaces the rejection details.
    captured = capsys.readouterr()
    assert "Failed!" in captured.out
    assert "Message: API rejected" in captured.out
    assert "Reason: out of range" in captured.out


async def test_cmd_exec_reports_rejections_without_details(mock_cli_context, capsys):
    """Absent rejection details print no empty Message or Reason lines."""
    # Arrange: Make the API reject the command without details.
    args = _cli_args("exec", "heating.curve", "setCurve")
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(
        build_feature()
    )
    mock_cli_context.client.run_command.side_effect = ViCommandRejectedError()

    # Act: Run the command through the CLI.
    assert await cmd_exec(args) is False

    # Assert: The failure prints without message or reason lines.
    captured = capsys.readouterr()
    assert "Failed!" in captured.out
    assert "Message:" not in captured.out
    assert "Reason:" not in captured.out


async def test_cmd_exec_rejects_malformed_parameter_arguments(mock_cli_context, capsys):
    """Unparseable parameter arguments should reject before any request."""
    args = _cli_args("exec", "heating.curve", "setCurve", "not-key-value")

    assert await cmd_exec(args) is False

    assert "Error parsing parameters:" in capsys.readouterr().out
    mock_cli_context.client.refresh_device.assert_not_called()


async def test_cmd_exec_reports_not_found_errors(mock_cli_context, capsys):
    """Not-found explicit commands should surface the API message."""
    # Arrange: Make the explicit command fail with a not-found API error.
    args = _cli_args("exec", "heating.curve", "setCurve", "slope=1.4")
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(
        build_feature()
    )
    mock_cli_context.client.run_command.side_effect = ViNotFoundError("device gone")

    # Act: Execute the explicit command.
    assert await cmd_exec(args) is False

    # Assert: The CLI reports the not-found failure.
    assert "Not found: device gone" in capsys.readouterr().out


async def test_cmd_exec_reports_validation_errors(mock_cli_context, capsys):
    """Validation errors from explicit commands should print their message."""
    # Arrange: Make the explicit command fail with a validation error.
    args = _cli_args("exec", "heating.curve", "setCurve", "slope=invalid")
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(
        build_feature()
    )
    mock_cli_context.client.run_command.side_effect = ViValidationError(
        "Simulated Validation Error"
    )

    # Act: Execute the explicit command.
    assert await cmd_exec(args) is False

    # Assert: The CLI reports the validation failure.
    assert "Validation failed: Simulated Validation Error" in capsys.readouterr().out


async def test_run_cli_set_writes_a_fixture_feature(run_cli):
    """A fixture device write succeeds end-to-end through the CLI status."""
    # Act: Set the slope of the fixture heating curve.
    exit_status, out, _ = await run_cli(
        "set",
        "--fixture-device",
        "Vitocal250A",
        "heating.circuits.0.heating.curve.slope",
        "0.9",
    )

    # Assert: The write confirms the command and the parameter before
    # reporting success.
    assert exit_status == 0
    assert "Setting 'heating.circuits.0.heating.curve.slope' to '0.9'..." in out
    assert "(Command: setCurve, Param: slope)" in out
    assert "✅ Success!" in out


async def test_run_cli_set_fails_on_a_rejected_command(run_cli, mock_cli_context):
    """A rejected CLI write exits non-zero with the rejection lines."""
    # Arrange: Offer a writable slope and reject the write with details.
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(
        build_feature(
            binding=_binding(), constraints=FeatureConstraints(value_type="number")
        )
    )
    mock_cli_context.client.set_feature.side_effect = ViCommandRejectedError(
        "API rejected", "out of range"
    )

    # Act: Set the slope through the full CLI dispatch.
    exit_status, out, _ = await run_cli("set", "heating.curve.slope", "1.4")

    # Assert: The process fails and prints the failure lines.
    assert exit_status == 1
    assert "❌ Failed!" in out
    assert "Message: API rejected" in out
    assert "Reason: out of range" in out


async def test_run_cli_exec_runs_a_command_without_parameters(run_cli):
    """A fixture command without parameters runs end-to-end through the CLI."""
    # Act: Unschedule a fixture holiday through the CLI.
    exit_status, out, _ = await run_cli(
        "exec",
        "--fixture-device",
        "Vitocal250A",
        "heating.operating.programs.holiday",
        "unschedule",
    )

    # Assert: The command runs and the CLI succeeds.
    assert exit_status == 0
    assert "Executing 'unschedule' on heating.operating.programs.holiday..." in out
    assert "Using parameters: {}" in out
    assert "✅ Success!" in out


async def test_run_cli_exec_rejects_flat_feature_names(run_cli):
    """A flat feature name is no API feature; the error points to its parent."""
    # Act: Use a flat feature name as the API feature argument.
    exit_status, _, err = await run_cli(
        "exec",
        "--fixture-device",
        "Vitocal250A",
        "heating.circuits.0.heating.curve.slope",
        "setCurve",
        "slope=1.4",
    )

    # Assert: The failure names the similar API features reporting commands,
    # the closest first; API features without commands stay off the list.
    assert exit_status == 1
    assert (
        "API feature 'heating.circuits.0.heating.curve.slope' reports no commands"
        in err
    )
    assert (
        "API features with commands: heating.circuits.0.heating.curve, "
        "heating.circuits.0\n" in err
    )


async def test_run_cli_get_feature_lists_the_reported_commands(run_cli):
    """get-feature prints the value alongside the reported commands."""
    # Act: Read the fixture heating curve slope.
    exit_status, out, _ = await run_cli(
        "get-feature",
        "--fixture-device",
        "Vitocal250A",
        "heating.circuits.0.heating.curve.slope",
    )

    # Assert: The output shows the value, the write binding, and one command
    # list with parameter names, required markers, and constraints.
    assert exit_status == 0
    lines = out.splitlines()
    assert any(
        line.startswith("- heating.circuits.0.heating.curve.slope: 0.6")
        for line in lines
    )
    assert "  Writable via command: setCurve" in lines
    assert "  Commands of heating.circuits.0.heating.curve:" in lines
    assert "  - setCurve: slope*, shift*" in lines
    assert "      slope: number, min: 0.2, max: 3.5, step: 0.1" in lines
    assert "      shift: number, min: -13, max: 40, step: 1" in lines


async def test_cmd_set_hydrates_required_command_dependencies(mock_cli_context):
    """CLI writes should provide sibling values required by a command."""
    # Arrange: Provide heating-curve features that share the setCurve command.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    binding = _binding(required_parameters=["shift", "slope"])
    slope = build_feature(binding=binding)
    shift = build_feature(name="heating.curve.shift", value=4.0, binding=binding)
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(slope, shift)

    # Act: Set the slope through the CLI command.
    assert await cmd_set(args) is True

    # Assert: The write receives a device containing the sibling shift value.
    command_device = mock_cli_context.client.set_feature.call_args.args[0]
    assert command_device.get_feature("heating.curve.slope") is slope
    assert command_device.get_feature("heating.curve.shift") is shift
    assert mock_cli_context.client.refresh_device.call_args.kwargs == {
        "only_available": False
    }


async def test_cmd_list_features_json_prints_feature_names(mock_cli_context, capsys):
    """Names-only JSON is a bare array without the text header."""
    # Arrange: Provide two features and request JSON output.
    args = _cli_args("list-features", "--json")
    mock_cli_context.client.get_features.return_value = [
        build_feature(name="f1", value=1),
        build_feature(name="f2", value=2),
    ]

    # Act: List the features with JSON output.
    assert await cmd_list_features(args) is True

    # Assert: stdout carries a JSON list of feature names.
    assert json.loads(capsys.readouterr().out) == ["f1", "f2"]


async def test_cmd_list_features_json_values_print_value_documents(
    mock_cli_context, capsys
):
    """Value JSON carries value, unit, formatted text, writability, and notice."""
    # Arrange: Provide one writable feature and request JSON value output.
    args = _cli_args("list-features", "--values", "--json")
    mock_cli_context.client.get_features.return_value = [
        build_feature(name="f1", value=1.4, binding=_binding())
    ]

    # Act: List the feature values with JSON output.
    assert await cmd_list_features(args) is True

    # Assert: stdout carries one machine-readable value document per feature.
    documents = json.loads(capsys.readouterr().out)
    assert documents == [
        {
            "name": "f1",
            "value": 1.4,
            "unit": None,
            "formatted": "1.4",
            "writable": True,
            "deprecation": None,
        }
    ]


@pytest.mark.parametrize("flag", ["--available", "--enabled"])
async def test_cmd_list_features_available_requests_only_available_features(
    mock_cli_context, flag
):
    """--available, or its alias --enabled, delegates the filter to the read."""
    # Arrange: Provide one feature and request the available filter.
    args = _cli_args("list-features", flag, "--json")
    mock_cli_context.client.get_features.return_value = [
        build_feature(name="f_enabled", value=1)
    ]

    # Act: List only available features.
    assert await cmd_list_features(args) is True

    # Assert: The read requests the available filter for the context device.
    call_args = mock_cli_context.client.get_features.call_args
    assert call_args.args[0].id == "DEV1"
    assert call_args.kwargs["only_available"] is True


async def test_cmd_list_features_values_prints_table_with_writable_marks(
    mock_cli_context, capsys
):
    """Value rows mark writable features and truncate long values to fit."""
    # Arrange: Provide a writable feature and a long read-only value.
    args = _cli_args("list-features", "--values")
    long_value = "x" * 90
    mock_cli_context.client.get_features.return_value = [
        build_feature(name="writable.feature", value=1.4, binding=_binding()),
        build_feature(name="long.feature", value=long_value),
    ]

    # Act: List the feature values as a table.
    assert await cmd_list_features(args) is True

    # Assert: The table shows the count, writable marks, and truncation.
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert "Found 2 Features for device DEV1:" in lines
    assert lines[1].startswith("* writable.feature ")
    assert lines[2].startswith("  long.feature ")
    assert lines[2].endswith("...")
    assert "(* = writable)" in lines


async def test_cmd_list_features_prints_one_name_per_line(mock_cli_context, capsys):
    """Without flags the listing is a device-scoped header plus one bullet per name."""
    # Arrange: Provide one feature and request no flags.
    args = _cli_args("list-features")
    mock_cli_context.client.get_features.return_value = [
        build_feature(name="f1", value=1)
    ]

    # Act: List the features without flags.
    assert await cmd_list_features(args) is True

    # Assert: The output prints the device-scoped feature name list.
    captured = capsys.readouterr()
    assert "Found 1 Features for device DEV1:" in captured.out
    assert "- f1" in captured.out


@pytest.mark.usefixtures("no_http_requests")
async def test_async_main_json_setup_error_keeps_stdout_empty(
    monkeypatch, capsys, caplog, tmp_path
):
    """A JSON-mode setup error must preserve stdout for a payload document."""
    # Arrange: Request live JSON output without saved, explicit, or
    # environment credentials.
    monkeypatch.delenv("VIESSMANN_CLIENT_ID", raising=False)
    monkeypatch.delenv("VIESSMANN_REDIRECT_URI", raising=False)

    # Act: Invoke the CLI entry path.
    exit_status = await async_main(
        ["list-features", "--json", "--token-file", str(tmp_path / "tokens.json")]
    )

    # Assert: The failed setup leaves stdout empty and logs the diagnostic.
    assert exit_status == 1
    assert capsys.readouterr().out == ""
    assert "Client ID not found." in caplog.text


async def test_cmd_list_devices_prints_account_hierarchy(mock_cli_context, capsys):
    """Listing devices should print installations, gateways, and devices."""
    # Arrange: Script one installation with one gateway and device.
    args = _cli_args("list-devices")
    mock_cli_context.client.get_installations.return_value = [build_installation("123")]
    mock_cli_context.client.get_gateways.return_value = [build_gateway("GW1", "123")]
    mock_cli_context.client.get_devices.return_value = [build_device("0", "123", "GW1")]

    # Act: List the account hierarchy.
    assert await cmd_list_devices(args) is True

    # Assert: The output formats each hierarchy level.
    captured = capsys.readouterr()
    assert "Found 1 installations" in captured.out
    assert "ID: 123" in captured.out
    assert "Found 1 gateways" in captured.out
    assert "Serial: GW1" in captured.out
    assert "Found 1 devices" in captured.out
    assert "ID: 0" in captured.out


async def test_cmd_list_devices_does_not_require_device_context(capsys, tmp_path):
    """Listing devices needs no installation, gateway, or device IDs."""
    # Arrange: Construct the real CLI context without any device-specific IDs.
    args = _cli_args(
        "list-devices",
        "--client-id",
        "test_id",
        "--redirect-uri",
        "http://localhost",
        "--token-file",
        str(tmp_path / "tokens.json"),
    )

    # The spec rejects calls to methods the real client does not offer.
    client = AsyncMock(spec=ViClient)
    client.get_installations.return_value = [build_installation("123")]
    client.get_gateways.return_value = [build_gateway("GW1", "123")]
    client.get_devices.return_value = [build_device("0", "123", "GW1")]

    with (
        patch("vi_api_client.cli.ViClient", return_value=client),
        patch("vi_api_client.cli.OAuth"),
        patch("vi_api_client.cli.create_session") as mock_session,
    ):
        mock_session.return_value.__aenter__.return_value = MagicMock()

        # Act: List all account devices without choosing a device context first.
        assert await cmd_list_devices(args) is True

        # Assert: The command queries the account hierarchy directly.
        client.get_installations.assert_awaited_once()
        client.get_gateways.assert_awaited_once()
        client.get_devices.assert_awaited_once_with("123", "GW1")

    assert "Found 1 installations" in capsys.readouterr().out


async def test_cmd_list_writable_prints_constraints(mock_cli_context, capsys):
    """Listing writable features should print command and constraint details."""
    # Arrange: Provide one writable feature with numeric constraints.
    args = _cli_args("list-writable")
    feature = build_feature(
        name="heating.circuits.0.heating.curve.slope",
        binding=_binding(),
        constraints=FeatureConstraints(min=0.2, max=3.5, step=0.1),
    )
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: List the writable features.
    assert await cmd_list_writable(args) is True

    # Assert: The output names the feature, command, and constraints.
    captured = capsys.readouterr()
    assert "heating.circuits.0.heating.curve.slope" in captured.out
    assert "setCurve" in captured.out
    assert "min: 0.2" in captured.out


async def test_cmd_list_writable_prints_string_and_enum_constraints(
    mock_cli_context, capsys
):
    """String and enum command constraints should print beside numeric ones."""
    # Arrange: Provide a writable feature with every constraint kind.
    args = _cli_args("list-writable")
    feature = build_feature(
        name="heating.program",
        binding=_binding(),
        constraints=FeatureConstraints(
            options=["auto", "manual"],
            min_length=1,
            max_length=10,
            pattern="^[a-z]+$",
        ),
    )
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: List the writable features.
    assert await cmd_list_writable(args) is True

    # Assert: Every constraint kind appears in the constraint line.
    captured = capsys.readouterr()
    assert "options: ('auto', 'manual')" in captured.out
    assert "min_length: 1" in captured.out
    assert "max_length: 10" in captured.out
    assert "pattern: ^[a-z]+$" in captured.out


async def test_cmd_list_writable_omits_absent_constraints(mock_cli_context, capsys):
    """Writable features without constraints should print without the line."""
    # Arrange: Provide a writable feature whose command declares no constraints.
    args = _cli_args("list-writable")
    feature = build_feature(name="heating.program", binding=_binding())
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: List the writable features.
    assert await cmd_list_writable(args) is True

    # Assert: The feature prints with its command but no constraint line.
    captured = capsys.readouterr()
    assert "heating.program" in captured.out
    assert "setCurve" in captured.out
    assert "Constraints:" not in captured.out


async def test_cmd_list_writable_prints_reported_schedule_rules(
    mock_cli_context, capsys
):
    """Schedule features should print only the schedule rules the API reports."""
    # Arrange: Provide a schedule control that reports some rules but not all.
    args = _cli_args("list-writable")
    feature = build_feature(
        name="heating.dhw.schedule",
        binding=_binding(command_name="setSchedule", parameter_name="newSchedule"),
        constraints=FeatureConstraints(
            schedule=ScheduleConstraints(max_entries=4, modes=["on"], resolution=10),
        ),
    )
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(feature)

    # Act: List the writable features.
    assert await cmd_list_writable(args) is True

    # Assert: Reported rules appear; unreported ones are left out.
    captured = capsys.readouterr()
    assert (
        "Schedule rules: max_entries: 4, modes: ('on',), resolution: 10" in captured.out
    )
    assert "overlap_allowed" not in captured.out
    assert "default_mode" not in captured.out


async def test_cmd_list_fixture_devices_prints_the_bundled_catalog(capsys):
    """Listing fixture devices should print the real offline catalog."""
    # Arrange: Read the shipped catalog the command is expected to list.
    args = _cli_args("list-fixture-devices")
    expected_devices = FixtureViClient.get_available_fixture_devices()

    # Act: List the fixture devices without mocking the catalog.
    assert await cmd_list_fixture_devices(args) is True

    # Assert: The output prints the header and every bundled device.
    captured = capsys.readouterr()
    assert "Available Fixture Devices:" in captured.out
    for device in expected_devices:
        assert f"- {device}" in captured.out
    assert "- Vitodens200W" in captured.out


async def test_dispatch_command_maps_handler_failure_to_status_one():
    """A handler's False result becomes the process failure status."""
    handler = AsyncMock(return_value=False)
    args = Namespace(command="set", handler=handler)

    assert await _dispatch_command(args) == 1
    handler.assert_awaited_once_with(args)


def test_main_exits_with_async_command_status():
    """The console entry point should expose the asynchronous exit status."""
    # Arrange: Let the asynchronous entry point report a failure status.
    with (
        patch(
            "vi_api_client.cli.async_main", new_callable=AsyncMock
        ) as mock_async_main,
        pytest.raises(SystemExit) as exit_error,
    ):
        mock_async_main.return_value = 1

        # Act: Run the console entry point.
        main()

    # Assert: The process exits with the reported status.
    assert exit_error.value.code == 1


async def test_async_main_without_command_prints_help(capsys):
    """Invoking the CLI without a command should print help and exit zero."""
    exit_status = await async_main([])

    assert exit_status == 0
    assert "usage:" in capsys.readouterr().out


async def test_async_main_maps_keyboard_interrupt_to_130():
    """Ctrl+C ends the CLI with the shell's conventional SIGINT status."""
    # Arrange: Interrupt the dispatcher while it runs a command.
    with patch(
        "vi_api_client.cli._dispatch_command", new_callable=AsyncMock
    ) as mock_dispatch:
        mock_dispatch.side_effect = KeyboardInterrupt

        # Act: Invoke the CLI entry path.
        exit_status = await async_main(["list-fixture-devices"])

    # Assert: The interrupt maps to the conventional terminal status.
    assert exit_status == 130


async def test_async_main_maps_unexpected_errors_to_one():
    """Errors escaping the dispatcher end with the generic failure status."""
    # Arrange: Fail the dispatcher with an unexpected error.
    with patch(
        "vi_api_client.cli._dispatch_command", new_callable=AsyncMock
    ) as mock_dispatch:
        mock_dispatch.side_effect = RuntimeError("boom")

        # Act: Invoke the CLI entry path.
        exit_status = await async_main(["list-fixture-devices"])

    # Assert: The unexpected failure maps to a generic failure status.
    assert exit_status == 1


@pytest.mark.parametrize(
    ("extra_argv", "expected_level"),
    [
        pytest.param([], logging.INFO, id="default"),
        pytest.param(["--verbose"], logging.DEBUG, id="verbose"),
    ],
)
async def test_async_main_configures_logging_after_parsing(
    monkeypatch, extra_argv, expected_level
):
    """The CLI should configure root logging itself, honoring the --verbose flag."""
    # Arrange: Request a fixture listing with or without verbose logging.
    configured_levels = []
    monkeypatch.setattr(
        logging,
        "basicConfig",
        lambda **kwargs: configured_levels.append(kwargs.get("level")),
    )

    # Act: Invoke the parser and dispatcher through the CLI entry path.
    exit_status = await async_main(["list-fixture-devices", *extra_argv])

    # Assert: Logging is configured once with the requested level.
    assert exit_status == 0
    assert configured_levels == [expected_level]


def test_importing_cli_leaves_root_logging_unconfigured():
    """Importing the CLI module should not touch root logging configuration."""
    # Arrange: Probe the import side effect in a fresh interpreter.
    probe = (
        "import logging, vi_api_client.cli; print(len(logging.getLogger().handlers))"
    )

    # Act: Import the CLI in a clean process.
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )

    # Assert: No handler is installed on the root logger at import time.
    assert result.stdout.strip() == "0"


async def test_cmd_list_events_auto_selects_first_installation(
    mock_cli_context, capsys
):
    """Without an installation ID the first account installation is used."""
    # Arrange: Leave the installation scope unresolved in the context.
    args = _cli_args("list-events", "--days", "7")
    mock_cli_context.installation_id = None
    mock_cli_context.client.get_installations.return_value = [
        build_installation("12345")
    ]
    mock_cli_context.client.get_event_history_window.return_value = (
        _empty_event_window()
    )

    # Act: List events without an explicit installation ID.
    assert await cmd_list_events(args) is True

    # Assert: The first installation scopes the event history request.
    mock_cli_context.client.get_event_history_window.assert_awaited_once_with(
        "12345", 7, limit=None, max_pages=50
    )


async def test_cmd_list_events_json_reports_auto_selection_on_stderr(
    mock_cli_context, capsys
):
    """The auto-selected installation must not contaminate the JSON document."""
    # Arrange: Omit the installation ID and request JSON output.
    args = _cli_args("list-events", "--days", "7", "--json")
    mock_cli_context.installation_id = None
    mock_cli_context.client.get_installations.return_value = [
        build_installation("12345")
    ]
    mock_cli_context.client.get_event_history_window.return_value = (
        _empty_event_window()
    )

    # Act: List events in JSON mode.
    assert await cmd_list_events(args) is True

    # Assert: stdout holds only the document; the diagnostic goes to stderr.
    captured = capsys.readouterr()
    assert json.loads(captured.out)["installationId"] == "12345"
    assert "Auto-selected installation: 12345" in captured.err


async def test_cmd_list_events_fails_without_installations(mock_cli_context, caplog):
    """An account without installations has no event history to list."""
    # Arrange: Omit the installation ID and return no installations.
    args = _cli_args("list-events", "--days", "7")
    mock_cli_context.installation_id = None
    mock_cli_context.client.get_installations.return_value = []

    # Act: List events for the empty account.
    result = await cmd_list_events(args)

    # Assert: The command fails with the reason and reads no history.
    assert result is False
    assert "No installations found." in caplog.text
    mock_cli_context.client.get_event_history_window.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(["--days", "0"], id="zero-days"),
        pytest.param(["--days", "-2"], id="negative-days"),
        pytest.param(["--days", "seven"], id="text-days"),
        pytest.param(["--days", "7", "--limit", "0"], id="zero-limit"),
        pytest.param(["--days", "7", "--max-pages", "-1"], id="negative-pages"),
    ],
)
def test_list_events_parser_rejects_non_positive_windows(arguments, capsys):
    """Non-positive windows and limits should fail before any command runs."""
    # Act: Parse an invalid rolling window or page limit.
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["list-events", *arguments])

    # Assert: argparse reports a usage error on stderr only.
    captured = capsys.readouterr()
    assert error.value.code == 2
    assert captured.out == ""
    assert "is not a positive integer" in captured.err


def test_list_events_parser_requires_days(capsys):
    """Event history has no implicit window, so omitting --days is a usage error."""
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(["list-events"])

    assert error.value.code == 2
    assert "--days" in capsys.readouterr().err


def test_parser_provides_shared_defaults_for_every_command():
    """Commands without target or JSON options still expose their defaults."""
    # Act: Parse a command that defines neither option.
    args = build_parser().parse_args(["list-devices"])

    # Assert: Shared readers see absent values instead of missing attributes.
    assert (args.installation_id, args.gateway_serial, args.device_id) == (
        None,
        None,
        None,
    )
    assert args.json is False


def test_parser_keeps_installation_ids_as_text():
    """Installation IDs should keep the API's string form."""
    args = build_parser().parse_args(["list-features", "--installation-id", "123"])

    assert args.installation_id == "123"


async def test_list_events_json_emits_complete_events_across_pages(run_cli):
    """JSON output is one document with every event and the pagination state."""
    # Act: List the two-page fixture history as JSON.
    exit_status, out, err = await run_cli(
        "list-events", "--fixture-device", "Vitodens200W", "--days", "7", "--json"
    )

    # Assert: The document keeps the provider events unchanged, follows the
    # cursor to the final page, and leaves diagnostics on stderr.
    assert exit_status == 0
    assert json.loads(out) == {
        "installationId": "99999",
        "events": load_fixture_device("event_history")["data"],
        "eventCount": 3,
        "earliestEventTimestamp": "2026-09-18T08:02:10.500Z",
        "latestEventTimestamp": "2026-09-20T10:15:30.000Z",
        "pagesFetched": 2,
        "paginationComplete": True,
        "nextCursor": None,
    }
    assert "Using Fixture Device: Vitodens200W" in err


def _empty_event_window() -> EventHistoryWindow:
    """Return the window the client collects when the provider has no events."""
    return EventHistoryWindow(events=[], next_cursor=None, pages_fetched=1)


async def test_cmd_list_events_summarizes_an_empty_window(mock_cli_context, capsys):
    """An empty window completes and prints no event timestamps."""
    # Arrange: The provider returns no events for a one-year window.
    args = _cli_args("list-events", "--days", "365")
    mock_cli_context.client.get_event_history_window.return_value = (
        _empty_event_window()
    )

    # Act: List the empty window as readable text.
    assert await cmd_list_events(args) is True

    # Assert: The summary names the empty window without timestamps.
    out = capsys.readouterr().out
    assert "Found 0 event(s) for installation 99 (last 365 days):" in out
    assert "Earliest event:" not in out
    assert "Pagination completed after 1 page(s)" in out


async def test_cmd_list_events_json_describes_an_empty_window(mock_cli_context, capsys):
    """An empty window is one complete JSON document with null timestamps."""
    # Arrange: The provider returns no events for a one-year window.
    args = _cli_args("list-events", "--days", "365", "--json")
    mock_cli_context.client.get_event_history_window.return_value = (
        _empty_event_window()
    )

    # Act: List the empty window as JSON.
    assert await cmd_list_events(args) is True

    # Assert: The document is complete and has no event timestamps.
    assert json.loads(capsys.readouterr().out) == {
        "installationId": "99",
        "events": [],
        "eventCount": 0,
        "earliestEventTimestamp": None,
        "latestEventTimestamp": None,
        "pagesFetched": 1,
        "paginationComplete": True,
        "nextCursor": None,
    }


async def test_list_events_json_marks_the_safety_limit(run_cli):
    """A remaining cursor at the page limit marks the JSON result incomplete."""
    # Act: List the two-page fixture history with a one page limit.
    exit_status, out, _ = await run_cli(
        "list-events",
        "--fixture-device",
        "Vitodens200W",
        "--days",
        "7",
        "--max-pages",
        "1",
        "--json",
    )

    # Assert: The document reports the stop and the cursor to continue from.
    assert exit_status == 0
    document = json.loads(out)
    assert document["eventCount"] == 3
    assert document["pagesFetched"] == 1
    assert document["paginationComplete"] is False
    assert document["nextCursor"] == "b3BhcXVlLWN1cnNvci10b2tlbg=="


async def test_list_events_prints_readable_summary(run_cli):
    """The readable summary groups events by UTC date without loss."""
    # Act: List the two-page fixture history as text.
    exit_status, out, _ = await run_cli(
        "list-events", "--fixture-device", "Vitodens200W", "--days", "7"
    )

    # Assert: Events group by UTC date with complete details; a single gateway
    # adds no serial label.
    assert exit_status == 0
    lines = out.splitlines()
    assert "Found 3 event(s) for installation 99999 (last 7 days):" in lines
    assert lines[lines.index("2026-09-20 (UTC)") + 1] == (
        "- 10:15:30 UTC feature-changed"
    )
    assert "    heating.dhw.temperature.main" in lines
    assert "    command: setTargetTemperature" in lines
    assert '    parameters: {"temperature": 55}' in lines
    assert "- 22:41:03 UTC gateway.disconnected" in lines
    assert "    body: null" in lines
    assert "- 08:02:10 UTC device.error.raised" in lines
    assert (
        "Earliest event: 2026-09-18T08:02:10.500Z; "
        "latest event: 2026-09-20T10:15:30.000Z" in lines
    )
    assert "Pagination completed after 2 page(s)" in out
    assert all(len(line) <= _EVENT_LINE_WIDTH for line in lines)
    assert "..." not in out
    assert "1234567890123456" not in out


async def test_list_events_readable_output_marks_the_safety_limit(run_cli):
    """Readable output names the remaining cursor of a truncated history."""
    # Act: List the two-page fixture history with a one page limit.
    exit_status, out, _ = await run_cli(
        "list-events",
        "--fixture-device",
        "Vitodens200W",
        "--days",
        "7",
        "--max-pages",
        "1",
    )

    # Assert: The summary reports the stop instead of claiming completion.
    assert exit_status == 0
    assert "Found 3 event(s) for installation 99999 (last 7 days):" in out
    assert (
        "Stopped at the safety limit of 1 page(s); more events may be "
        "available (next cursor: b3BhcXVlLWN1cnNvci10b2tlbg==)"
    ) in out
    assert "Pagination completed" not in out


def _detail_event(body: FeatureValue, event_type: str = "detail") -> InstallationEvent:
    """Build one event carrying only the body under test."""
    return InstallationEvent(
        event_type=event_type,
        created_at="2026-09-20T10:15:30.000Z",
        event_timestamp="2026-09-20T10:15:30.000Z",
        gateway_serial=None,
        body=body,
        fields={},
    )


@pytest.mark.parametrize(
    ("event_type", "body", "expected"),
    [
        pytest.param("detail", None, ["body: null"], id="null"),
        pytest.param("detail", "plain text", ['body: "plain text"'], id="text"),
        pytest.param("detail", 42, ["body: 42"], id="number"),
        pytest.param("detail", ["a", "b"], ['body: ["a", "b"]'], id="list"),
        pytest.param(
            "detail", {"online": True}, ['body: {"online": true}'], id="object"
        ),
        pytest.param(
            "detail",
            {"featureName": "heating.dhw.temperature.main", "reason": "user"},
            ['body: {"featureName": "heating.dhw.temperature.main", "reason": "user"}'],
            id="object-with-feature-name",
        ),
        pytest.param(
            "feature-changed",
            {"featureName": "heating.dhw.temperature.main"},
            ["heating.dhw.temperature.main"],
            id="feature-changed-name",
        ),
        pytest.param(
            "feature-changed",
            {
                "featureName": "heating.dhw.temperature.main",
                "commandName": "setTargetTemperature",
            },
            ["heating.dhw.temperature.main", "command: setTargetTemperature"],
            id="feature-changed-command",
        ),
        pytest.param(
            "feature-changed",
            {
                "featureName": "heating.dhw.temperature.main",
                "commandBody": {"temperature": 55},
            },
            ["heating.dhw.temperature.main", 'parameters: {"temperature": 55}'],
            id="feature-changed-parameters",
        ),
        pytest.param(
            "feature-changed",
            {"commandName": "setTargetTemperature"},
            ['body: {"commandName": "setTargetTemperature"}'],
            id="feature-changed-without-name",
        ),
    ],
)
def test_event_detail_lines_render_known_and_unknown_bodies(
    event_type: str, body: FeatureValue, expected: list[str]
):
    """Detail lines should use known event types and complete JSON otherwise."""
    details = _event_detail_lines(_detail_event(body, event_type))

    assert details == expected


@pytest.mark.parametrize(
    ("online", "label"),
    [
        pytest.param(True, "ONLINE", id="online"),
        pytest.param(False, "OFFLINE", id="offline"),
    ],
)
def test_event_detail_lines_labels_gateway_online_events(online: bool, label: str):
    """Gateway-online events should render the reported online transition."""
    event = replace(
        _detail_event(None), event_type="gateway-online", body={"online": online}
    )

    assert _event_detail_lines(event) == [label]


def test_event_detail_lines_does_not_infer_gateway_state():
    """A non-boolean online flag must not imply an online or offline state."""
    event = replace(_detail_event({"online": "yes"}), event_type="gateway-online")

    assert _event_detail_lines(event) == ['body: {"online": "yes"}']


def _status_event(body: FeatureValue) -> InstallationEvent:
    """Build one device-message-status event carrying the body under test."""
    return replace(_detail_event(body), event_type="device-message-status")


def test_event_detail_lines_renders_active_status_transition():
    """An active status event should show code, transition, and identifiers."""
    # Arrange: One anonymized activation event with a meaningful description.
    body: FeatureValue = {
        "errorCode": "S.134",
        "active": True,
        "deviceId": "17",
        "modelId": "67",
        "equipmentType": "Vitodens 200-W",
        "errorDescription": "Burner fault",
    }

    # Act and assert: Every supplied field is rendered without loss.
    assert _event_detail_lines(_status_event(body)) == [
        "code: S.134",
        "ACTIVE",
        "device: 17, model: 67",
        "equipment type: Vitodens 200-W",
        "description: Burner fault",
    ]


def test_event_detail_lines_renders_ended_status_transition():
    """An ended status event should report the ENDED transition."""
    # Arrange: One anonymized end-of-status event.
    body: FeatureValue = {
        "errorCode": "S.134",
        "active": False,
        "deviceId": "17",
        "modelId": "67",
    }

    # Act and assert: The boolean transition is rendered as ENDED.
    assert _event_detail_lines(_status_event(body)) == [
        "code: S.134",
        "ENDED",
        "device: 17, model: 67",
    ]


@pytest.mark.parametrize(
    ("error_description", "expected"),
    [
        pytest.param("S.134", [], id="same-code"),
        pytest.param("s.134", [], id="same-code-lowercase"),
        pytest.param("", [], id="empty"),
        pytest.param(None, [], id="missing"),
        pytest.param(
            "S.134 Burner fault", ["description: S.134 Burner fault"], id="informative"
        ),
    ],
)
def test_event_detail_lines_shows_descriptions_beyond_the_code(
    error_description: str | None, expected: list[str]
):
    """Only descriptions adding information beyond the code should appear."""
    # Arrange: One status event with the description under test.
    body: FeatureValue = {
        "errorCode": "S.134",
        "active": True,
        "errorDescription": error_description,
    }

    # Act: Render the status event body.
    details = _event_detail_lines(_status_event(body))

    # Assert: Repeated, empty, and missing descriptions are omitted while
    # informative ones survive.
    assert [d for d in details if d.startswith("description:")] == expected


def test_event_detail_lines_keeps_description_without_code():
    """A description without a code is informative on its own."""
    body: FeatureValue = {"errorDescription": "Burner fault"}

    assert _event_detail_lines(_status_event(body)) == ["description: Burner fault"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param({"active": True}, ["ACTIVE"], id="active-only"),
        pytest.param({"errorCode": "S.134"}, ["code: S.134"], id="code-only"),
        pytest.param(
            {"deviceId": "17", "modelId": "67"},
            ["device: 17, model: 67"],
            id="device-and-model",
        ),
        pytest.param({"deviceId": "17"}, ["device: 17"], id="device-only"),
        pytest.param({}, ["body: {}"], id="empty-object"),
        pytest.param("scalar", ['body: "scalar"'], id="scalar"),
    ],
)
def test_event_detail_lines_handles_missing_status_fields_without_fabrication(
    body: FeatureValue, expected: list[str]
):
    """Sparse status bodies render only supplied fields; other bodies stay raw JSON."""
    details = _event_detail_lines(_status_event(body))

    assert details == expected


def test_print_event_summary_wraps_long_details_without_truncation(capsys):
    """Long details should wrap onto indented continuation lines."""
    # Arrange: Provide one event with a feature name beyond the line width.
    feature_name = "x" * 200
    event = _detail_event({"featureName": feature_name}, "feature-changed")
    window = EventHistoryWindow(events=[event], next_cursor=None, pages_fetched=1)

    # Act: Print the readable summary of the single event.
    _print_event_summary(window, "99", 7, 50)

    # Assert: The complete name survives across wrapped lines.
    output_lines = capsys.readouterr().out.splitlines()
    detail_lines = [line for line in output_lines if line.startswith("    ")]
    assert "".join(line.strip() for line in detail_lines) == feature_name
    assert all(len(line) <= 120 for line in detail_lines)
    assert detail_lines[0].startswith("    x")
    assert detail_lines[1].startswith("      x")
    assert "..." not in "\n".join(output_lines)


def _rendered_detail(output: str, detail_prefix: str) -> str:
    """Return one wrapped detail's content with indents removed exactly.

    Continuation lines lose exactly the six space indent, so whitespace
    inside the wrapped content survives the reconstruction.
    """
    lines = output.splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.startswith(detail_prefix)
    )
    end = start + 1
    while end < len(lines) and lines[end].startswith("      "):
        end += 1
    assert all(len(line) <= _EVENT_LINE_WIDTH for line in lines[start:end])
    rendered = lines[start][len(detail_prefix) :]
    return rendered + "".join(line[len("      ") :] for line in lines[start + 1 : end])


def test_print_event_summary_keeps_long_nested_parameters_visible(capsys):
    """Complete nested command parameters should survive wrapping."""
    # Arrange: One feature change with a long, nested command body.
    command_body = {
        "schedule": [
            {"start": "2026-09-20T05:30:00.000Z", "end": "2026-09-20T22:00:00.000Z"}
        ],
        "note": "n" * 60,
    }
    event = _detail_event(
        {
            "featureName": "heating.dhw.schedule",
            "commandName": "setSchedule",
            "commandBody": command_body,
        },
        "feature-changed",
    )
    window = EventHistoryWindow(events=[event], next_cursor=None, pages_fetched=1)

    # Act: Print the readable summary of the single event.
    _print_event_summary(window, "99", 7, 50)

    # Assert: The wrapped parameters reconstruct the exact command body.
    rendered = _rendered_detail(capsys.readouterr().out, "    parameters: ")
    assert json.loads(rendered) == command_body
    assert len(rendered) + len("    parameters: ") > _EVENT_LINE_WIDTH


def test_print_event_summary_preserves_spaces_in_wrapped_parameters(capsys):
    """Wrapping must not drop spaces inside parameter values."""
    # Arrange: One feature change whose parameter value contains repeated
    # double spaces that span the wrap boundaries.
    command_body: FeatureValue = {"note": "word  " * 20}
    event = _detail_event(
        {"featureName": "heating.dhw.notes", "commandBody": command_body},
        "feature-changed",
    )
    window = EventHistoryWindow(events=[event], next_cursor=None, pages_fetched=1)

    # Act: Print the readable summary of the single event.
    _print_event_summary(window, "99", 7, 50)

    # Assert: The wrapped parameters reconstruct the exact value with every
    # interior space preserved.
    rendered = _rendered_detail(capsys.readouterr().out, "    parameters: ")
    assert json.loads(rendered) == command_body


def test_print_event_summary_prints_utc_date_headings(capsys):
    """Date headings should follow UTC while the returned order is kept."""
    # Arrange: The +02:00 timestamp is 2026-09-19 22:30:00 UTC, so its raw
    # date and its UTC heading differ.
    offset_event = replace(
        _detail_event(None), event_timestamp="2026-09-20T00:30:00.000+02:00"
    )
    first = replace(_detail_event(None), event_timestamp="2026-09-19T12:00:00.000Z")
    second = replace(_detail_event(None), event_timestamp="2026-09-18T09:00:00.000Z")
    window = EventHistoryWindow(
        events=[first, offset_event, second], next_cursor=None, pages_fetched=1
    )

    # Act: Print the readable summary of the mixed-offset window.
    _print_event_summary(window, "99", 7, 50)

    # Assert: Headings appear in first-seen order with UTC-converted times.
    lines = capsys.readouterr().out.splitlines()
    first_heading = lines.index("2026-09-19 (UTC)")
    second_heading = lines.index("2026-09-18 (UTC)")
    first_event = lines.index("- 12:00:00 UTC detail")
    offset_time_event = lines.index("- 22:30:00 UTC detail")
    second_event = lines.index("- 09:00:00 UTC detail")
    assert (
        first_heading < first_event < offset_time_event < second_heading < second_event
    )


def test_print_event_summary_preserves_event_order_across_date_changes(capsys):
    """A heading is printed at every date change instead of grouping by date."""
    # Arrange: The provider returns one 27th event, one 26th event, and
    # another 27th event.
    first = replace(_detail_event(None), event_timestamp="2026-09-27T10:00:00.000Z")
    second = replace(_detail_event(None), event_timestamp="2026-09-26T10:00:00.000Z")
    third = replace(_detail_event(None), event_timestamp="2026-09-27T11:00:00.000Z")
    window = EventHistoryWindow(
        events=[first, second, third], next_cursor=None, pages_fetched=1
    )

    # Act: Print the readable summary of the interleaved window.
    _print_event_summary(window, "99", 7, 50)

    # Assert: A heading is printed at each date change and events keep
    # their returned order.
    relevant = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.endswith("(UTC)") or line.startswith("- ")
    ]
    assert relevant == [
        "2026-09-27 (UTC)",
        "- 10:00:00 UTC detail",
        "2026-09-26 (UTC)",
        "- 10:00:00 UTC detail",
        "2026-09-27 (UTC)",
        "- 11:00:00 UTC detail",
    ]


def test_print_event_summary_represents_missing_timestamps_without_invention(capsys):
    """Undatable events group under 'Unknown date' and keep their raw timestamp."""
    # Arrange: One event carries an unparsable timestamp and one a missing
    # timestamp, both built directly because the API parser rejects them.
    unparsable = replace(_detail_event(None), event_timestamp="not-a-timestamp")
    missing = replace(_detail_event(None), event_timestamp="")
    window = EventHistoryWindow(
        events=[unparsable, missing], next_cursor=None, pages_fetched=1
    )

    # Act: Print the readable summary of the undatable window.
    _print_event_summary(window, "99", 7, 50)

    # Assert: No date or time is invented for either event.
    lines = capsys.readouterr().out.splitlines()
    assert "Unknown date" in lines
    assert "- not-a-timestamp detail" in lines
    assert "- time unknown detail" in lines


def test_print_event_summary_marks_safety_limit_results(capsys):
    """A remaining cursor should mark the readable result incomplete."""
    # Arrange: One event window that stopped at the page safety limit.
    event = _detail_event(
        {"featureName": "heating.dhw.temperature.main"}, "feature-changed"
    )
    window = EventHistoryWindow(
        events=[event], next_cursor="cursor-token", pages_fetched=50
    )

    # Act: Print the readable summary of the limited traversal.
    _print_event_summary(window, "99", 7, 50)

    # Assert: The limit, cursor, and extremes stay visible.
    output = capsys.readouterr().out
    assert (
        "Stopped at the safety limit of 50 page(s); more events may be "
        "available (next cursor: cursor-token)" in output
    )
    assert "Pagination completed" not in output
    assert "Earliest event:" in output


@pytest.mark.parametrize(
    ("early", "late"),
    [
        # 09:15:30+02:00 is 07:15:30Z, so it sorts later as text only.
        ("2026-09-20T09:15:30.000+02:00", "2026-09-20T08:15:30.000Z"),
        # A timestamp without offset counts as UTC: 08:00 follows 07:30Z.
        ("2026-09-20T09:30:00+02:00", "2026-09-20T08:00:00"),
    ],
    ids=["utc-offset", "no-offset-as-utc"],
)
def test_event_history_window_compares_timestamps_as_points_in_time(
    early: str, late: str
):
    """Earliest and latest should order by instant, not lexically."""
    # Arrange: The two timestamps sort the other way round as text.
    window = EventHistoryWindow(
        events=[
            replace(_detail_event(None), event_timestamp=late),
            replace(_detail_event(None), event_timestamp=early),
        ],
        next_cursor=None,
        pages_fetched=1,
    )

    # Act and assert: The window orders the timestamps by instant.
    assert (window.earliest_timestamp, window.latest_timestamp) == (early, late)


def test_event_history_window_falls_back_to_lexical_for_unparsable_timestamps():
    """Unparsable timestamps should fall back to a lexical comparison."""
    # Arrange: One provider timestamp is not parsable ISO-8601.
    parsable = replace(_detail_event(None), event_timestamp="2026-09-20T10:15:30.000Z")
    unparsable = replace(_detail_event(None), event_timestamp="not-a-timestamp")
    window = EventHistoryWindow(
        events=[parsable, unparsable], next_cursor=None, pages_fetched=1
    )

    # Act: Read the window's extremes.
    extremes = (window.earliest_timestamp, window.latest_timestamp)

    # Assert: The whole window uses the lexical fallback ordering.
    assert extremes == ("2026-09-20T10:15:30.000Z", "not-a-timestamp")


def test_print_event_summary_labels_events_from_multiple_gateways(capsys):
    """Events from several gateways should stay attributable."""
    # Arrange: Build one window with events from two gateways and one event
    # without a reported gateway.
    first = replace(
        _detail_event(
            {"featureName": "heating.dhw.temperature.main"}, "feature-changed"
        ),
        gateway_serial="1234567890123456",
    )
    second = replace(
        _detail_event({"featureName": "heating.dhw.oneTimeCharge"}, "feature-changed"),
        gateway_serial="9876543210987654",
    )
    third = replace(
        _detail_event({"featureName": "heating.dhw.schedule"}, "feature-changed"),
        gateway_serial=None,
    )
    window = EventHistoryWindow(
        events=[first, second, third], next_cursor=None, pages_fetched=1
    )

    # Act: Print the readable summary of the mixed-gateway window.
    _print_event_summary(window, "99", 7, 50)

    # Assert: Every event names its gateway, or explicitly none.
    output = capsys.readouterr().out
    assert "- 10:15:30 UTC feature-changed gateway 1234567890123456" in output
    assert "- 10:15:30 UTC feature-changed gateway 9876543210987654" in output
    assert "- 10:15:30 UTC feature-changed gateway unknown" in output


async def test_run_cli_get_feature_shows_both_commands_of_a_pair(run_cli):
    """get-feature names the on and the off command of a pair-written feature."""
    # Act: Read a fixture feature that only a command pair writes.
    exit_status, out, _ = await run_cli(
        "get-feature",
        "--fixture-device",
        "Vitocal222S",
        "heating.dhw.hygiene.enabled",
    )

    # Assert: Both commands are named, and no parameter is shown.
    assert exit_status == 0
    assert "  Writable via commands: enable (on), disable (off)" in out
    assert "Target param" not in out


async def test_run_cli_get_feature_json_prints_the_pair_commands(run_cli):
    """get-feature --json prints a pair-written feature's on and off command."""
    # Act: Read a pair-written fixture feature as JSON.
    exit_status, out, _ = await run_cli(
        "get-feature",
        "--fixture-device",
        "Vitocal222S",
        "--json",
        "heating.dhw.hygiene.enabled",
    )

    # Assert: The command entry names both commands and the boolean rule.
    assert exit_status == 0
    document = json.loads(out)
    assert document["command"] == {"on": "enable", "off": "disable"}
    assert document["constraints"]["value_type"] == "boolean"


async def test_run_cli_list_writable_shows_both_commands_of_a_pair(run_cli):
    """list-writable names the on and the off command of a pair-written feature."""
    # Act: List the writable features of a fixture with a pair-written one.
    exit_status, out, _ = await run_cli(
        "list-writable", "--fixture-device", "Vitocal222S"
    )

    # Assert: The pair-written feature lists both commands.
    assert exit_status == 0
    assert (
        "- heating.dhw.hygiene.enabled\n    Commands: enable (on), disable (off)" in out
    )


@pytest.mark.parametrize(
    "raw_value",
    [pytest.param("true", id="on"), pytest.param("false", id="off")],
)
async def test_run_cli_set_writes_a_pair_written_feature(run_cli, raw_value):
    """set accepts true and false for a pair-written feature."""
    # Act: Write the fixture hygiene function through the CLI.
    exit_status, out, _ = await run_cli(
        "set",
        "--fixture-device",
        "Vitocal222S",
        "heating.dhw.hygiene.enabled",
        raw_value,
    )

    # Assert: The write names both commands and succeeds.
    assert exit_status == 0
    assert "(Commands: enable (on), disable (off))" in out
    assert "✅ Success!" in out


async def test_run_cli_list_writable_omits_features_without_a_setter(run_cli):
    """list-writable lists no feature that only a non-setter command takes."""
    # Act: List the writable features of a fixture with holiday, daylight
    # saving, and hygiene trigger commands.
    exit_status, out, _ = await run_cli(
        "list-writable", "--fixture-device", "Vitodens200W"
    )

    # Assert: The features those commands take are not listed.
    assert exit_status == 0
    listed = {line[2:] for line in out.splitlines() if line.startswith("- ")}
    assert "heating.circuits.0.heating.curve.slope" in listed
    assert listed.isdisjoint(
        {
            "device.time.daylightSaving.begin",
            "device.time.daylightSaving.end",
            "heating.dhw.hygiene.trigger.startHour",
            "heating.dhw.hygiene.trigger.startMinute",
            "heating.operating.programs.holiday.start",
            "heating.operating.programs.holiday.end",
            "heating.operating.programs.holidayAtHome.start",
            "heating.operating.programs.holidayAtHome.end",
        }
    )


async def test_run_cli_exec_schedules_a_holiday(run_cli):
    """exec plans a holiday through the schedule command."""
    # Act: Plan a fixture holiday through the CLI.
    exit_status, out, _ = await run_cli(
        "exec",
        "--fixture-device",
        "Vitodens200W",
        "heating.operating.programs.holiday",
        "schedule",
        "start=2026-12-24",
        "end=2026-12-31",
    )

    # Assert: The command runs with both dates.
    assert exit_status == 0
    assert "Executing 'schedule' on heating.operating.programs.holiday..." in out
    assert "Using parameters: {'start': '2026-12-24', 'end': '2026-12-31'}" in out
    assert "✅ Success!" in out


async def test_run_cli_set_points_a_non_setter_feature_to_exec(run_cli):
    """set on a feature without a setter names the exec alternative."""
    # Act: Try to set a holiday date through the CLI.
    exit_status, out, _ = await run_cli(
        "set",
        "--fixture-device",
        "Vitodens200W",
        "heating.operating.programs.holiday.start",
        "2026-12-24",
    )

    # Assert: The failure names the API feature's commands and exec.
    assert exit_status == 1
    assert "Feature 'heating.operating.programs.holiday.start' is read-only" in out
    assert "reports the commands changeEndDate, schedule, unschedule" in out
    assert "vi-client exec heating.operating.programs.holiday" in out


def _recorded_deprecated_features(api_feature_name: str) -> list[Feature]:
    """Parse one recorded API feature of the deprecation test responses."""
    return next(
        api_feature_to_flat_features(api_feature)
        for api_feature in load_fixture_json("deprecated_features.json")
        if api_feature["feature"] == api_feature_name
    )


@pytest.mark.parametrize(
    ("api_feature_name", "expected_line"),
    [
        pytest.param(
            "heating.dhw.sensors.temperature.hotWaterStorage",
            "  Deprecated: removal announced for 2024-09-15 "
            "(replaced by heating.dhw.sensors.temperature.dhwCylinder)",
            id="deprecated",
        ),
        pytest.param("heating.dhw.sensors.temperature.dhwCylinder", None, id="regular"),
    ],
)
async def test_run_cli_get_feature_prints_the_removal_notice(
    run_cli, mock_cli_context, api_feature_name, expected_line
):
    """get-feature prints the removal date and info text of a deprecated feature."""
    # Arrange: Serve one recorded API feature for the selection and the
    # snapshot read.
    features = _recorded_deprecated_features(api_feature_name)
    mock_cli_context.client.get_features.return_value = features
    mock_cli_context.client.refresh_device.return_value = _snapshot_device(*features)

    # Act: Read the feature through the full CLI dispatch.
    exit_status, out, _ = await run_cli("get-feature", api_feature_name)

    # Assert: Only a deprecated feature shows the notice line, once per
    # flat feature.
    assert exit_status == 0
    if expected_line is None:
        assert "Deprecated" not in out
    else:
        assert out.count(expected_line) == len(features)


@pytest.mark.parametrize(
    ("api_feature_name", "expected_notice"),
    [
        pytest.param(
            "heating.dhw.temperature.levels",
            {"removal_date": "2027-03-31", "info": "Not used anymore"},
            id="deprecated",
        ),
        pytest.param("heating.dhw.sensors.temperature.dhwCylinder", None, id="regular"),
    ],
)
async def test_run_cli_get_feature_json_prints_the_removal_notice(
    run_cli, mock_cli_context, api_feature_name, expected_notice
):
    """get-feature --json carries the deprecation key for every feature."""
    # Arrange: Serve one recorded API feature for the selection.
    features = _recorded_deprecated_features(api_feature_name)
    mock_cli_context.client.get_features.return_value = features

    # Act: Read the feature as JSON through the full CLI dispatch.
    exit_status, out, _ = await run_cli("get-feature", "--json", api_feature_name)

    # Assert: Every document carries the notice with an ISO date, or null.
    assert exit_status == 0
    documents = json.loads(out)
    assert len(documents) == len(features)
    assert [document["deprecation"] for document in documents] == [
        expected_notice
    ] * len(features)


def test_removal_notice_text_handles_missing_fields():
    """A notice without a date or info still prints one readable line."""
    assert _deprecation_text(FeatureDeprecation(None, "Not used anymore")) == (
        "removal announced without a date (Not used anymore)"
    )
    assert _deprecation_text(FeatureDeprecation(date(2027, 3, 31), None)) == (
        "removal announced for 2027-03-31"
    )
    assert _deprecation_document(FeatureDeprecation(None, None)) == {
        "removal_date": None,
        "info": None,
    }


def _all_recorded_deprecation_features() -> list[Feature]:
    """Parse every recorded API feature of the deprecation test responses.

    Two API features carry a notice and one, the replacement, carries none.
    """
    return [
        feature
        for api_feature in load_fixture_json("deprecated_features.json")
        for feature in api_feature_to_flat_features(api_feature)
    ]


async def test_run_cli_list_features_deprecated_lists_only_notices(
    run_cli, mock_cli_context
):
    """list-features --deprecated lists each deprecated feature with its notice."""
    # Arrange: Serve two deprecated API features and their replacement.
    mock_cli_context.client.get_features.return_value = (
        _all_recorded_deprecation_features()
    )

    # Act: List the deprecated features.
    exit_status, out, _ = await run_cli("list-features", "--deprecated")

    # Assert: Only features with a notice are listed, the header counts
    # them, and each line ends with the removal date and the info text.
    assert exit_status == 0
    assert out.splitlines() == [
        "Found 5 Features for device DEV1:",
        "- heating.dhw.sensors.temperature.hotWaterStorage (removal 2024-09-15: "
        "replaced by heating.dhw.sensors.temperature.dhwCylinder)",
        "- heating.dhw.sensors.temperature.hotWaterStorage.status (removal "
        "2024-09-15: replaced by heating.dhw.sensors.temperature.dhwCylinder)",
        "- heating.dhw.temperature.levels.min (removal 2027-03-31: Not used anymore)",
        "- heating.dhw.temperature.levels.max (removal 2027-03-31: Not used anymore)",
        "- heating.dhw.temperature.levels.default (removal 2027-03-31: "
        "Not used anymore)",
    ]


async def test_run_cli_list_features_deprecated_values_shows_value_and_notice(
    run_cli, mock_cli_context
):
    """list-features --deprecated --values appends the notice to the value."""
    # Arrange: Serve two deprecated API features and their replacement.
    mock_cli_context.client.get_features.return_value = (
        _all_recorded_deprecation_features()
    )

    # Act: List the deprecated features with their values.
    exit_status, out, _ = await run_cli("list-features", "--deprecated", "--values")

    # Assert: The value layout is kept and the notice follows the value.
    assert exit_status == 0
    lines = out.splitlines()
    assert lines[0] == "Found 5 Features for device DEV1:"
    assert lines[1] == (
        f"  {'heating.dhw.sensors.temperature.hotWaterStorage':<75}: 48.8 celsius "
        "(removal 2024-09-15: replaced by heating.dhw.sensors.temperature.dhwCylinder)"
    )
    assert lines[-1] == "(* = writable)"
    assert not any("dhwCylinder " in line for line in lines)


async def test_run_cli_list_features_deprecated_combines_with_enabled(
    run_cli, mock_cli_context
):
    """--deprecated filters the features the --available read returns."""
    # Arrange: The availability-filtered read returns one deprecated and one
    # regular feature.
    features = _all_recorded_deprecation_features()
    mock_cli_context.client.get_features.return_value = [features[0], features[2]]

    # Act: Combine both filters.
    exit_status, out, _ = await run_cli("list-features", "--deprecated", "--available")

    # Assert: The read was availability-filtered and only the deprecated
    # feature is listed.
    assert exit_status == 0
    mock_cli_context.client.get_features.assert_awaited_once()
    assert mock_cli_context.client.get_features.await_args.kwargs == {
        "only_available": True
    }
    assert out.splitlines()[0] == "Found 1 Features for device DEV1:"
    assert len(out.splitlines()) == 2


async def test_run_cli_list_features_deprecated_json_prints_names_only(
    run_cli, mock_cli_context
):
    """list-features --deprecated --json prints the filtered feature names."""
    # Arrange: Serve two deprecated API features and their replacement.
    mock_cli_context.client.get_features.return_value = (
        _all_recorded_deprecation_features()
    )

    # Act: List the deprecated features as JSON.
    exit_status, out, _ = await run_cli("list-features", "--deprecated", "--json")

    # Assert: The document is a plain list of the deprecated feature names.
    assert exit_status == 0
    assert json.loads(out) == [
        "heating.dhw.sensors.temperature.hotWaterStorage",
        "heating.dhw.sensors.temperature.hotWaterStorage.status",
        "heating.dhw.temperature.levels.min",
        "heating.dhw.temperature.levels.max",
        "heating.dhw.temperature.levels.default",
    ]


@pytest.mark.parametrize(
    ("options", "expected_count"),
    [
        pytest.param([], 7, id="all-features"),
        pytest.param(["--deprecated"], 5, id="deprecated-only"),
    ],
)
async def test_run_cli_list_features_json_values_always_carries_the_notice(
    run_cli, mock_cli_context, options, expected_count
):
    """list-features --json --values prints the deprecation key for every feature."""
    # Arrange: Serve two deprecated API features and their replacement.
    mock_cli_context.client.get_features.return_value = (
        _all_recorded_deprecation_features()
    )

    # Act: List the features with values as JSON.
    exit_status, out, _ = await run_cli("list-features", "--json", "--values", *options)

    # Assert: Every document has the key, in the shape get-feature uses:
    # an object for a deprecated feature and null for a regular one.
    assert exit_status == 0
    documents = {document["name"]: document for document in json.loads(out)}
    assert len(documents) == expected_count
    assert all("deprecation" in document for document in documents.values())
    assert documents["heating.dhw.sensors.temperature.hotWaterStorage"][
        "deprecation"
    ] == {
        "removal_date": "2024-09-15",
        "info": "replaced by heating.dhw.sensors.temperature.dhwCylinder",
    }
    if not options:
        assert (
            documents["heating.dhw.sensors.temperature.dhwCylinder"]["deprecation"]
            is None
        )


@pytest.mark.parametrize(
    ("options", "expected_out"),
    [
        pytest.param([], "Found 0 Features for device DEV1:\n", id="text"),
        pytest.param(["--values"], None, id="values"),
        pytest.param(["--json"], "[]\n", id="json"),
        pytest.param(["--json", "--values"], "[]\n", id="json-values"),
    ],
)
async def test_run_cli_list_features_deprecated_succeeds_without_notices(
    run_cli, mock_cli_context, options, expected_out
):
    """A device without any notice lists nothing and exits successfully."""
    # Arrange: Serve only the replacement feature, which carries no notice.
    mock_cli_context.client.get_features.return_value = _recorded_deprecated_features(
        "heating.dhw.sensors.temperature.dhwCylinder"
    )

    # Act: List the deprecated features.
    exit_status, out, _ = await run_cli("list-features", "--deprecated", *options)

    # Assert: The result is empty and the command succeeds.
    assert exit_status == 0
    if expected_out is None:
        assert out == "Found 0 Features for device DEV1:\n(* = writable)\n"
    else:
        assert out == expected_out


async def test_run_cli_list_features_without_the_option_prints_no_notice(
    run_cli, mock_cli_context
):
    """Without --deprecated the text output carries no notice."""
    # Arrange: Serve two deprecated API features and their replacement.
    mock_cli_context.client.get_features.return_value = (
        _all_recorded_deprecation_features()
    )

    # Act: List the features in both text layouts.
    _, names_out, _ = await run_cli("list-features")
    exit_status, values_out, _ = await run_cli("list-features", "--values")

    # Assert: Every feature is listed and no line mentions a removal.
    assert exit_status == 0
    assert names_out.splitlines()[0] == "Found 7 Features for device DEV1:"
    assert "- heating.dhw.sensors.temperature.hotWaterStorage\n" in names_out
    assert "removal" not in names_out
    assert "removal" not in values_out


@pytest.mark.parametrize(
    ("deprecation", "expected_suffix"),
    [
        pytest.param(
            FeatureDeprecation(date(2027, 3, 31), None),
            " (removal 2027-03-31)",
            id="date-only",
        ),
        pytest.param(
            FeatureDeprecation(None, "Not used anymore"),
            " (removal: Not used anymore)",
            id="info-only",
        ),
        pytest.param(FeatureDeprecation(None, None), " (removal)", id="empty-notice"),
        pytest.param(None, "", id="no-notice"),
    ],
)
def test_removal_suffix_leaves_out_missing_fields(deprecation, expected_suffix):
    """A missing date or info is left out of the listing suffix."""
    feature = replace(build_feature(), deprecation=deprecation)

    assert _removal_suffix(feature) == expected_suffix


def _subcommand_names() -> list[str]:
    """Return every subcommand name the parser offers."""
    subparsers = next(
        action
        for action in build_parser()._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return sorted(subparsers.choices)


@pytest.mark.parametrize("command", _subcommand_names())
def test_every_command_help_ends_with_examples(command, capsys):
    # Act: Print the command's help.
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args([command, "--help"])

    # Assert: The help exits cleanly, groups the shared options, and shows
    # an example of the command itself.
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "authentication:" in out
    assert f"examples:\n  vi-client {command}" in out


def test_main_help_lists_commands_and_examples(capsys):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])

    out = capsys.readouterr().out
    assert all(command in out for command in _subcommand_names())
    assert "Run 'vi-client <command> --help'" in out


def test_version_prints_the_installed_package_version(capsys):
    with pytest.raises(SystemExit) as exit_info:
        build_parser().parse_args(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == (
        f"vi-client {importlib.metadata.version('vi_api_client')}\n"
    )


def test_version_without_package_metadata_is_unknown(monkeypatch, capsys):
    # Arrange: Run from a source tree without installed package metadata.
    def missing_metadata(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr("vi_api_client.cli.version", missing_metadata)

    # Act: Ask for the version.
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--version"])

    # Assert: The version is reported as unknown instead of failing.
    assert capsys.readouterr().out == "vi-client unknown\n"


async def test_run_cli_get_feature_suggests_close_names(run_cli):
    exit_status, out, _ = await run_cli(
        "get-feature",
        "--fixture-device",
        "Vitocal250A",
        "heating.circuits.0.heating.curve.slop",
    )

    assert exit_status == 1
    assert "Did you mean: heating.circuits.0.heating.curve.slope," in out


async def test_run_cli_set_suggests_close_names(run_cli):
    exit_status, out, _ = await run_cli(
        "set", "--fixture-device", "Vitocal250A", "heating.dhw.temperature.mian", "50"
    )

    assert exit_status == 1
    assert "Did you mean: heating.dhw.temperature.main," in out


async def test_run_cli_exec_suggests_close_api_feature_names(run_cli):
    exit_status, _, err = await run_cli(
        "exec",
        "--fixture-device",
        "Vitocal250A",
        "heating.circuits.0.heating.curv",
        "setCurve",
        "slope=1",
    )

    assert exit_status == 1
    assert "API features with commands: heating.circuits.0.heating.curve," in err


def test_close_name_hint_is_empty_without_a_close_name():
    assert _close_name_hint("heating.nothing.like.it", ["device.serial"]) == ""


async def test_run_cli_list_devices_json_lists_every_device_with_roles(run_cli):
    exit_status, out, err = await run_cli(
        "list-devices", "--json", "--fixture-device", "Vitocal250A"
    )

    # Assert: stdout is one JSON document; setup output stays on stderr.
    assert exit_status == 0
    document = json.loads(out)
    (gateway,) = document["gateways"]
    (device,) = gateway["devices"]
    assert device["model_id"] == "E3_Vitocal_16"
    assert "type:product;Vitocal_250A" in device["roles"]
    assert document["installations"][0]["id"] == "99999"
    assert "Using Fixture Device" in err


async def test_run_cli_list_writable_json_matches_the_feature_documents(run_cli):
    exit_status, out, _ = await run_cli(
        "list-writable", "--json", "--fixture-device", "Vitocal250A"
    )

    assert exit_status == 0
    documents = json.loads(out)
    slope = next(
        document
        for document in documents
        if document["name"] == "heating.circuits.0.heating.curve.slope"
    )
    assert slope["command"] == {"name": "setCurve", "parameter": "slope"}
    assert all(document["command"] is not None for document in documents)


async def test_run_cli_exec_json_reports_the_result(run_cli):
    exit_status, out, _ = await run_cli(
        "exec",
        "--json",
        "--fixture-device",
        "Vitocal250A",
        "heating.circuits.0.heating.curve",
        "setCurve",
        "slope=1.2",
        "shift=0",
    )

    assert exit_status == 0
    assert json.loads(out) == {
        "api_feature": "heating.circuits.0.heating.curve",
        "command": "setCurve",
        "parameters": {"slope": 1.2, "shift": 0},
        "success": True,
        "message": None,
        "reason": None,
    }


async def test_cmd_exec_json_reports_a_rejected_command(mock_cli_context, capsys):
    # Arrange: The device rejects the command.
    args = _cli_args("exec", "--json", "x.y", "setMode", "mode=eco")
    mock_cli_context.client.refresh_device.return_value = build_device("DEV1")
    mock_cli_context.client.run_command.side_effect = ViCommandRejectedError(
        "not allowed", "COMMAND_NOT_EXECUTABLE"
    )

    # Act: Run the command.
    assert await cmd_exec(args) is False

    # Assert: The JSON result names the rejection.
    document = json.loads(capsys.readouterr().out)
    assert document["success"] is False
    assert (document["message"], document["reason"]) == (
        "not allowed",
        "COMMAND_NOT_EXECUTABLE",
    )
