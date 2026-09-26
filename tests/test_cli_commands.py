"""Tests for the CLI command handlers and the dispatch entry path."""

import json
import logging
import subprocess
import sys
from argparse import Namespace
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vi_api_client import (
    EventHistoryPage,
    FeatureValue,
    FixtureViClient,
    InstallationEvent,
)
from vi_api_client.cli import (
    EventHistoryWindow,
    _dispatch_command,
    _format_event_details,
    _infer_feature_value_type,
    _print_event_summary,
    async_main,
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
    ViResponseError,
    ViValidationError,
)
from vi_api_client.models import Device, Feature, FeatureControl, Gateway, Installation


def _cli_args(**overrides: Any) -> Namespace:
    """Build a CLI argument namespace with optional overrides."""
    arguments: dict[str, Any] = {
        "token_file": "tokens.json",
        "client_id": None,
        "redirect_uri": None,
        "insecure": False,
        "fixture_device": None,
        "installation_id": None,
        "gateway_serial": None,
        "device_id": None,
    }
    arguments.update(overrides)
    return Namespace(**arguments)


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


def _feature(
    name: str = "heating.curve.slope",
    value: FeatureValue = 1.4,
    control: FeatureControl | None = None,
) -> Feature:
    """Build one enabled, ready feature snapshot."""
    return Feature(
        name=name,
        value=value,
        unit=None,
        is_enabled=True,
        is_ready=True,
        control=control,
    )


@pytest.fixture
def mock_cli_context():
    """Provide a mocked CLI context with an async client boundary."""
    fixture_client = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.client = fixture_client
    mock_ctx.inst_id = "99"
    mock_ctx.gw_serial = "GW1"
    mock_ctx.dev_id = "DEV1"
    return mock_ctx


@contextmanager
def _patched_cli_context(mock_cli_context) -> Iterator[MagicMock]:
    """Patch the CLI context setup boundary and yield the setup mock."""
    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context
        yield mock_setup


def _successful_set_result() -> tuple[MagicMock, MagicMock]:
    """Return a mocked successful set_feature result pair."""
    return MagicMock(success=True, message=None, reason=None), MagicMock()


@pytest.mark.asyncio
async def test_cmd_set_success(mock_cli_context, capsys):
    """Successful writes should confirm the command, param, and result."""
    # Arrange: Provide a writable numeric feature and a successful write.
    args = _cli_args(feature_name="heating.curve.slope", value="1.4")
    feature = _feature(control=_control(value_type="number"))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = _successful_set_result()

    with _patched_cli_context(mock_cli_context):
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
        ("string", "01", "01"),
        ("boolean", "TRUE", True),
        ("boolean", "false", False),
        ("integer", "2", 2),
    ],
)
@pytest.mark.asyncio
async def test_cmd_set_converts_typed_command_values(
    mock_cli_context, value_type: str, raw_value: str, expected_value: Any
):
    """CLI writes should convert values to the target command parameter type."""
    # Arrange: Provide a writable feature of the parameter's declared type.
    args = _cli_args(feature_name="heating.curve.slope", value=raw_value)
    feature = _feature(control=_control(value_type=value_type))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = _successful_set_result()

    with _patched_cli_context(mock_cli_context):
        # Act: Submit the raw command line value.
        assert await cmd_set(args) is True

    # Assert: The command receives the converted typed value.
    assert mock_cli_context.client.set_feature.call_args.args[2] == expected_value


@pytest.mark.parametrize(
    ("value_type", "raw_value", "message"),
    [
        ("number", "automatic", "must be a number"),
        ("number", "inf", "must be finite"),
        ("boolean", "maybe", "must be true or false"),
        ("integer", "1.5", "must be an integer"),
    ],
)
@pytest.mark.asyncio
async def test_cmd_set_rejects_malformed_typed_values(
    mock_cli_context, capsys, value_type: str, raw_value: str, message: str
):
    """Malformed typed values should explain the failure without a write."""
    # Arrange: Provide a writable feature whose declared type rejects the value.
    args = _cli_args(feature_name="heating.curve.slope", value=raw_value)
    feature = _feature(control=_control(value_type=value_type))
    mock_cli_context.client.get_features.return_value = [feature]

    with _patched_cli_context(mock_cli_context):
        # Act: Submit the malformed command line value.
        assert await cmd_set(args) is False

    # Assert: The validation failure is printed and no write is sent.
    captured = capsys.readouterr()
    assert f"Validation failed: Value '{raw_value}'" in captured.out
    assert message in captured.out
    mock_cli_context.client.set_feature.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_set_infers_legacy_feature_types(mock_cli_context):
    """Legacy features without metadata should infer their write type."""
    # Arrange: Provide a valueless-type control whose value hints the type.
    args = _cli_args(feature_name="heating.curve.slope", value="1.4")
    feature = _feature(value=1.0, control=_control(value_type=None))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = _successful_set_result()

    with _patched_cli_context(mock_cli_context):
        # Act: Set a numeric current value through the CLI.
        assert await cmd_set(args) is True

    # Assert: The numeric feature value infers the number conversion.
    assert mock_cli_context.client.set_feature.call_args.args[2] == 1.4


@pytest.mark.asyncio
async def test_cmd_set_reports_failed_command_results(mock_cli_context, capsys):
    """Failed API writes should print the response message and reason."""
    # Arrange: Make the write succeed at the boundary but fail at the API.
    args = _cli_args(feature_name="heating.curve.slope", value="1.4")
    feature = _feature(control=_control(value_type="number"))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = (
        MagicMock(success=False, message="API rejected", reason="out of range"),
        MagicMock(),
    )

    with _patched_cli_context(mock_cli_context):
        # Act: Attempt the write through the CLI.
        assert await cmd_set(args) is False

    # Assert: The failure surfaces the response details.
    captured = capsys.readouterr()
    assert "Failed!" in captured.out
    assert "Message: API rejected" in captured.out
    assert "Reason: out of range" in captured.out


@pytest.mark.asyncio
async def test_cmd_set_failed_result_without_details(mock_cli_context, capsys):
    """Failed writes without response details should print the bare failure."""
    # Arrange: Make the write fail without a message or reason.
    args = _cli_args(feature_name="heating.curve.slope", value="1.4")
    feature = _feature(control=_control(value_type="number"))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = (
        MagicMock(success=False, message=None, reason=None),
        MagicMock(),
    )

    with _patched_cli_context(mock_cli_context):
        # Act: Attempt the write through the CLI.
        assert await cmd_set(args) is False

    # Assert: The failure prints without message or reason lines.
    captured = capsys.readouterr()
    assert "Failed!" in captured.out
    assert "Message:" not in captured.out
    assert "Reason:" not in captured.out


