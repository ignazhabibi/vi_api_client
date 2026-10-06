"""Tests for the CLI command handlers and the dispatch entry path."""

import json
import logging
import subprocess
import sys
from argparse import Namespace
from collections.abc import Iterator
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from builders import (
    build_device,
    build_feature,
    build_gateway,
    build_installation,
    load_fixture_device,
)

from vi_api_client import (
    CommandResponse,
    EventHistoryPage,
    FeatureValue,
    FixtureViClient,
    InstallationEvent,
    ViClient,
)
from vi_api_client.cli import (
    _EVENT_LINE_WIDTH,
    CLIContext,
    EventHistoryWindow,
    _dispatch_command,
    _event_detail_lines,
    _infer_feature_value_type,
    _print_event_summary,
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
    Device,
    Feature,
    FeatureControl,
    ScheduleConstraints,
)


def _cli_args(*argv: str) -> Namespace:
    """Parse a command line through the real CLI parser."""
    return build_parser().parse_args(argv)


def _control(**overrides: Any) -> FeatureControl:
    """Build one writable feature control for a ``heating.curve.slope`` target."""
    fields: dict[str, Any] = {
        "command_name": "setCurve",
        "param_name": "slope",
        "required_params": ["slope"],
        "parent_feature_name": "heating.curve",
        "uri": "uri",
    }
    fields.update(overrides)
    return FeatureControl(**fields)


@pytest.fixture
def mock_cli_context() -> Iterator[CLIContext]:
    """Make commands use a context whose client offers real ViClient methods."""
    context = CLIContext(None, AsyncMock(spec=ViClient), "99", "GW1", "DEV1")
    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = context
        yield context


def _successful_set_result() -> tuple[CommandResponse, MagicMock]:
    """Return a mocked successful set_feature result pair."""
    return CommandResponse(success=True), MagicMock(spec=Device)


async def test_cmd_set_writes_the_parsed_value(mock_cli_context, capsys):
    """Successful writes should confirm the command, param, and result."""
    # Arrange: Provide a writable numeric feature and a successful write.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    feature = build_feature(control=_control(value_type="number"))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = _successful_set_result()

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
    feature = build_feature(control=_control(value_type=value_type))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = _successful_set_result()

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
    feature = build_feature(control=_control(value_type=value_type))
    mock_cli_context.client.get_features.return_value = [feature]

    # Act: Submit the malformed command line value.
    assert await cmd_set(args) is False

    # Assert: The validation failure is printed and no write is sent.
    captured = capsys.readouterr()
    assert f"Validation failed: Value '{raw_value}'" in captured.out
    assert message in captured.out
    mock_cli_context.client.set_feature.assert_not_called()


async def test_cmd_set_rejects_schedules_that_are_not_json(mock_cli_context, capsys):
    """A schedule value that is not JSON should be rejected without a write."""
    # Arrange: Provide a writable schedule feature.
    args = _cli_args("set", "heating.curve.slope", "mon 06:00-22:00")
    feature = build_feature(control=_control(value_type="Schedule"))
    mock_cli_context.client.get_features.return_value = [feature]

    # Act: Submit a schedule that is not JSON.
    assert await cmd_set(args) is False

    # Assert: The validation failure asks for JSON and no write is sent.
    assert "must be a JSON schedule object" in capsys.readouterr().out
    mock_cli_context.client.set_feature.assert_not_called()


async def test_cmd_set_infers_legacy_feature_types(mock_cli_context):
    """Legacy features without metadata should infer their write type."""
    # Arrange: Provide a valueless-type control whose value hints the type.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    feature = build_feature(value=1.0, control=_control(value_type=None))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = _successful_set_result()

    # Act: Set a numeric current value through the CLI.
    assert await cmd_set(args) is True

    # Assert: The numeric feature value infers the number conversion.
    assert mock_cli_context.client.set_feature.call_args.args[2] == 1.4


async def test_cmd_set_reports_failed_command_results(mock_cli_context, capsys):
    """Failed API writes should print the response message and reason."""
    # Arrange: Make the write succeed at the boundary but fail at the API.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    feature = build_feature(control=_control(value_type="number"))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = (
        CommandResponse(success=False, message="API rejected", reason="out of range"),
        MagicMock(spec=Device),
    )

    # Act: Attempt the write through the CLI.
    assert await cmd_set(args) is False

    # Assert: The failure surfaces the response details.
    captured = capsys.readouterr()
    assert "Failed!" in captured.out
    assert "Message: API rejected" in captured.out
    assert "Reason: out of range" in captured.out


