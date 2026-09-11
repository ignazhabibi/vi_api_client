import json
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vi_api_client.cli import (
    _dispatch_command,
    async_main,
    cmd_exec,
    cmd_get_feature,
    cmd_list_devices,
    cmd_list_features,
    cmd_list_mock_devices,
    cmd_list_writable,
    cmd_login,
    cmd_set,
    get_client_config,
    main,
)
from vi_api_client.exceptions import ViValidationError
from vi_api_client.models import Device, Feature, FeatureControl, Gateway, Installation


@pytest.fixture
def mock_cli_context():
    """Fixture to mock setup_client_context functionality."""
    mock_client = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.client = mock_client
    mock_ctx.inst_id = 99
    mock_ctx.gw_serial = "GW1"
    mock_ctx.dev_id = "DEV1"
    return mock_ctx


@pytest.mark.asyncio
async def test_cmd_set_success(mock_cli_context, capsys):
    """Test successful feature setting via CLI."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        feature_name="heating.curve.slope",
        value="1.4",
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
    )

    # Mock get_feature return
    mock_control = FeatureControl(
        command_name="setCurve",
        param_name="slope",
        required_params=["slope"],
        parent_feature_name="heating.curve",
        uri="uri",
        value_type="number",
    )
    mock_feature = Feature(
        name="heating.curve.slope",
        value=1.0,
        unit=None,
        is_enabled=True,
        is_ready=True,
        control=mock_control,
    )

    # Mock return value call validation
    mock_cli_context.client.get_features.return_value = [mock_feature]
    mock_response = MagicMock()
    mock_response.success = True
    mock_response.message = "OK"
    mock_response.reason = None
    # Return tuple (response, device)
    mock_device = Device(
        id="DEV1",
        gateway_serial="GW1",
        installation_id="99",
        model_id="Test",
        device_type="heating",
        status="ok",
    )
    mock_cli_context.client.set_feature.return_value = (mock_response, mock_device)

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Execute the function being tested.
        assert await cmd_set(args) is True

        # Assert: Verify the results match expectations.
        # Verify calls
        assert mock_cli_context.client.get_features.called
        # Should call set_feature
        mock_cli_context.client.set_feature.assert_called()
        # Verify call args for set_feature: (device, feature, value)
        call_args_set = mock_cli_context.client.set_feature.call_args[0]
        assert call_args_set[0].get_feature("heating.curve.slope") is mock_feature
        assert call_args_set[2] == 1.4  # Value parsed from float

        # Verify output
        captured = capsys.readouterr()
        assert "Success!" in captured.out


@pytest.mark.asyncio
async def test_cmd_set_preserves_string_values(mock_cli_context):
    """CLI writes should preserve numeric-looking string values."""
    # Arrange: Create a writable string feature with a numeric-looking enum option.
    args = Namespace(
        feature_name="heating.program",
        value="01",
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
    )
    control = FeatureControl(
        command_name="setProgram",
        param_name="program",
        required_params=["program"],
        parent_feature_name="heating",
        uri="uri",
        value_type="string",
        options=["01", "auto"],
    )
    feature = Feature(
        name="heating.program",
        value="auto",
        unit=None,
        is_enabled=True,
        is_ready=True,
        control=control,
    )
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = (
        MagicMock(success=True, message=None, reason=None),
        MagicMock(),
    )

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Set the numeric-looking enum value through the CLI.
        assert await cmd_set(args) is True

    # Assert: The command should receive the exact string rather than a float.
    assert mock_cli_context.client.set_feature.call_args.args[2] == "01"


@pytest.mark.asyncio
async def test_cmd_set_parses_boolean_values(mock_cli_context):
    """CLI writes should convert boolean command parameters explicitly."""
    # Arrange: Create a writable boolean feature and uppercase CLI input.
    args = Namespace(
        feature_name="heating.enabled",
        value="TRUE",
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
    )
    control = FeatureControl(
        command_name="setEnabled",
        param_name="enabled",
        required_params=["enabled"],
        parent_feature_name="heating",
        uri="uri",
        value_type="boolean",
    )
    feature = Feature(
        name="heating.enabled",
        value=False,
        unit=None,
        is_enabled=True,
        is_ready=True,
        control=control,
    )
    mock_cli_context.client.get_features.return_value = [feature]
    mock_cli_context.client.set_feature.return_value = (
        MagicMock(success=True, message=None, reason=None),
        MagicMock(),
    )

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Set the boolean feature through the CLI.
        assert await cmd_set(args) is True

    # Assert: The command should receive a native boolean value.
    assert mock_cli_context.client.set_feature.call_args.args[2] is True


@pytest.mark.asyncio
async def test_cmd_set_rejects_invalid_numeric_values(mock_cli_context, capsys):
    """CLI writes should reject non-numeric input for numeric controls."""
    # Arrange: Create a numeric feature and provide a text value.
    args = Namespace(
        feature_name="heating.curve.slope",
        value="automatic",
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
    )
    control = FeatureControl(
        command_name="setCurve",
        param_name="slope",
        required_params=["slope"],
        parent_feature_name="heating.curve",
        uri="uri",
        value_type="number",
    )
    feature = Feature(
        name="heating.curve.slope",
        value=1.4,
        unit=None,
        is_enabled=True,
        is_ready=True,
        control=control,
    )
    mock_cli_context.client.get_features.return_value = [feature]

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Attempt to submit text to a numeric command parameter.
        assert await cmd_set(args) is False

    # Assert: The CLI should explain the validation failure without sending a write.
    assert "must be a number" in capsys.readouterr().out
    mock_cli_context.client.set_feature.assert_not_called()


@pytest.mark.asyncio
async def test_cmd_login_uses_environment_config_and_persists_it(monkeypatch, tmp_path):
    """Login should reuse environment credentials and save them for later commands."""
    # Arrange: Seed a token file and provide client settings through the environment.
    token_file = tmp_path / "tokens.json"
    token_file.write_text(
        '{"access_token": "existing-token", "future_field": "preserve-me"}',
        encoding="utf-8",
    )
    args = Namespace(
        client_id=None,
        insecure=False,
        redirect_uri=None,
        token_file=str(token_file),
    )
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
    )
    assert mock_auth.websession is mock_session
    assert saved_config == {
        "access_token": "existing-token",
        "client_id": "environment-client-id",
        "redirect_uri": "http://localhost:8123/auth",
        "future_field": "preserve-me",
    }


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
    args = Namespace(
        client_id="argument-client",
        redirect_uri=None,
        token_file=token_file,
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
    args = Namespace(client_id=None, redirect_uri=None, token_file=token_file)
    monkeypatch.delenv("VIESSMANN_CLIENT_ID", raising=False)
    monkeypatch.delenv("VIESSMANN_REDIRECT_URI", raising=False)

    # Act: Resolve settings for a command without arguments or environment values.
    client_id, redirect_uri = get_client_config(args)

    # Assert: The document supplies the client ID and the redirect falls back.
    assert (client_id, redirect_uri) == ("saved-client", "http://localhost:4200/")


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
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        feature_name="heating.curve.slope",
        command_name="setCurve",
        params=["slope=1.4", "shift=0"],
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,  # Context defaults
    )

    # Mock get_feature return
    mock_control = FeatureControl(
        command_name="setCurve",
        param_name="slope",
        required_params=["slope"],
        parent_feature_name="heating.curve",
        uri="uri",
    )
    mock_feature = Feature(
        name="heating.curve.slope",
        value=1.0,
        unit=None,
        is_enabled=True,
        is_ready=True,
        control=mock_control,
    )

    # Mock return value call validation
    mock_cli_context.client.get_features.return_value = [mock_feature]
    # Mock CommandResponse object
    mock_response = MagicMock()
    mock_response.success = True
    mock_response.message = "OK"
    mock_response.reason = None
    mock_cli_context.client.execute_command.return_value = mock_response

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Execute the function being tested.
        assert await cmd_exec(args) is True

        # Assert: Verify the results match expectations.
        # Verify calls
        assert mock_cli_context.client.get_features.called
        # Check first argument (Device)
        args_list = mock_cli_context.client.get_features.call_args[0]
        # args_list is (device,)
        assert args_list[0].id == "DEV1"
        assert mock_cli_context.client.get_features.call_args[1] == {}

        mock_cli_context.client.execute_command.assert_awaited_once_with(
            mock_feature, {"slope": 1.4, "shift": 0}
        )
        mock_cli_context.client.set_feature.assert_not_called()

        # Verify output
        captured = capsys.readouterr()
        assert "Success!" in captured.out


@pytest.mark.asyncio
async def test_cmd_set_hydrates_required_command_dependencies(mock_cli_context):
    """Test CLI writes provide sibling values required by a command."""
    # Arrange: Provide heating-curve features that share the setCurve command.
    args = Namespace(
        feature_name="heating.curve.slope",
        value="1.4",
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
    )
    control = FeatureControl(
        command_name="setCurve",
        param_name="slope",
        required_params=["shift", "slope"],
        parent_feature_name="heating.curve",
        uri="uri",
    )
    slope = Feature(
        name="heating.curve.slope",
        value=1.0,
        unit=None,
        is_enabled=True,
        is_ready=True,
        control=control,
    )
    shift = Feature(
        name="heating.curve.shift",
        value=4.0,
        unit=None,
        is_enabled=True,
        is_ready=True,
        control=control,
    )
    mock_cli_context.client.get_features.return_value = [slope, shift]
    mock_cli_context.client.set_feature.return_value = (
        MagicMock(success=True, message=None, reason=None),
        MagicMock(),
    )

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Set the slope through the CLI command.
        await cmd_set(args)

    # Assert: The write should receive a device containing the sibling shift value.
    command_device = mock_cli_context.client.set_feature.call_args.args[0]
    assert command_device.get_feature("heating.curve.slope") is slope
    assert command_device.get_feature("heating.curve.shift") is shift
    assert mock_cli_context.client.get_features.call_args.kwargs == {}


@pytest.mark.asyncio
async def test_cmd_exec_validation_error(mock_cli_context, capsys):
    """Test that ValidationErrors are printed nicely."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        feature_name="heating.curve.slope",
        command_name="setCurve",
        params=["slope=invalid"],
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
    )

    # Mock Feature
    mock_control = FeatureControl(
        command_name="setCurve",
        param_name="slope",
        required_params=["slope"],
        parent_feature_name="heating.curve",
        uri="uri",
    )
    mock_feature = Feature(
        name="heating.curve.slope",
        value=1.0,
        unit=None,
        is_enabled=True,
        is_ready=True,
        control=mock_control,
    )
    mock_cli_context.client.get_features.return_value = [mock_feature]

    error = ViValidationError("Simulated Validation Error")
    # If parsing fails to produce float, it might pass string to set_feature if logic allows,
    # OR if parse_cli_params works (it does strings).
    # "slope=invalid" -> params_dict={"slope": "invalid"} -> target_val="invalid"
    mock_cli_context.client.execute_command.side_effect = error

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Execute the function being tested.
        assert await cmd_exec(args) is False

        # Assert: Verify the results match expectations.
        captured = capsys.readouterr()
        # The logic might catch validation error or print it.
        # "Validation failed: ..."
        assert "Validation failed: Simulated Validation Error" in captured.out