@pytest.mark.asyncio
async def test_cmd_set_reports_missing_device_context(mock_cli_context):
    """Writes without a device context should fail gracefully."""
    # Arrange: Strip the device identifier from the CLI context.
    args = _cli_args(feature_name="heating.curve.slope", value="1.4")
    mock_cli_context.dev_id = None
    mock_cli_context.client.get_features.return_value = [
        _feature(control=_control(value_type="number"))
    ]

    with _patched_cli_context(mock_cli_context):
        # Act: Attempt the write without a device context.
        assert await cmd_set(args) is False

    # Assert: No write is sent without the device context.
    mock_cli_context.client.set_feature.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_set_rejects_read_only_features(mock_cli_context, capsys):
    """Read-only features should reject CLI writes before any request."""
    # Arrange: Provide a feature without command metadata.
    args = _cli_args(feature_name="heating.curve.slope", value="1.4")
    mock_cli_context.client.get_features.return_value = [_feature(control=None)]

    with _patched_cli_context(mock_cli_context):
        # Act: Attempt to set the read-only feature.
        assert await cmd_set(args) is False

    # Assert: The CLI explains the read-only state without a write.
    assert "is read-only" in capsys.readouterr().out
    mock_cli_context.client.set_feature.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_set_reports_missing_features(mock_cli_context, capsys):
    """Unknown feature names should reject CLI writes before any request."""
    # Arrange: Return no features for the requested name.
    args = _cli_args(feature_name="missing.feature", value="1.4")
    mock_cli_context.client.get_features.return_value = []

    with _patched_cli_context(mock_cli_context):
        # Act: Attempt to set an absent feature.
        assert await cmd_set(args) is False

    # Assert: The CLI names the missing feature.
    assert "Error: Feature 'missing.feature' not found." in capsys.readouterr().out
    mock_cli_context.client.set_feature.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_set_reports_not_found_errors(mock_cli_context, capsys):
    """Not-found write failures should surface the API message."""
    # Arrange: Make the write fail with a not-found API error.
    args = _cli_args(feature_name="heating.curve.slope", value="1.4")
    feature = _feature(control=_control(value_type="number"))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.side_effect = ViNotFoundError("device gone")

    with _patched_cli_context(mock_cli_context):
        # Act: Attempt the write.
        assert await cmd_set(args) is False

    # Assert: The CLI reports the not-found failure.
    assert "Not found: device gone" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cmd_set_reports_unexpected_errors(mock_cli_context, capsys):
    """Unexpected write failures should fail the command without a traceback."""
    # Arrange: Make the write fail with an unexpected error.
    args = _cli_args(feature_name="heating.curve.slope", value="1.4")
    feature = _feature(control=_control(value_type="number"))
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.side_effect = RuntimeError("boom")

    with _patched_cli_context(mock_cli_context):
        # Act: Attempt the write.
        assert await cmd_set(args) is False

    # Assert: The command fails cleanly with an empty success output.
    assert "Success!" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("feature_value", "numeric_constraints", "expected_type"),
    [
        (True, False, "boolean"),
        (1.4, False, "number"),
        (3, False, "number"),
        ("text", True, "number"),
        ("text", False, "string"),
    ],
)
def test_infer_feature_value_type(
    feature_value: FeatureValue, numeric_constraints: bool, expected_type: str
):
    """Legacy feature values should infer their command parameter type."""
    # Arrange: Build a legacy feature with or without numeric constraints.
    control = _control(value_type=None)
    if numeric_constraints:
        control = _control(value_type=None, min=0.2, max=3.5, step=0.1)
    feature = _feature(value=feature_value, control=control)

    # Act: Infer the type for a command without metadata.
    # Assert: The value shape and constraints determine the inferred type.
    assert _infer_feature_value_type(feature) == expected_type


@pytest.mark.asyncio
async def test_cmd_get_feature_prints_value_and_control_details(
    mock_cli_context, capsys
):
    """A writable feature should print its value, command, and constraints."""
    # Arrange: Return one constrained writable feature for the requested name.
    args = _cli_args(feature_name="heating.curve.slope", raw=False)
    feature = Feature(
        name="heating.curve.slope",
        value=1.4,
        unit="celsius",
        is_enabled=True,
        is_ready=True,
        control=_control(min=0.2, max=3.5, step=0.1, options=["low", "high"]),
    )
    mock_cli_context.client.get_features.return_value = [feature]

    with _patched_cli_context(mock_cli_context):
        # Act: Read the feature through the CLI.
        assert await cmd_get_feature(args) is True

    # Assert: The output includes the formatted value and control details.
    captured = capsys.readouterr()
    assert "- heating.curve.slope: 1.4 celsius" in captured.out
    assert "Writable via command: setCurve" in captured.out
    assert "Target param: slope" in captured.out
    assert "Constraints: min=0.2, max=3.5, step=0.1" in captured.out
    assert "Options: ('low', 'high')" in captured.out


@pytest.mark.asyncio
async def test_cmd_get_feature_prints_read_only_values(mock_cli_context, capsys):
    """A read-only feature should print its value without control details."""
    # Arrange: Return one read-only feature for the requested name.
    args = _cli_args(feature_name="heating.status", raw=False)
    feature = Feature(
        name="heating.status",
        value="ready",
        unit=None,
        is_enabled=True,
        is_ready=True,
        control=None,
    )
    mock_cli_context.client.get_features.return_value = [feature]

    with _patched_cli_context(mock_cli_context):
        # Act: Read the feature through the CLI.
        assert await cmd_get_feature(args) is True

    # Assert: The value prints without any writable command details.
    captured = capsys.readouterr()
    assert "- heating.status: ready" in captured.out
    assert "Writable via command" not in captured.out


@pytest.mark.parametrize(
    ("control_overrides", "expect_constraints"),
    [
        ({"options": ["low", "high"]}, False),
        ({"min": 0.2}, True),
    ],
    ids=["options-only", "min-only"],
)
@pytest.mark.asyncio
async def test_cmd_get_feature_prints_only_present_control_details(
    mock_cli_context, capsys, control_overrides: dict, expect_constraints: bool
):
    """Control details should print only the constraints and options that exist."""
    # Arrange: Return a writable feature with only one kind of control detail.
    args = _cli_args(feature_name="heating.curve.slope", raw=False)
    feature = Feature(
        name="heating.curve.slope",
        value=1.4,
        unit="celsius",
        is_enabled=True,
        is_ready=True,
        control=_control(**control_overrides),
    )
    mock_cli_context.client.get_features.return_value = [feature]

    with _patched_cli_context(mock_cli_context):
        # Act: Read the feature through the CLI.
        assert await cmd_get_feature(args) is True

    # Assert: Only the present detail kind prints.
    captured = capsys.readouterr()
    assert ("Constraints:" in captured.out) is expect_constraints
    assert ("Options:" in captured.out) is not expect_constraints


@pytest.mark.asyncio
async def test_cmd_get_feature_raw_prints_machine_readable_document(
    mock_cli_context, capsys
):
    """The raw flag should print the feature as an indented JSON document."""
    # Arrange: Return one writable feature with a unit for the requested name.
    args = _cli_args(feature_name="heating.curve.slope", raw=True)
    feature = Feature(
        name="heating.curve.slope",
        value=1.4,
        unit="celsius",
        is_enabled=True,
        is_ready=True,
        control=_control(),
    )
    mock_cli_context.client.get_features.return_value = [feature]

    with _patched_cli_context(mock_cli_context):
        # Act: Read the feature in raw mode.
        assert await cmd_get_feature(args) is True

    # Assert: The document carries the feature fields and control text.
    document = json.loads(capsys.readouterr().out)
    assert document["name"] == "heating.curve.slope"
    assert document["value"] == 1.4
    assert document["unit"] == "celsius"
    assert "setCurve" in document["control"]


@pytest.mark.asyncio
async def test_cmd_get_feature_not_found(mock_cli_context, capsys):
    """Unknown feature names should print a not-found notice."""
    # Arrange: Return no features for the requested name.
    args = _cli_args(feature_name="missing.feature", raw=False)
    mock_cli_context.client.get_features.return_value = []

    with _patched_cli_context(mock_cli_context):
        # Act: Read the absent feature.
        assert await cmd_get_feature(args) is False

    # Assert: The CLI names the missing feature.
    assert "Feature 'missing.feature' not found." in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cmd_get_feature_reports_unexpected_errors(mock_cli_context):
    """Unexpected read failures should fail the command without a traceback."""
    # Arrange: Make the feature read fail with an unexpected error.
    args = _cli_args(feature_name="heating.curve.slope", raw=False)
    mock_cli_context.client.get_features.side_effect = RuntimeError("boom")

    with _patched_cli_context(mock_cli_context):
        # Act: Read the feature.
        assert await cmd_get_feature(args) is False


@pytest.mark.asyncio
async def test_cmd_login_uses_environment_config_and_persists_it(monkeypatch, tmp_path):
    """Login should reuse environment credentials and save them for later commands."""
    # Arrange: Seed a token file and provide client settings through the environment.
    token_file = tmp_path / "tokens.json"
    token_file.write_text(
        '{"access_token": "existing-token", "future_field": "preserve-me"}',
        encoding="utf-8",
    )
    args = _cli_args(token_file=str(token_file))
    monkeypatch.setenv("VIESSMANN_CLIENT_ID", "environment-client-id")
    monkeypatch.setenv("VIESSMANN_REDIRECT_URI", "http://localhost:8123/auth")
    mock_auth = MagicMock()
    mock_auth.get_authorization_url.return_value = "https://example.invalid/authorize"
    mock_auth.async_fetch_details_from_code = AsyncMock()

    with (
        patch("builtins.input", return_value="authorization-code"),
        patch("vi_api_client.cli.OAuth", return_value=mock_auth) as mock_oauth,
        patch(
            "vi_api_client.cli.create_session", new_callable=AsyncMock
        ) as mock_create_session,
    ):
        mock_session = MagicMock()
        mock_create_session.return_value.__aenter__.return_value = mock_session

        # Act: Complete the CLI login flow without explicit command-line settings.
        await cmd_login(args)

    # Assert: The resolved configuration should be used and stored with tokens.
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