async def test_cmd_set_reports_failed_results_without_details(mock_cli_context, capsys):
    """Absent response details must not print empty Message or Reason lines."""
    # Arrange: Make the write fail without a message or reason.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    feature = build_feature(control=_control(value_type="number"))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = (
        CommandResponse(success=False),
        MagicMock(spec=Device),
    )

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
    mock_cli_context.client.get_features.assert_not_called()
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
            ["exec", "heating.curve.slope", "setCurve", "slope=1.4"],
            ("execute_command", "executing command"),
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
            ("get_features", "listing writable features"),
            id="list-writable",
        ),
        pytest.param(
            ["list-events", "--days", "7"],
            ("get_event_history", "listing events"),
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
    mock_cli_context.client.get_features.return_value = [
        build_feature(control=_control(value_type="number"))
    ]
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
    mock_cli_context.client.get_features.return_value = [build_feature(control=None)]

    # Act: Attempt to set the read-only feature.
    assert await cmd_set(args) is False

    # Assert: The CLI explains the read-only state without a write.
    assert "is read-only" in capsys.readouterr().out
    mock_cli_context.client.set_feature.assert_not_called()


async def test_cmd_set_reports_missing_features(mock_cli_context, capsys):
    """Unknown feature names should reject CLI writes before any request."""
    # Arrange: Return no features for the requested name.
    args = _cli_args("set", "missing.feature", "1.4")
    mock_cli_context.client.get_features.return_value = []

    # Act: Attempt to set an absent feature.
    assert await cmd_set(args) is False

    # Assert: The CLI names the missing feature.
    assert "Error: Feature 'missing.feature' not found." in capsys.readouterr().out
    mock_cli_context.client.set_feature.assert_not_called()


async def test_cmd_set_reports_not_found_errors(mock_cli_context, capsys):
    """Not-found write failures should surface the API message."""
    # Arrange: Make the write fail with a not-found API error.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    feature = build_feature(control=_control(value_type="number"))
    mock_cli_context.client.get_features.return_value = [feature]
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
    control = _control(value_type=None)
    if numeric_constraints:
        control = _control(value_type=None, min=0.2, max=3.5, step=0.1)
    feature = build_feature(value=feature_value, control=control)

    # Act and assert: The value shape and constraints determine the type
    # inferred for a command without metadata.
    assert _infer_feature_value_type(feature) == expected_type


async def test_cmd_get_feature_prints_value_and_control_details(
    mock_cli_context, capsys
):
    """A writable feature should print its value, command, and constraints."""
    # Arrange: Return one constrained writable feature for the requested name.
    args = _cli_args("get-feature", "heating.curve.slope")
    feature = build_feature(
        "heating.curve.slope",
        1.4,
        _control(min=0.2, max=3.5, step=0.1, options=["low", "high"]),
        unit="celsius",
    )
    mock_cli_context.client.get_features.return_value = [feature]

    # Act: Read the feature through the CLI.
    assert await cmd_get_feature(args) is True

    # Assert: The output includes the formatted value and control details.
    captured = capsys.readouterr()
    assert "- heating.curve.slope: 1.4 celsius" in captured.out
    assert "Writable via command: setCurve" in captured.out
    assert "Target param: slope" in captured.out
    assert "Constraints: min=0.2, max=3.5, step=0.1" in captured.out
    assert "Options: ('low', 'high')" in captured.out


async def test_cmd_get_feature_prints_read_only_values(mock_cli_context, capsys):
    """A read-only feature should print its value without control details."""
    # Arrange: Return one read-only feature for the requested name.
    args = _cli_args("get-feature", "heating.status")
    feature = build_feature("heating.status", "ready")
    mock_cli_context.client.get_features.return_value = [feature]

    # Act: Read the feature through the CLI.
    assert await cmd_get_feature(args) is True

    # Assert: The value prints without any writable command details.
    captured = capsys.readouterr()
    assert "- heating.status: ready" in captured.out
    assert "Writable via command" not in captured.out


@pytest.mark.parametrize(
    ("control_overrides", "expect_constraints"),
    [
        pytest.param({"options": ["low", "high"]}, False, id="options-only"),
        pytest.param({"min": 0.2}, True, id="min-only"),
    ],
)
async def test_cmd_get_feature_prints_only_present_control_details(
    mock_cli_context, capsys, control_overrides: dict, expect_constraints: bool
):
    """Control details should print only the constraints and options that exist."""
    # Arrange: Return a writable feature with only one kind of control detail.
    args = _cli_args("get-feature", "heating.curve.slope")
    feature = build_feature(
        "heating.curve.slope", 1.4, _control(**control_overrides), unit="celsius"
    )
    mock_cli_context.client.get_features.return_value = [feature]

    # Act: Read the feature through the CLI.
    assert await cmd_get_feature(args) is True

    # Assert: Only the present detail kind prints.
    captured = capsys.readouterr()
    assert ("Constraints:" in captured.out) is expect_constraints
    assert ("Options:" in captured.out) is not expect_constraints


async def test_cmd_get_feature_json_prints_machine_readable_document(
    mock_cli_context, capsys
):
    """The JSON flag should print the feature and its control as JSON."""
    # Arrange: Return one writable feature with a unit for the requested name.
    args = _cli_args("get-feature", "heating.curve.slope", "--json")
    feature = build_feature("heating.curve.slope", 1.4, _control(), unit="celsius")
    mock_cli_context.client.get_features.return_value = [feature]

    # Act: Read the feature in JSON mode.
    assert await cmd_get_feature(args) is True

    # Assert: The document carries the feature fields and control object.
    document = json.loads(capsys.readouterr().out)
    assert document["name"] == "heating.curve.slope"
    assert document["value"] == 1.4
    assert document["unit"] == "celsius"
    assert document["control"]["command_name"] == "setCurve"
    assert document["control"]["param_name"] == "slope"


def _curve_features() -> list[Feature]:
    """Return the two features parsed from one heating curve API feature."""
    return [
        build_feature(f"heating.curve.{name}", value)
        for name, value in (("shift", 0), ("slope", 1.4))
    ]


async def test_cmd_get_feature_json_marks_read_only_features_with_null_control(
    mock_cli_context, capsys
):
    """Read-only features have no command metadata in the JSON document."""
    # Arrange: Return one read-only feature.
    args = _cli_args("get-feature", "heating.curve.slope", "--json")
    mock_cli_context.client.get_features.return_value = [build_feature(control=None)]

    # Act: Read the feature in JSON mode.
    assert await cmd_get_feature(args) is True

    # Assert: The control is JSON null rather than an absent or text value.
    document = json.loads(capsys.readouterr().out)
    assert document["control"] is None


async def test_cmd_get_feature_prints_every_feature_of_an_api_feature(
    mock_cli_context, capsys
):
    """An API feature name should print every feature parsed from it."""
    # Arrange: Return both features of the requested API feature.
    args = _cli_args("get-feature", "heating.curve")
    mock_cli_context.client.get_features.return_value = _curve_features()

    # Act: Read the API feature through the CLI.
    assert await cmd_get_feature(args) is True

    # Assert: Both matching features print.
    captured = capsys.readouterr()
    assert "- heating.curve.shift: 0" in captured.out
    assert "- heating.curve.slope: 1.4" in captured.out


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

    assert await cmd_get_feature(args) is False

    assert "Feature 'missing.feature' not found." in capsys.readouterr().out


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


async def test_cmd_exec_preserves_explicit_parameters(mock_cli_context, capsys):
    """CLI executes every explicitly supplied advanced command parameter."""
    # Arrange: Provide a writable feature and an explicit parameter set.
    args = _cli_args("exec", "heating.curve.slope", "setCurve", "slope=1.4", "shift=0")
    feature = build_feature(control=_control())
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.execute_command.return_value = CommandResponse(
        success=True, message="OK", reason=None
    )

    # Act: Execute the explicit command through the CLI.
    assert await cmd_exec(args) is True

    # Assert: The client receives the hydrated device and exact parameters.
    assert mock_cli_context.client.get_features.call_args.args[0].id == "DEV1"
    mock_cli_context.client.execute_command.assert_awaited_once_with(
        feature, {"slope": 1.4, "shift": 0}
    )
    assert "Success!" in capsys.readouterr().out


async def test_cmd_exec_rejects_malformed_parameter_arguments(mock_cli_context, capsys):
    """Unparseable parameter arguments should reject before any request."""
    args = _cli_args("exec", "heating.curve.slope", "setCurve", "not-key-value")

    assert await cmd_exec(args) is False

    assert "Error parsing parameters:" in capsys.readouterr().out
    mock_cli_context.client.get_features.assert_not_called()


async def test_cmd_exec_rejects_read_only_features(mock_cli_context, capsys):
    """Read-only features should reject explicit commands."""
    # Arrange: Provide a feature without command metadata and no params argument.
    args = _cli_args("exec", "heating.curve.slope", "setCurve")
    mock_cli_context.client.get_features.return_value = [build_feature(control=None)]

    # Act: Attempt the explicit command.
    assert await cmd_exec(args) is False

    # Assert: The CLI explains the read-only state without executing.
    assert "is read-only" in capsys.readouterr().out
    mock_cli_context.client.execute_command.assert_not_called()


async def test_cmd_exec_reports_missing_features(mock_cli_context, capsys):
    """Unknown feature names should reject explicit commands."""
    # Arrange: Return no features for the requested name.
    args = _cli_args("exec", "missing.feature", "setCurve")
    mock_cli_context.client.get_features.return_value = []

    # Act: Attempt the explicit command.
    assert await cmd_exec(args) is False

    # Assert: The CLI names the missing feature.
    assert "Error: Feature 'missing.feature' not found." in capsys.readouterr().out
    mock_cli_context.client.execute_command.assert_not_called()


async def test_cmd_exec_rejects_foreign_command_names(mock_cli_context, capsys):
    """Explicit commands should only run a feature's primary command."""
    # Arrange: Request a command name the feature does not expose.
    args = _cli_args("exec", "heating.curve.slope", "otherCommand", "slope=1.4")
    mock_cli_context.client.get_features.return_value = [
        build_feature(control=_control())
    ]

    # Act: Attempt the foreign command.
    assert await cmd_exec(args) is False

    # Assert: The CLI names the expected command without executing.
    captured = capsys.readouterr()
    assert "only supports command 'setCurve', not 'otherCommand'" in captured.out
    mock_cli_context.client.execute_command.assert_not_called()


async def test_cmd_exec_reports_not_found_errors(mock_cli_context, capsys):
    """Not-found explicit commands should surface the API message."""
    # Arrange: Make the explicit command fail with a not-found API error.
    args = _cli_args("exec", "heating.curve.slope", "setCurve", "slope=1.4")
    mock_cli_context.client.get_features.return_value = [
        build_feature(control=_control())
    ]
    mock_cli_context.client.execute_command.side_effect = ViNotFoundError("device gone")

    # Act: Execute the explicit command.
    assert await cmd_exec(args) is False

    # Assert: The CLI reports the not-found failure.
    assert "Not found: device gone" in capsys.readouterr().out


async def test_cmd_exec_reports_validation_errors(mock_cli_context, capsys):
    """Validation errors from explicit commands should print their message."""
    # Arrange: Make the explicit command fail with a validation error.
    args = _cli_args("exec", "heating.curve.slope", "setCurve", "slope=invalid")
    mock_cli_context.client.get_features.return_value = [
        build_feature(control=_control())
    ]
    mock_cli_context.client.execute_command.side_effect = ViValidationError(
        "Simulated Validation Error"
    )

    # Act: Execute the explicit command.
    assert await cmd_exec(args) is False

    # Assert: The CLI reports the validation failure.
    assert "Validation failed: Simulated Validation Error" in capsys.readouterr().out


async def test_cmd_set_hydrates_required_command_dependencies(mock_cli_context):
    """CLI writes should provide sibling values required by a command."""
    # Arrange: Provide heating-curve features that share the setCurve command.
    args = _cli_args("set", "heating.curve.slope", "1.4")
    control = _control(required_params=["shift", "slope"])
    slope = build_feature(control=control)
    shift = build_feature(name="heating.curve.shift", value=4.0, control=control)
    mock_cli_context.client.get_features.return_value = [slope, shift]
    mock_cli_context.client.set_feature.return_value = _successful_set_result()

    # Act: Set the slope through the CLI command.
    assert await cmd_set(args) is True

    # Assert: The write receives a device containing the sibling shift value.
    command_device = mock_cli_context.client.set_feature.call_args.args[0]
    assert command_device.get_feature("heating.curve.slope") is slope
    assert command_device.get_feature("heating.curve.shift") is shift
    assert mock_cli_context.client.get_features.call_args.kwargs == {}


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
    """Value JSON carries the raw value, unit, formatted text, and writability."""
    # Arrange: Provide one writable feature and request JSON value output.
    args = _cli_args("list-features", "--values", "--json")
    mock_cli_context.client.get_features.return_value = [
        build_feature(name="f1", value=1.4, control=_control())
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
        }
    ]