@pytest.mark.asyncio
async def test_cmd_get_feature_not_found(mock_cli_context, capsys):
    """Test finding feature failure handling."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        feature_name="missing.feature",
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
        raw=False,
    )

    mock_cli_context.client.get_features.return_value = []

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Execute the function being tested.
        assert await cmd_get_feature(args) is False

        # Assert: Verify the results match expectations.
        captured = capsys.readouterr()
        assert "Feature 'missing.feature' not found." in captured.out


@pytest.mark.asyncio
async def test_cmd_list_features_json(mock_cli_context, capsys):
    """Test listing features with JSON output."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
        enabled=False,
        values=False,
        json=True,
    )

    mock_cli_context.client.get_features.return_value = [
        Feature(name="f1", value=1, unit=None, is_enabled=True, is_ready=True),
        Feature(name="f2", value=2, unit=None, is_enabled=True, is_ready=True),
    ]

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Execute the function being tested.
        assert await cmd_list_features(args) is True

        # Assert: Verify the results match expectations.
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output == ["f1", "f2"]


@pytest.mark.asyncio
async def test_async_main_json_output_with_mock_device_is_machine_readable(
    monkeypatch, capsys
):
    """The real CLI path must reserve stdout for a JSON feature list."""
    # Arrange: Use a bundled fixture without replacing client context setup.
    monkeypatch.setattr(
        "sys.argv",
        ["vi-client", "list-features", "--mock-device", "Vitocal250A", "--json"],
    )

    # Act: Invoke the parser, dispatcher, and context setup through the CLI entry path.
    exit_status = await async_main()

    # Assert: JSON output is parseable and setup diagnostics stay on stderr.
    captured = capsys.readouterr()
    assert exit_status == 0
    assert json.loads(captured.out)
    assert "Using Mock Device: Vitocal250A" in captured.err