@pytest.mark.asyncio
async def test_cmd_login_requires_a_client_id(monkeypatch, tmp_path, capsys):
    """Login without any configured client ID should exit with guidance."""
    # Arrange: Provide neither an argument, environment, nor stored client ID.
    token_file = tmp_path / "tokens.json"
    args = _cli_args(token_file=str(token_file))
    monkeypatch.delenv("VIESSMANN_CLIENT_ID", raising=False)
    monkeypatch.delenv("VIESSMANN_REDIRECT_URI", raising=False)

    # Act and assert: The login command exits with status one and a hint.
    with pytest.raises(SystemExit) as exit_error:
        await cmd_login(args)
    assert exit_error.value.code == 1
    assert "Client ID not found" in capsys.readouterr().out


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
        client_id="argument-client", token_file=token_file, redirect_uri=None
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
    args = _cli_args(token_file=token_file)
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
    args = _cli_args(token_file=token_file)
    monkeypatch.delenv("VIESSMANN_CLIENT_ID", raising=False)
    monkeypatch.delenv("VIESSMANN_REDIRECT_URI", raising=False)

    # Act: Resolve settings for a command without arguments or environment values.
    client_id, redirect_uri = get_client_config(args)

    # Assert: The document supplies both stored settings.
    assert (client_id, redirect_uri) == ("saved-client", "http://saved")


@pytest.mark.asyncio
async def test_cmd_get_feature_rejects_non_string_feature_names():
    """Non-string feature names should reject with a local contract error."""
    # Arrange: Supply a non-string feature name outside the argparse boundary.
    args = _cli_args(feature_name=123, raw=False)

    # Act and assert: The malformed argument raises with its name.
    with pytest.raises(ValueError, match="feature_name' must be a string"):
        await cmd_get_feature(args)


@pytest.mark.asyncio
async def test_cmd_login_rejects_non_path_token_files(monkeypatch, tmp_path):
    """Non-path token file arguments should reject with a local contract error."""
    # Arrange: Supply a non-path token file outside the argparse boundary.
    monkeypatch.delenv("VIESSMANN_CLIENT_ID", raising=False)
    args = _cli_args(token_file=5, client_id="configured-client")

    # Act and assert: The malformed argument raises with its name.
    with pytest.raises(ValueError, match="token_file' must be a path"):
        await cmd_login(args)


@pytest.mark.parametrize(
    ("params", "message"),
    [
        (5, "params' must be a list"),
        (["ok", 5], "must be a list of strings"),
    ],
    ids=["non-list-params", "non-string-param"],
)
@pytest.mark.asyncio
async def test_cmd_exec_rejects_malformed_params_arguments(params, message):
    """Malformed params arguments should reject with a local contract error."""
    # Arrange: Supply malformed params outside the argparse boundary.
    args = _cli_args(
        feature_name="heating.curve.slope", command_name="setCurve", params=params
    )

    # Act and assert: The malformed argument raises with its reason.
    with pytest.raises(ValueError, match=message):
        await cmd_exec(args)


@pytest.mark.asyncio
async def test_async_main_rejects_malformed_credential_document(monkeypatch, tmp_path):
    """Malformed credentials should produce a failing CLI status without rewrites."""
    # Arrange: Point an initial login command at malformed credential data.
    token_file = tmp_path / "tokens.json"
    invalid_content = "{not-json"
    token_file.write_text(invalid_content, encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv", ["vi-client", "login", "--token-file", str(token_file)]
    )

    # Act: Invoke the command through its process-status boundary.
    exit_status = await async_main()

    # Assert: The command fails and leaves the malformed source untouched.
    assert exit_status == 1
    assert token_file.read_text(encoding="utf-8") == invalid_content


@pytest.mark.asyncio
async def test_cmd_exec_preserves_explicit_parameters(mock_cli_context, capsys):
    """CLI executes every explicitly supplied advanced command parameter."""
    # Arrange: Provide a writable feature and an explicit parameter set.
    args = _cli_args(
        feature_name="heating.curve.slope",
        command_name="setCurve",
        params=["slope=1.4", "shift=0"],
    )
    feature = _feature(control=_control())
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.execute_command.return_value = MagicMock(
        success=True, message="OK", reason=None
    )

    with _patched_cli_context(mock_cli_context):
        # Act: Execute the explicit command through the CLI.
        assert await cmd_exec(args) is True

    # Assert: The client receives the hydrated device and exact parameters.
    assert mock_cli_context.client.get_features.call_args.args[0].id == "DEV1"
    mock_cli_context.client.execute_command.assert_awaited_once_with(
        feature, {"slope": 1.4, "shift": 0}
    )
    assert "Success!" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cmd_exec_rejects_malformed_parameter_arguments(capsys):
    """Unparseable parameter arguments should reject before any request."""
    # Arrange: Supply one parameter without the key=value shape.
    args = _cli_args(
        feature_name="heating.curve.slope",
        command_name="setCurve",
        params=["not-key-value"],
    )

    # Act: Attempt the explicit command.
    assert await cmd_exec(args) is False

    # Assert: The CLI explains the parameter shape failure.
    assert "Error parsing parameters:" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cmd_exec_rejects_read_only_features(mock_cli_context, capsys):
    """Read-only features should reject explicit commands."""
    # Arrange: Provide a feature without command metadata and no params argument.
    args = _cli_args(feature_name="heating.curve.slope", command_name="setCurve")
    mock_cli_context.client.get_features.return_value = [_feature(control=None)]

    with _patched_cli_context(mock_cli_context):
        # Act: Attempt the explicit command.
        assert await cmd_exec(args) is False

    # Assert: The CLI explains the read-only state without executing.
    assert "is read-only" in capsys.readouterr().out
    mock_cli_context.client.execute_command.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_exec_reports_missing_features(mock_cli_context, capsys):
    """Unknown feature names should reject explicit commands."""
    # Arrange: Return no features for the requested name.
    args = _cli_args(feature_name="missing.feature", command_name="setCurve", params=[])
    mock_cli_context.client.get_features.return_value = []

    with _patched_cli_context(mock_cli_context):
        # Act: Attempt the explicit command.
        assert await cmd_exec(args) is False

    # Assert: The CLI names the missing feature.
    assert "Error: Feature 'missing.feature' not found." in capsys.readouterr().out
    mock_cli_context.client.execute_command.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_exec_failed_result_without_details(mock_cli_context, capsys):
    """Failed explicit commands without details should print the bare failure."""
    # Arrange: Provide a writable feature whose command fails without details.
    args = _cli_args(
        feature_name="heating.curve.slope",
        command_name="setCurve",
        params=["slope=1.4"],
    )
    mock_cli_context.client.get_features.return_value = [_feature(control=_control())]
    mock_cli_context.client.execute_command.return_value = MagicMock(
        success=False, message=None, reason=None
    )

    with _patched_cli_context(mock_cli_context):
        # Act: Execute the explicit command.
        assert await cmd_exec(args) is False

    # Assert: The failure prints without message or reason lines.
    captured = capsys.readouterr()
    assert "Failed!" in captured.out
    assert "Message:" not in captured.out
    assert "Reason:" not in captured.out


@pytest.mark.asyncio
async def test_cmd_exec_rejects_foreign_command_names(mock_cli_context, capsys):
    """Explicit commands should only run a feature's primary command."""
    # Arrange: Request a command name the feature does not expose.
    args = _cli_args(
        feature_name="heating.curve.slope",
        command_name="otherCommand",
        params=["slope=1.4"],
    )
    mock_cli_context.client.get_features.return_value = [_feature(control=_control())]

    with _patched_cli_context(mock_cli_context):
        # Act: Attempt the foreign command.
        assert await cmd_exec(args) is False

    # Assert: The CLI names the expected command without executing.
    captured = capsys.readouterr()
    assert "expects command 'setCurve'" in captured.out
    mock_cli_context.client.execute_command.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_exec_reports_failed_command_results(mock_cli_context, capsys):
    """Failed explicit commands should print the response details."""
    # Arrange: Provide a writable feature whose command fails at the API.
    args = _cli_args(
        feature_name="heating.curve.slope",
        command_name="setCurve",
        params=["slope=1.4"],
    )
    mock_cli_context.client.get_features.return_value = [_feature(control=_control())]
    mock_cli_context.client.execute_command.return_value = MagicMock(
        success=False, message="API rejected", reason="out of range"
    )

    with _patched_cli_context(mock_cli_context):
        # Act: Execute the explicit command.
        assert await cmd_exec(args) is False

    # Assert: The failure surfaces the response details.
    captured = capsys.readouterr()
    assert "Failed!" in captured.out
    assert "Message: API rejected" in captured.out
    assert "Reason: out of range" in captured.out