async def test_cmd_list_features_enabled_requests_only_enabled_features(
    mock_cli_context,
):
    """--enabled delegates the filter to the client read for the context device."""
    # Arrange: Provide one feature and request the enabled filter.
    args = _cli_args("list-features", "--enabled", "--json")
    mock_cli_context.client.get_features.return_value = [
        build_feature(name="f_enabled", value=1)
    ]

    # Act: List only enabled features.
    assert await cmd_list_features(args) is True

    # Assert: The read requests the enabled filter for the context device.
    call_args = mock_cli_context.client.get_features.call_args
    assert call_args.args[0].id == "DEV1"
    assert call_args.kwargs["only_enabled"] is True


async def test_cmd_list_features_values_prints_table_with_writable_marks(
    mock_cli_context, capsys
):
    """Value rows mark writable features and truncate long values to fit."""
    # Arrange: Provide a writable feature and a long read-only value.
    args = _cli_args("list-features", "--values")
    long_value = "x" * 90
    mock_cli_context.client.get_features.return_value = [
        build_feature(name="writable.feature", value=1.4, control=_control()),
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
        control=_control(min=0.2, max=3.5, step=0.1),
    )
    mock_cli_context.client.get_features.return_value = [feature]

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
        control=_control(
            options=["auto", "manual"],
            min_length=1,
            max_length=10,
            pattern="^[a-z]+$",
        ),
    )
    mock_cli_context.client.get_features.return_value = [feature]

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
    # Arrange: Provide a writable feature whose control declares no constraints.
    args = _cli_args("list-writable")
    feature = build_feature(name="heating.program", control=_control())
    mock_cli_context.client.get_features.return_value = [feature]

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
        control=_control(
            command_name="setSchedule",
            param_name="newSchedule",
            schedule=ScheduleConstraints(max_entries=4, modes=["on"], resolution=10),
        ),
    )
    mock_cli_context.client.get_features.return_value = [feature]

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
    mock_cli_context.client.get_event_history.return_value = _empty_event_page()

    # Act: List events without an explicit installation ID.
    assert await cmd_list_events(args) is True

    # Assert: The first installation scopes the event history request.
    mock_cli_context.client.get_event_history.assert_awaited_once_with(
        "12345", days=7, limit=None
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
    mock_cli_context.client.get_event_history.return_value = _empty_event_page()

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
    mock_cli_context.client.get_event_history.assert_not_called()


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


def _empty_event_page() -> EventHistoryPage:
    """Return the page the provider sends for a window without events."""
    return EventHistoryPage(events=[], next_cursor=None)


async def test_cmd_list_events_summarizes_an_empty_window(mock_cli_context, capsys):
    """An empty window completes and prints no event timestamps."""
    # Arrange: The provider returns no events for a one-year window.
    args = _cli_args("list-events", "--days", "365")
    mock_cli_context.client.get_event_history.return_value = _empty_event_page()

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
    mock_cli_context.client.get_event_history.return_value = _empty_event_page()

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
    assert "7630175843100101" not in out


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
        gateway_serial="7630175843100101",
    )
    second = replace(
        _detail_event({"featureName": "heating.dhw.oneTimeCharge"}, "feature-changed"),
        gateway_serial="8112200229931101",
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
    assert "- 10:15:30 UTC feature-changed gateway 7630175843100101" in output
    assert "- 10:15:30 UTC feature-changed gateway 8112200229931101" in output
    assert "- 10:15:30 UTC feature-changed gateway unknown" in output