@pytest.mark.asyncio
async def test_async_main_json_feature_values_with_mock_device_are_machine_readable(
    monkeypatch, capsys
):
    """The real CLI path must reserve stdout for JSON feature values."""
    # Arrange: Use a bundled fixture without replacing client context setup.
    monkeypatch.setattr(
        "sys.argv",
        [
            "vi-client",
            "list-features",
            "--mock-device",
            "Vitocal250A",
            "--values",
            "--json",
        ],
    )

    # Act: Invoke the parser, dispatcher, and context setup through the CLI entry path.
    exit_status = await async_main()

    # Assert: JSON output includes values and setup diagnostics stay on stderr.
    captured = capsys.readouterr()
    assert exit_status == 0
    features = json.loads(captured.out)
    assert features
    assert all("value" in feature for feature in features)
    assert "Using Mock Device: Vitocal250A" in captured.err


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


@pytest.mark.asyncio
async def test_cmd_list_features_enabled(mock_cli_context, capsys):
    """Test listing only enabled features (should use only_enabled=True)."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
        enabled=True,
        values=False,
        json=True,
    )

    mock_cli_context.client.get_features.return_value = [
        Feature(name="f_enabled", value=1, unit=None, is_enabled=True, is_ready=True)
    ]

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Execute the function being tested.
        assert await cmd_list_features(args) is True

        # Assert: Verify the results match expectations.
        # Verify call used only_enabled=True and passes Device
        assert mock_cli_context.client.get_features.called
        call_args = mock_cli_context.client.get_features.call_args
        # Arg 0 is Device object
        assert call_args[0][0].id == "DEV1"
        assert call_args[1]["only_enabled"] is True


@pytest.mark.asyncio
async def test_cmd_list_devices(mock_cli_context, capsys):
    """Test listing installations, gateways, and devices."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
    )

    # Mock Data (Objects)
    inst = Installation(id="123", description="Home", alias="MyHome", address={})
    gw = Gateway(serial="GW1", version="1.0", status="ok", installation_id="123")
    dev = Device(
        id="0",
        gateway_serial="GW1",
        installation_id="123",
        model_id="Test",
        device_type="heating",
        status="ok",
    )

    mock_cli_context.client.get_installations.return_value = [inst]
    mock_cli_context.client.get_gateways.return_value = [gw]
    mock_cli_context.client.get_devices.return_value = [dev]

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Execute the function being tested.
        assert await cmd_list_devices(args) is True

        # Assert: Verify the results match expectations.
        captured = capsys.readouterr()

        # Verify Output
        assert "Found 1 installations" in captured.out
        assert "ID: 123" in captured.out
        assert "Found 1 gateways" in captured.out
        assert "Serial: GW1" in captured.out
        assert "Found 1 devices" in captured.out
        assert "ID: 0" in captured.out