@pytest.mark.asyncio
async def test_cmd_exec_reports_not_found_errors(mock_cli_context, capsys):
    """Not-found explicit commands should surface the API message."""
    # Arrange: Make the explicit command fail with a not-found API error.
    args = _cli_args(
        feature_name="heating.curve.slope",
        command_name="setCurve",
        params=["slope=1.4"],
    )
    mock_cli_context.client.get_features.return_value = [_feature(control=_control())]
    mock_cli_context.client.execute_command.side_effect = ViNotFoundError("device gone")

    with _patched_cli_context(mock_cli_context):
        # Act: Execute the explicit command.
        assert await cmd_exec(args) is False

    # Assert: The CLI reports the not-found failure.
    assert "Not found: device gone" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cmd_exec_reports_unexpected_errors(mock_cli_context):
    """Unexpected explicit-command failures should fail without a traceback."""
    # Arrange: Make the explicit command fail with an unexpected error.
    args = _cli_args(
        feature_name="heating.curve.slope",
        command_name="setCurve",
        params=["slope=1.4"],
    )
    mock_cli_context.client.get_features.return_value = [_feature(control=_control())]
    mock_cli_context.client.execute_command.side_effect = RuntimeError("boom")

    with _patched_cli_context(mock_cli_context):
        # Act: Execute the explicit command.
        assert await cmd_exec(args) is False


@pytest.mark.asyncio
async def test_cmd_exec_rejects_non_json_parameter_arguments():
    """Artificial namespaces with non-JSON params reject as local errors."""
    # Arrange: Supply a params argument JSON cannot represent.
    args = _cli_args(
        feature_name="heating.curve.slope",
        command_name="setCurve",
        params=object(),
    )

    # Act and assert: The local contract violation surfaces as a ValueError.
    with pytest.raises(ValueError, match="Command parameters"):
        await cmd_exec(args)


@pytest.mark.asyncio
async def test_cmd_exec_reports_validation_errors(mock_cli_context, capsys):
    """Validation errors from explicit commands should print their message."""
    # Arrange: Make the explicit command fail with a validation error.
    args = _cli_args(
        feature_name="heating.curve.slope",
        command_name="setCurve",
        params=["slope=invalid"],
    )
    mock_cli_context.client.get_features.return_value = [_feature(control=_control())]
    mock_cli_context.client.execute_command.side_effect = ViValidationError(
        "Simulated Validation Error"
    )

    with _patched_cli_context(mock_cli_context):
        # Act: Execute the explicit command.
        assert await cmd_exec(args) is False

    # Assert: The CLI reports the validation failure.
    assert "Validation failed: Simulated Validation Error" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cmd_set_hydrates_required_command_dependencies(mock_cli_context):
    """CLI writes should provide sibling values required by a command."""
    # Arrange: Provide heating-curve features that share the setCurve command.
    args = _cli_args(feature_name="heating.curve.slope", value="1.4")
    control = _control(required_params=["shift", "slope"])
    slope = _feature(control=control)
    shift = _feature(name="heating.curve.shift", value=4.0, control=control)
    mock_cli_context.client.get_features.return_value = [slope, shift]
    mock_cli_context.client.set_feature.return_value = _successful_set_result()

    with _patched_cli_context(mock_cli_context):
        # Act: Set the slope through the CLI command.
        await cmd_set(args)

    # Assert: The write receives a device containing the sibling shift value.
    command_device = mock_cli_context.client.set_feature.call_args.args[0]
    assert command_device.get_feature("heating.curve.slope") is slope
    assert command_device.get_feature("heating.curve.shift") is shift
    assert mock_cli_context.client.get_features.call_args.kwargs == {}


@pytest.mark.asyncio
async def test_cmd_list_features_json(mock_cli_context, capsys):
    """Listing features with JSON output should print the feature names."""
    # Arrange: Provide two features and request JSON output.
    args = _cli_args(enabled=False, values=False, json=True)
    mock_cli_context.client.get_features.return_value = [
        _feature(name="f1", value=1),
        _feature(name="f2", value=2),
    ]

    with _patched_cli_context(mock_cli_context):
        # Act: List the features with JSON output.
        assert await cmd_list_features(args) is True

    # Assert: stdout carries a JSON list of feature names.
    assert json.loads(capsys.readouterr().out) == ["f1", "f2"]


@pytest.mark.asyncio
async def test_cmd_list_features_json_with_values(mock_cli_context, capsys):
    """Listing values with JSON output should print value documents."""
    # Arrange: Provide one writable feature and request JSON value output.
    args = _cli_args(enabled=False, values=True, json=True)
    mock_cli_context.client.get_features.return_value = [
        _feature(name="f1", value=1.4, control=_control())
    ]

    with _patched_cli_context(mock_cli_context):
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


@pytest.mark.asyncio
async def test_cmd_list_features_enabled(mock_cli_context):
    """The enabled flag should request only enabled features."""
    # Arrange: Provide one feature and request the enabled filter.
    args = _cli_args(enabled=True, values=False, json=True)
    mock_cli_context.client.get_features.return_value = [
        _feature(name="f_enabled", value=1)
    ]

    with _patched_cli_context(mock_cli_context):
        # Act: List only enabled features.
        assert await cmd_list_features(args) is True

    # Assert: The read requests the enabled filter for the context device.
    call_args = mock_cli_context.client.get_features.call_args
    assert call_args.args[0].id == "DEV1"
    assert call_args.kwargs["only_enabled"] is True


@pytest.mark.asyncio
async def test_cmd_list_features_values_table(mock_cli_context, capsys):
    """The values flag should print a human-readable table with writable marks."""
    # Arrange: Provide a writable feature and a long read-only value.
    args = _cli_args(enabled=False, values=True, json=False)
    long_value = "x" * 90
    mock_cli_context.client.get_features.return_value = [
        _feature(name="writable.feature", value=1.4, control=_control()),
        _feature(name="long.feature", value=long_value),
    ]

    with _patched_cli_context(mock_cli_context):
        # Act: List the feature values as a table.
        assert await cmd_list_features(args) is True

    # Assert: The table shows the count, names, truncation, and writable marks.
    captured = capsys.readouterr()
    assert "Found 2 Features for device DEV1:" in captured.out
    assert "writable.feature" in captured.out
    assert "..." in captured.out
    assert "(* = writable)" in captured.out


@pytest.mark.asyncio
async def test_cmd_list_features_simple_list(mock_cli_context, capsys):
    """Default listing should print one line per feature name."""
    # Arrange: Provide one feature and request no flags.
    args = _cli_args(enabled=False, values=False, json=False)
    mock_cli_context.client.get_features.return_value = [_feature(name="f1", value=1)]

    with _patched_cli_context(mock_cli_context):
        # Act: List the features without flags.
        assert await cmd_list_features(args) is True

    # Assert: The output prints the device-scoped feature name list.
    captured = capsys.readouterr()
    assert "Found 1 Features for device DEV1:" in captured.out
    assert "- f1" in captured.out


@pytest.mark.asyncio
async def test_cmd_list_features_reports_unexpected_errors(mock_cli_context):
    """Unexpected listing failures should fail the command without a traceback."""
    # Arrange: Make the feature listing fail with an unexpected error.
    args = _cli_args(enabled=False, values=False, json=False)
    mock_cli_context.client.get_features.side_effect = RuntimeError("boom")

    with _patched_cli_context(mock_cli_context):
        # Act: List the features.
        assert await cmd_list_features(args) is False


@pytest.mark.parametrize(
    "output_flags",
    [["--json"], ["--values", "--json"]],
    ids=["names-only", "with-values"],
)
@pytest.mark.asyncio
async def test_async_main_fixture_device_json_output_is_machine_readable(
    monkeypatch, capsys, output_flags
):
    """The real CLI path must reserve stdout for JSON feature data."""
    # Arrange: Use a bundled fixture without replacing client context setup.
    monkeypatch.setattr(
        "sys.argv",
        [
            "vi-client",
            "list-features",
            "--fixture-device",
            "Vitocal250A",
            *output_flags,
        ],
    )

    # Act: Invoke the parser, dispatcher, and context setup through the CLI path.
    exit_status = await async_main()

    # Assert: JSON output is parseable and setup diagnostics stay on stderr.
    captured = capsys.readouterr()
    assert exit_status == 0
    features = json.loads(captured.out)
    assert features
    if output_flags == ["--json"]:
        assert all(isinstance(feature, str) for feature in features)
    else:
        assert all("value" in feature for feature in features)
    assert "Using Fixture Device: Vitocal250A" in captured.err


@pytest.mark.asyncio
async def test_async_main_json_setup_error_is_written_to_stderr(
    monkeypatch, capsys, tmp_path
):
    """A JSON-mode setup error must preserve stdout for a payload document."""
    # Arrange: Request live JSON output without saved or explicit credentials.
    monkeypatch.setattr(
        "sys.argv",
        [
            "vi-client",
            "list-features",
            "--json",
            "--token-file",
            str(tmp_path / "tokens.json"),
        ],
    )

    # Act: Invoke the CLI entry path.
    with pytest.raises(SystemExit) as error:
        await async_main()

    # Assert: The failed setup leaves stdout empty and preserves the diagnostic.
    captured = capsys.readouterr()
    assert error.value.code == 1
    assert captured.out == ""
    assert "Error: Client ID not found." in captured.err


def _installation() -> Installation:
    """Build one installation discovery snapshot."""
    return Installation(id="123", description="Home", alias="MyHome", address={})


def _gateway() -> Gateway:
    """Build one gateway discovery snapshot."""
    return Gateway(serial="GW1", version="1.0", status="ok", installation_id="123")


def _discovered_device() -> Device:
    """Build one device discovery snapshot."""
    return Device(
        id="0",
        gateway_serial="GW1",
        installation_id="123",
        model_id="Test",
        device_type="heating",
        status="ok",
    )


@pytest.mark.asyncio
async def test_cmd_list_devices_prints_account_hierarchy(mock_cli_context, capsys):
    """Listing devices should print installations, gateways, and devices."""
    # Arrange: Script one installation with one gateway and device.
    args = _cli_args()
    mock_cli_context.client.get_installations.return_value = [_installation()]
    mock_cli_context.client.get_gateways.return_value = [_gateway()]
    mock_cli_context.client.get_devices.return_value = [_discovered_device()]

    with _patched_cli_context(mock_cli_context):
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


@pytest.mark.asyncio
async def test_cmd_list_devices_reports_unexpected_errors(mock_cli_context):
    """Unexpected listing failures should fail the command without a traceback."""
    # Arrange: Make the installation listing fail with an unexpected error.
    args = _cli_args()
    mock_cli_context.client.get_installations.side_effect = RuntimeError("boom")

    with _patched_cli_context(mock_cli_context):
        # Act: List the account hierarchy.
        assert await cmd_list_devices(args) is False


@pytest.mark.asyncio
async def test_cmd_list_devices_does_not_require_device_context(capsys):
    """List devices without installation, gateway, or device IDs."""
    # Arrange: Construct the real CLI context without any device-specific IDs.
    args = _cli_args(client_id="test_id", redirect_uri="http://localhost")

    with (
        patch("vi_api_client.cli.ViClient") as mock_client_class,
        patch("vi_api_client.cli.OAuth"),
        patch(
            "vi_api_client.cli.create_session", new_callable=AsyncMock
        ) as mock_session,
    ):
        mock_session.return_value.__aenter__.return_value = MagicMock()
        client = mock_client_class.return_value
        client.get_installations = AsyncMock(return_value=[_installation()])
        client.get_gateways = AsyncMock(return_value=[_gateway()])
        client.get_devices = AsyncMock(return_value=[_discovered_device()])

        # Act: List all account devices without choosing a device context first.
        assert await cmd_list_devices(args) is True

        # Assert: The command queries the account hierarchy directly.
        client.get_installations.assert_awaited_once()
        client.get_gateways.assert_awaited_once()
        client.get_devices.assert_awaited_once_with("123", "GW1")

    assert "Found 1 installations" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_cmd_list_writable_prints_constraints(mock_cli_context, capsys):
    """Listing writable features should print command and constraint details."""
    # Arrange: Provide one writable feature with numeric constraints.
    args = _cli_args()
    feature = _feature(
        name="heating.circuits.0.heating.curve.slope",
        control=_control(min=0.2, max=3.5, step=0.1),
    )
    mock_cli_context.client.get_features.return_value = [feature]

    with _patched_cli_context(mock_cli_context):
        # Act: List the writable features.
        assert await cmd_list_writable(args) is True

    # Assert: The output names the feature, command, and constraints.
    captured = capsys.readouterr()
    assert "heating.circuits.0.heating.curve.slope" in captured.out
    assert "setCurve" in captured.out
    assert "min: 0.2" in captured.out


@pytest.mark.asyncio
async def test_cmd_list_writable_prints_string_and_enum_constraints(
    mock_cli_context, capsys
):
    """String and enum command constraints should print beside numeric ones."""
    # Arrange: Provide a writable feature with every constraint kind.
    args = _cli_args()
    feature = _feature(
        name="heating.program",
        control=_control(
            options=["auto", "manual"],
            min_length=1,
            max_length=10,
            pattern="^[a-z]+$",
        ),
    )
    mock_cli_context.client.get_features.return_value = [feature]

    with _patched_cli_context(mock_cli_context):
        # Act: List the writable features.
        assert await cmd_list_writable(args) is True

    # Assert: Every constraint kind appears in the constraint line.
    captured = capsys.readouterr()
    assert "options: ('auto', 'manual')" in captured.out
    assert "min_length: 1" in captured.out
    assert "max_length: 10" in captured.out
    assert "pattern: ^[a-z]+$" in captured.out


@pytest.mark.asyncio
async def test_cmd_list_writable_omits_absent_constraints(mock_cli_context, capsys):
    """Writable features without constraints should print without the line."""
    # Arrange: Provide a writable feature whose control declares no constraints.
    args = _cli_args()
    feature = _feature(name="heating.program", control=_control())
    mock_cli_context.client.get_features.return_value = [feature]

    with _patched_cli_context(mock_cli_context):
        # Act: List the writable features.
        assert await cmd_list_writable(args) is True

    # Assert: The feature prints with its command but no constraint line.
    captured = capsys.readouterr()
    assert "heating.program" in captured.out
    assert "setCurve" in captured.out
    assert "Constraints:" not in captured.out


@pytest.mark.asyncio
async def test_cmd_list_writable_reports_unexpected_errors(mock_cli_context):
    """Unexpected listing failures should fail the command without a traceback."""
    # Arrange: Make the feature listing fail with an unexpected error.
    args = _cli_args()
    mock_cli_context.client.get_features.side_effect = RuntimeError("boom")

    with _patched_cli_context(mock_cli_context):
        # Act: List the writable features.
        assert await cmd_list_writable(args) is False


@pytest.mark.asyncio
async def test_cmd_list_fixture_devices_prints_the_bundled_catalog(capsys):
    """Listing fixture devices should print the real offline catalog."""
    # Arrange: Read the shipped catalog the command is expected to list.
    args = _cli_args()
    expected_devices = FixtureViClient.get_available_fixture_devices()

    # Act: List the fixture devices without mocking the catalog.
    assert await cmd_list_fixture_devices(args) is True

    # Assert: The output prints the header and every bundled device.
    captured = capsys.readouterr()
    assert "Available Fixture Devices:" in captured.out
    for device in expected_devices:
        assert f"- {device}" in captured.out
    assert "- Vitodens200W" in captured.out


@pytest.mark.asyncio
async def test_dispatch_returns_nonzero_for_failed_command():
    """The command dispatcher should map handler failures to exit status 1."""
    # Arrange: Dispatch a command whose handler reports failure.
    args = Namespace(command="set")

    with patch("vi_api_client.cli.cmd_set", new_callable=AsyncMock) as mock_cmd_set:
        mock_cmd_set.return_value = False

        # Act and assert: The dispatcher maps the failure to status one.
        assert await _dispatch_command(args) == 1