@pytest.mark.asyncio
async def test_cmd_list_writable(mock_cli_context, capsys):
    """Test listing available writable features for a device."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
    )

    # Create a feature with control
    control = FeatureControl(
        command_name="setCurve",
        param_name="slope",
        required_params=["slope"],
        parent_feature_name="heating.curve",
        uri="uri",
        min=0.2,
        max=3.5,
        step=0.1,
    )
    feature = Feature(
        name="heating.circuits.0.heating.curve.slope",
        value=1.0,
        unit=None,
        is_enabled=True,
        is_ready=True,
        control=control,
    )
    mock_cli_context.client.get_features.return_value = [feature]

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        # Act: Execute the function being tested.
        assert await cmd_list_writable(args) is True

        # Assert: Verify the results match expectations.
        captured = capsys.readouterr()
        # Should now list the flatter feature name
        assert "heating.circuits.0.heating.curve.slope" in captured.out
        assert "setCurve" in captured.out
        assert "slope" in captured.out
        assert "min: 0.2" in captured.out


@pytest.mark.asyncio
async def test_cmd_list_mock_devices(capsys):
    """Test listing mock devices."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
    )

    with patch("vi_api_client.cli.MockViClient.get_available_mock_devices") as mock_get:
        mock_get.return_value = ["MockDev1", "MockDev2"]

        # Act: Execute the function being tested.
        assert await cmd_list_mock_devices(args) is True

        # Assert: Verify the results match expectations.
        captured = capsys.readouterr()
        assert "Available Mock Devices:" in captured.out
        assert "- MockDev1" in captured.out
        assert "- MockDev2" in captured.out


@pytest.mark.asyncio
async def test_dispatch_returns_nonzero_for_failed_command():
    """The command dispatcher should map handler failures to exit status 1."""
    args = Namespace(command="set")

    with patch("vi_api_client.cli.cmd_set", new_callable=AsyncMock) as mock_cmd_set:
        mock_cmd_set.return_value = False

        assert await _dispatch_command(args) == 1


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