@pytest.mark.asyncio
async def test_dispatch_returns_two_for_unknown_command():
    """The command dispatcher should reject unknown commands with status 2."""
    # Arrange: Dispatch a command name that has no handler.
    args = Namespace(command="not-a-command")

    # Act and assert: The dispatcher refuses the unknown command.
    assert await _dispatch_command(args) == 2


@pytest.mark.asyncio
async def test_dispatch_login_requires_a_configured_client_id(
    monkeypatch, tmp_path, capsys
):
    """Dispatching login without any client ID should fail with guidance."""
    # Arrange: Provide neither an argument, environment, nor stored client ID.
    token_file = tmp_path / "tokens.json"
    args = Namespace(
        command="login",
        client_id=None,
        token_file=str(token_file),
    )
    monkeypatch.delenv("VIESSMANN_CLIENT_ID", raising=False)

    # Act: Dispatch the login command.
    exit_status = await _dispatch_command(args)

    # Assert: The pre-check fails with status one and an actionable hint.
    assert exit_status == 1
    assert "--client-id is required" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_dispatch_login_proceeds_with_a_client_id(monkeypatch, tmp_path):
    """Dispatching login with a client ID should reach the login handler."""
    # Arrange: Provide a client ID and replace the login handler.
    token_file = tmp_path / "tokens.json"
    args = Namespace(
        command="login",
        client_id="configured-client",
        token_file=str(token_file),
    )

    with patch("vi_api_client.cli.cmd_login", new_callable=AsyncMock) as mock_cmd_login:
        mock_cmd_login.return_value = True

        # Act: Dispatch the login command.
        exit_status = await _dispatch_command(args)

    # Assert: The pre-check passes and the handler result maps to status zero.
    mock_cmd_login.assert_awaited_once_with(args)
    assert exit_status == 0


@pytest.mark.asyncio
async def test_dispatch_login_accepts_a_saved_client_id(monkeypatch, tmp_path):
    """Dispatching login should accept a client ID saved in the document."""
    # Arrange: Store a client ID without an argument or environment value.
    token_file = tmp_path / "tokens.json"
    token_file.write_text('{"client_id": "saved-client"}', encoding="utf-8")
    args = Namespace(command="login", client_id=None, token_file=str(token_file))
    monkeypatch.delenv("VIESSMANN_CLIENT_ID", raising=False)

    with patch("vi_api_client.cli.cmd_login", new_callable=AsyncMock) as mock_cmd_login:
        mock_cmd_login.return_value = True

        # Act: Dispatch the login command.
        exit_status = await _dispatch_command(args)

    # Assert: The saved client ID satisfies the pre-check.
    mock_cmd_login.assert_awaited_once_with(args)
    assert exit_status == 0


def test_main_exits_with_async_command_status():
    """The console entry point should expose the asynchronous exit status."""
    with (
        patch(
            "vi_api_client.cli.async_main", new_callable=AsyncMock
        ) as mock_async_main,
        pytest.raises(SystemExit) as exit_error,
    ):
        mock_async_main.return_value = 1
        main()

    assert exit_error.value.code == 1


@pytest.mark.asyncio
async def test_async_main_without_command_prints_help(monkeypatch, capsys):
    """Invoking the CLI without a command should print help and exit zero."""
    # Arrange: Parse an argument list without a command.
    monkeypatch.setattr("sys.argv", ["vi-client"])

    # Act: Invoke the CLI entry path.
    exit_status = await async_main()

    # Assert: Help is printed and the process status stays successful.
    assert exit_status == 0
    assert "usage:" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_async_main_maps_keyboard_interrupt_to_130(monkeypatch):
    """Interrupting a command should map to the conventional exit status 130."""
    # Arrange: Interrupt the dispatcher while it runs a command.
    monkeypatch.setattr("sys.argv", ["vi-client", "list-fixture-devices"])

    with patch(
        "vi_api_client.cli._dispatch_command", new_callable=AsyncMock
    ) as mock_dispatch:
        mock_dispatch.side_effect = KeyboardInterrupt

        # Act: Invoke the CLI entry path.
        exit_status = await async_main()

    # Assert: The interrupt maps to the conventional terminal status.
    assert exit_status == 130


@pytest.mark.asyncio
async def test_async_main_maps_unexpected_errors_to_one(monkeypatch):
    """Unexpected dispatcher failures should map to exit status one."""
    # Arrange: Fail the dispatcher with an unexpected error.
    monkeypatch.setattr("sys.argv", ["vi-client", "list-fixture-devices"])

    with patch(
        "vi_api_client.cli._dispatch_command", new_callable=AsyncMock
    ) as mock_dispatch:
        mock_dispatch.side_effect = RuntimeError("boom")

        # Act: Invoke the CLI entry path.
        exit_status = await async_main()

    # Assert: The unexpected failure maps to a generic failure status.
    assert exit_status == 1


@pytest.mark.parametrize(
    ("extra_argv", "expected_level"),
    [
        ([], logging.INFO),
        (["--verbose"], logging.DEBUG),
    ],
)
@pytest.mark.asyncio
async def test_async_main_configures_logging_after_parsing(
    monkeypatch, extra_argv, expected_level
):
    """The CLI should configure root logging itself, honoring the --verbose flag."""
    # Arrange: Request a fixture listing with or without verbose logging.
    monkeypatch.setattr("sys.argv", ["vi-client", "list-fixture-devices", *extra_argv])
    configured_levels = []
    monkeypatch.setattr(
        logging,
        "basicConfig",
        lambda **kwargs: configured_levels.append(kwargs.get("level")),
    )

    # Act: Invoke the parser and dispatcher through the CLI entry path.
    exit_status = await async_main()

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


def _event_page() -> EventHistoryPage:
    """Build one complete final event history page with provider details."""
    feature_changed = InstallationEvent(
        event_type="feature-changed",
        created_at="2026-09-20T10:15:30.000Z",
        event_timestamp="2026-09-20T10:15:30.000Z",
        gateway_serial="7630175843100101",
        body={
            "featureName": "heating.dhw.temperature.main",
            "commandName": "setTargetTemperature",
            "commandBody": {"temperature": 55},
        },
        fields={
            "eventType": "feature-changed",
            "createdAt": "2026-09-20T10:15:30.000Z",
            "eventTimestamp": "2026-09-20T10:15:30.000Z",
            "gatewaySerial": "7630175843100101",
            "body": {
                "featureName": "heating.dhw.temperature.main",
                "commandName": "setTargetTemperature",
                "commandBody": {"temperature": 55},
            },
        },
    )
    gateway_online = InstallationEvent(
        event_type="gateway-online",
        created_at="2026-09-19T22:41:05.123Z",
        event_timestamp="2026-09-19T22:41:03.000Z",
        gateway_serial="7630175843100101",
        body={"online": True},
        fields={
            "eventType": "gateway-online",
            "createdAt": "2026-09-19T22:41:05.123Z",
            "eventTimestamp": "2026-09-19T22:41:03.000Z",
            "gatewaySerial": "7630175843100101",
            "body": {"online": True},
        },
    )
    return EventHistoryPage(events=[feature_changed, gateway_online], next_cursor=None)


@pytest.mark.asyncio
async def test_cmd_list_events_prints_readable_summary(mock_cli_context, capsys):
    """The event summary should report the window, events, and cursor."""
    # Arrange: Provide one fixture event page for a seven day window.
    args = _cli_args(days=7)
    mock_cli_context.client.get_event_history.return_value = _event_page()

    with _patched_cli_context(mock_cli_context):
        # Act: List the first page through the CLI.
        assert await cmd_list_events(args) is True

    # Assert: One aligned line per event; a single gateway adds no column.
    captured = capsys.readouterr()
    assert "Found 2 event(s) for installation 99 (last 7 days):" in captured.out
    event_lines = [
        line
        for line in captured.out.splitlines()
        if line.startswith("- 2026-09-20T10:15:30.000Z")
    ]
    assert len(event_lines) == 1
    assert event_lines[0].startswith(
        "- 2026-09-20T10:15:30.000Z feature-changed        "
        "heating.dhw.temperature.main (setTargetTemperature"
    )
    # The full command payload exceeds the line width and is truncated.
    assert len(event_lines[0]) == 120
    assert event_lines[0].endswith("...")
    assert '- 2026-09-19T22:41:03.000Z gateway-online         {"online": true}' in (
        captured.out
    )
    assert "7630175843100101" not in captured.out
    assert (
        "Earliest event: 2026-09-19T22:41:03.000Z; "
        "latest event: 2026-09-20T10:15:30.000Z" in captured.out
    )
    assert "Pagination completed after 1 page(s)" in captured.out
    assert "safety limit" not in captured.out
    mock_cli_context.client.get_event_history.assert_awaited_once_with(
        "99", days=7, limit=None
    )


@pytest.mark.asyncio
async def test_cmd_list_events_json_emits_one_document(mock_cli_context, capsys):
    """JSON output should keep complete events and pagination metadata."""
    # Arrange: Request the machine-readable form with a page limit.
    args = _cli_args(days=7, limit=10, json=True)
    mock_cli_context.client.get_event_history.return_value = _event_page()

    with _patched_cli_context(mock_cli_context):
        # Act: List the first page as JSON.
        assert await cmd_list_events(args) is True

    # Assert: One JSON document carries full events and the cursor.
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document == {
        "installationId": "99",
        "events": [
            {
                "eventType": "feature-changed",
                "createdAt": "2026-09-20T10:15:30.000Z",
                "eventTimestamp": "2026-09-20T10:15:30.000Z",
                "gatewaySerial": "7630175843100101",
                "body": {
                    "featureName": "heating.dhw.temperature.main",
                    "commandName": "setTargetTemperature",
                    "commandBody": {"temperature": 55},
                },
            },
            {
                "eventType": "gateway-online",
                "createdAt": "2026-09-19T22:41:05.123Z",
                "eventTimestamp": "2026-09-19T22:41:03.000Z",
                "gatewaySerial": "7630175843100101",
                "body": {"online": True},
            },
        ],
        "eventCount": 2,
        "earliestEventTimestamp": "2026-09-19T22:41:03.000Z",
        "latestEventTimestamp": "2026-09-20T10:15:30.000Z",
        "pagesFetched": 1,
        "paginationComplete": True,
        "nextCursor": None,
    }
    mock_cli_context.client.get_event_history.assert_awaited_once_with(
        "99", days=7, limit=10
    )


@pytest.mark.asyncio
async def test_cmd_list_events_auto_selects_first_installation(
    mock_cli_context, capsys
):
    """Without an installation ID the first account installation is used."""
    # Arrange: Leave the installation scope unresolved in the context.
    args = _cli_args(days=7)
    mock_cli_context.inst_id = None
    installation = Installation(
        id="12345", description="Home", alias="home", address={}
    )
    mock_cli_context.client.get_installations.return_value = [installation]
    mock_cli_context.client.get_event_history.return_value = _event_page()

    with _patched_cli_context(mock_cli_context):
        # Act: List events without an explicit installation ID.
        assert await cmd_list_events(args) is True

    # Assert: The first installation scopes the event history request.
    mock_cli_context.client.get_event_history.assert_awaited_once_with(
        "12345", days=7, limit=None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "argument_name"),
    [
        ({"days": 0}, "days"),
        ({"days": -2}, "days"),
        ({"days": 7, "limit": 0}, "limit"),
        ({"days": 7, "limit": -5}, "limit"),
        ({"days": 7, "max_pages": 0}, "max_pages"),
        ({"days": 7, "max_pages": -1}, "max_pages"),
    ],
)
async def test_cmd_list_events_rejects_non_positive_windows(
    arguments: dict, argument_name: str, capsys
):
    """Non-positive windows and limits should fail without a request."""
    # Arrange: Request an invalid rolling window or page limit.
    args = _cli_args(**arguments)

    # Act: The CLI rejects the argument before any client interaction.
    assert await cmd_list_events(args) is False

    # Assert: The error names the offending argument.
    captured = capsys.readouterr()
    assert f"'{argument_name}' must be a positive integer" in captured.out


@pytest.mark.asyncio
async def test_cmd_list_events_reports_unexpected_errors(mock_cli_context):
    """Event history failures should surface as a failed command."""
    # Arrange: Let the client boundary raise an unexpected error.
    args = _cli_args(days=7)
    mock_cli_context.client.get_event_history.side_effect = RuntimeError("boom")

    with _patched_cli_context(mock_cli_context):
        # Act and assert: The command reports failure instead of raising.
        assert await cmd_list_events(args) is False


@pytest.mark.asyncio
async def test_async_main_list_events_fixture_json_is_machine_readable(
    monkeypatch, capsys
):
    """The real CLI path must emit one JSON document for fixture events."""
    # Arrange: Use the bundled fixture through the real parser and dispatch.
    monkeypatch.setattr(
        "sys.argv",
        [
            "vi-client",
            "list-events",
            "--fixture-device",
            "Vitodens200W",
            "--days",
            "7",
            "--json",
        ],
    )

    # Act: Invoke the parser, dispatcher, and fixture context setup.
    exit_status = await async_main()

    # Assert: The document preserves full events and pagination metadata.
    captured = capsys.readouterr()
    assert exit_status == 0
    document = json.loads(captured.out)
    assert document["installationId"] == "99999"
    assert len(document["events"]) == 3
    first_event = document["events"][0]
    assert first_event["eventType"] == "feature-changed"
    assert first_event["body"] == {
        "featureName": "heating.dhw.temperature.main",
        "commandName": "setTargetTemperature",
        "commandBody": {"temperature": 55},
    }
    assert document["eventCount"] == 3
    assert document["pagesFetched"] == 2
    assert document["paginationComplete"] is True
    assert document["nextCursor"] is None
    assert document["earliestEventTimestamp"] == "2026-09-18T08:02:10.500Z"
    assert document["latestEventTimestamp"] == "2026-09-20T10:15:30.000Z"
    assert "Using Fixture Device: Vitodens200W" in captured.err


@pytest.mark.asyncio
async def test_cmd_list_events_keeps_json_stdout_clean_on_rejected_days(capsys):
    """JSON mode should report rejected windows on stderr only."""
    # Arrange: Request an invalid window in machine-readable mode.
    args = _cli_args(days=0, json=True)

    # Act: The CLI rejects the argument before any client interaction.
    assert await cmd_list_events(args) is False

    # Assert: Standard output stays empty for machine consumers.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "'days' must be a positive integer" in captured.err


@pytest.mark.asyncio
async def test_cmd_list_events_reports_malformed_pages(mock_cli_context):
    """Malformed event history pages should surface as a failed command."""
    # Arrange: Let the client boundary raise the response contract error.
    args = _cli_args(days=7)
    mock_cli_context.client.get_event_history.side_effect = ViResponseError(
        "Event history response data must be a list"
    )

    with _patched_cli_context(mock_cli_context):
        # Act and assert: The command reports failure instead of raising.
        assert await cmd_list_events(args) is False


@pytest.mark.asyncio
async def test_cmd_list_events_follows_cursors_across_pages(mock_cli_context, capsys):
    """The traversal should continue with the cursor, not the window."""
    # Arrange: Serve one continuation page followed by a final page.
    first_page = replace(_event_page(), next_cursor="cursor-token")
    args = _cli_args(days=7)
    mock_cli_context.client.get_event_history.side_effect = [
        first_page,
        EventHistoryPage(events=[], next_cursor=None),
    ]

    with _patched_cli_context(mock_cli_context):
        # Act: List the complete window through the CLI.
        assert await cmd_list_events(args) is True

    # Assert: The second request carries only the continuation cursor.
    captured = capsys.readouterr()
    assert "Found 2 event(s) for installation 99 (last 7 days):" in captured.out
    assert "Pagination completed after 2 page(s)" in captured.out
    awaited_calls = mock_cli_context.client.get_event_history.await_args_list
    assert len(awaited_calls) == 2
    assert awaited_calls[0].args == ("99",)
    assert awaited_calls[0].kwargs == {"days": 7, "limit": None}
    assert awaited_calls[1].args == ("99",)
    assert awaited_calls[1].kwargs == {"cursor": "cursor-token", "limit": None}


@pytest.mark.asyncio
async def test_cmd_list_events_stops_at_the_safety_limit(mock_cli_context, capsys):
    """A remaining cursor at the safety limit should mark the result incomplete."""
    # Arrange: Every served page reports a further cursor.
    continuing_page = replace(_event_page(), next_cursor="cursor-token")
    args = _cli_args(days=7, max_pages=2)
    mock_cli_context.client.get_event_history.side_effect = [
        continuing_page,
        continuing_page,
        continuing_page,
    ]

    with _patched_cli_context(mock_cli_context):
        # Act: Traverse with a two page safety limit.
        assert await cmd_list_events(args) is True

    # Assert: The summary reports the incomplete traversal explicitly.
    captured = capsys.readouterr()
    assert "Found 4 event(s) for installation 99 (last 7 days):" in captured.out
    assert "Stopped at the safety limit of 2 page(s)" in captured.out
    assert "next cursor: cursor-token" in captured.out
    assert "Pagination completed" not in captured.out
    assert mock_cli_context.client.get_event_history.await_count == 2


@pytest.mark.asyncio
async def test_cmd_list_events_json_marks_incomplete_traversals(
    mock_cli_context, capsys
):
    """JSON output should expose the remaining cursor of a limited traversal."""
    # Arrange: The single served page keeps reporting a further cursor.
    continuing_page = replace(_event_page(), next_cursor="cursor-token")
    args = _cli_args(days=7, max_pages=1, json=True)
    mock_cli_context.client.get_event_history.return_value = continuing_page

    with _patched_cli_context(mock_cli_context):
        # Act: Traverse one page in machine-readable mode.
        assert await cmd_list_events(args) is True

    # Assert: The document marks the result incomplete with its cursor.
    document = json.loads(capsys.readouterr().out)
    assert document["eventCount"] == 2
    assert document["pagesFetched"] == 1
    assert document["paginationComplete"] is False
    assert document["nextCursor"] == "cursor-token"


@pytest.mark.asyncio
@pytest.mark.parametrize("json_output", [False, True], ids=["readable", "json"])
async def test_cmd_list_events_reports_empty_windows(
    mock_cli_context, capsys, json_output
):
    """An empty returned window should complete without event timestamps."""
    # Arrange: The provider returns no events for the requested window.
    args = _cli_args(days=365, json=json_output)
    mock_cli_context.client.get_event_history.return_value = EventHistoryPage(
        events=[], next_cursor=None
    )

    with _patched_cli_context(mock_cli_context):
        # Act: List a winter-length window without events.
        assert await cmd_list_events(args) is True

    # Assert: Both outputs complete without timestamps.
    captured = capsys.readouterr()
    if json_output:
        document = json.loads(captured.out)
        assert document == {
            "installationId": "99",
            "events": [],
            "eventCount": 0,
            "earliestEventTimestamp": None,
            "latestEventTimestamp": None,
            "pagesFetched": 1,
            "paginationComplete": True,
            "nextCursor": None,
        }
    else:
        assert "Found 0 event(s) for installation 99 (last 365 days):" in captured.out
        assert "Earliest event:" not in captured.out
        assert "Pagination completed after 1 page(s)" in captured.out


@pytest.mark.asyncio
async def test_async_main_list_events_fixture_stops_at_one_page(monkeypatch, capsys):
    """The offline fixture traversal should honor the page safety limit."""
    # Arrange: Use the bundled fixture with a one page safety limit.
    monkeypatch.setattr(
        "sys.argv",
        [
            "vi-client",
            "list-events",
            "--fixture-device",
            "Vitodens200W",
            "--days",
            "7",
            "--max-pages",
            "1",
            "--json",
        ],
    )

    # Act: Invoke the parser, dispatcher, and fixture context setup.
    exit_status = await async_main()

    # Assert: The document marks the limited traversal incomplete.
    captured = capsys.readouterr()
    assert exit_status == 0
    document = json.loads(captured.out)
    assert document["eventCount"] == 3
    assert document["pagesFetched"] == 1
    assert document["paginationComplete"] is False
    assert document["nextCursor"] == "b3BhcXVlLWN1cnNvci10b2tlbg=="


def _detail_event(body: FeatureValue) -> InstallationEvent:
    """Build one event carrying only the body under test."""
    return InstallationEvent(
        event_type="detail",
        created_at="2026-09-20T10:15:30.000Z",
        event_timestamp="2026-09-20T10:15:30.000Z",
        gateway_serial=None,
        body=body,
        fields={},
    )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (None, None),
        (["a", "b"], '["a", "b"]'),
        ({"online": True}, '{"online": true}'),
        (
            {"featureName": "heating.dhw.temperature.main"},
            "heating.dhw.temperature.main",
        ),
        (
            {
                "featureName": "heating.dhw.temperature.main",
                "commandName": "setTargetTemperature",
            },
            "heating.dhw.temperature.main (setTargetTemperature)",
        ),
        (
            {
                "featureName": "heating.dhw.temperature.main",
                "commandName": "setTargetTemperature",
                "commandBody": {"temperature": 55},
            },
            'heating.dhw.temperature.main (setTargetTemperature {"temperature": 55})',
        ),
    ],
)
def test_format_event_details_summarizes_known_shapes(body: FeatureValue, expected):
    """Event body details should prefer known fields and fall back to JSON."""
    # Act: Format one event body for the readable summary.
    details = _format_event_details(_detail_event(body))

    # Assert: Known shapes are summarized and unknown bodies stay compact JSON.
    assert details == expected


def test_print_event_summary_truncates_long_lines(capsys):
    """Very long aligned event lines should stay within the summary width."""
    # Arrange: Provide one event with a feature name beyond the line width.
    event = _detail_event({"featureName": "x" * 200})
    window = EventHistoryWindow(events=[event], next_cursor=None, pages_fetched=1)

    # Act: Print the readable summary of the single event.
    _print_event_summary(window, "99", 7, 50)

    # Assert: The event line is truncated with an ellipsis marker.
    event_lines = [
        line for line in capsys.readouterr().out.splitlines() if line.startswith("- ")
    ]
    assert len(event_lines) == 1
    assert len(event_lines[0]) == 120
    assert event_lines[0].endswith("...")


def test_event_history_window_compares_timestamps_as_points_in_time():
    """Earliest and latest should order by instant, not lexically."""
    # Arrange: The offset timestamp is lexically later but temporally earlier
    # than the UTC timestamp (09:15:30+02:00 is 07:15:30Z; 08:15:30Z follows).
    temporally_early = replace(
        _detail_event(None), event_timestamp="2026-09-20T09:15:30.000+02:00"
    )
    temporally_late = replace(
        _detail_event(None), event_timestamp="2026-09-20T08:15:30.000Z"
    )
    window = EventHistoryWindow(
        events=[temporally_late, temporally_early], next_cursor=None, pages_fetched=1
    )

    # Act: Read the window's temporal extremes.

    # Assert: The offset timestamp is earliest despite sorting later as text.
    assert window.earliest_timestamp == "2026-09-20T09:15:30.000+02:00"
    assert window.latest_timestamp == "2026-09-20T08:15:30.000Z"


def test_event_history_window_falls_back_to_lexical_for_unparsable_timestamps():
    """Unparsable timestamps should fall back to a lexical comparison."""
    # Arrange: One provider timestamp is not parsable ISO-8601.
    parsable = replace(_detail_event(None), event_timestamp="2026-09-20T10:15:30.000Z")
    unparsable = replace(_detail_event(None), event_timestamp="not-a-timestamp")
    window = EventHistoryWindow(
        events=[parsable, unparsable], next_cursor=None, pages_fetched=1
    )

    # Act: Read the window's extremes.

    # Assert: The whole window uses the lexical fallback ordering.
    assert window.earliest_timestamp == "2026-09-20T10:15:30.000Z"
    assert window.latest_timestamp == "not-a-timestamp"


def test_print_event_summary_adds_gateway_column_for_multiple_gateways(capsys):
    """Events from several gateways should be disambiguated by a column."""
    # Arrange: Build one window with events from two different gateways.
    first = replace(
        _detail_event({"featureName": "heating.dhw.temperature.main"}),
        gateway_serial="7630175843100101",
    )
    second = replace(
        _detail_event({"featureName": "heating.dhw.oneTimeCharge"}),
        gateway_serial="8112200229931101",
    )
    window = EventHistoryWindow(
        events=[first, second], next_cursor=None, pages_fetched=1
    )

    # Act: Print the readable summary of the mixed-gateway window.
    _print_event_summary(window, "99", 7, 50)

    # Assert: Both serials appear in an aligned gateway column.
    output = capsys.readouterr().out
    assert "- 2026-09-20T10:15:30.000Z detail                 7630175843100101" in (
        output
    )
    assert "8112200229931101" in output
