import json
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vi_api_client.cli import (
    cmd_exec,
    cmd_get_consumption,
    cmd_get_feature,
    cmd_list_devices,
    cmd_list_features,
    cmd_list_mock_devices,
    cmd_list_writable,
    cmd_set,
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
    mock_cli_context.client.set_feature.return_value = mock_response

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        await cmd_set(args)

        # Verify calls
        assert mock_cli_context.client.get_features.called
        # Should call set_feature
        mock_cli_context.client.set_feature.assert_called()
        # Verify call args for set_feature: (device, feature, value)
        call_args_set = mock_cli_context.client.set_feature.call_args[0]
        assert call_args_set[2] == 1.4  # Value parsed from float

        # Verify output
        captured = capsys.readouterr()
        assert "Success!" in captured.out


@pytest.mark.asyncio
async def test_cmd_exec_success(mock_cli_context, capsys):
    """Test successful command execution via CLI (Legacy/Advanced)."""
    args = Namespace(
        feature_name="heating.curve.slope",
        command_name="setCurve",
        params=["slope=1.4"],
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
    mock_cli_context.client.set_feature.return_value = mock_response

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        await cmd_exec(args)

        # Verify calls
        assert mock_cli_context.client.get_features.called
        # Check first argument (Device)
        args_list = mock_cli_context.client.get_features.call_args[0]
        kwargs_list = mock_cli_context.client.get_features.call_args[1]
        # args_list is (device,)
        assert args_list[0].id == "DEV1"
        assert kwargs_list["feature_names"] == ["heating.curve.slope"]

        # Should call set_feature
        mock_cli_context.client.set_feature.assert_called()
        # Verify call args for set_feature: (device, feature, value)
        call_args_set = mock_cli_context.client.set_feature.call_args[0]
        assert call_args_set[2] == 1.4  # Value parsed from float

        # Verify output
        captured = capsys.readouterr()
        assert "Success!" in captured.out


@pytest.mark.asyncio
async def test_cmd_exec_validation_error(mock_cli_context, capsys):
    """Test that ValidationErrors are printed nicely."""
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
    mock_cli_context.client.set_feature.side_effect = error

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        await cmd_exec(args)

        captured = capsys.readouterr()
        # The logic might catch validation error or print it.
        # "Validation failed: ..."
        assert "Validation failed: Simulated Validation Error" in captured.out


@pytest.mark.asyncio
async def test_cmd_get_feature_not_found(mock_cli_context, capsys):
    """Test finding feature failure handling."""
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

        await cmd_get_feature(args)

        captured = capsys.readouterr()
        assert "Feature 'missing.feature' not found." in captured.out


@pytest.mark.asyncio
async def test_cmd_list_features_json(mock_cli_context, capsys):
    """Test listing features with JSON output."""
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

        await cmd_list_features(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output == ["f1", "f2"]


@pytest.mark.asyncio
async def test_cmd_list_features_enabled(mock_cli_context, capsys):
    """Test listing only enabled features (should use only_enabled=True)."""
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

        await cmd_list_features(args)

        # Verify call used only_enabled=True and passes Device
        assert mock_cli_context.client.get_features.called
        call_args = mock_cli_context.client.get_features.call_args
        # Arg 0 is Device object
        assert call_args[0][0].id == "DEV1"
        assert call_args[1]["only_enabled"] is True


@pytest.mark.asyncio
async def test_cmd_list_devices(mock_cli_context, capsys):
    """Test listing installations, gateways, and devices."""

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
    inst = Installation(id=123, description="Home", alias="MyHome", address={})
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

        await cmd_list_devices(args)

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

        await cmd_list_writable(args)

        captured = capsys.readouterr()
        # Should now list the flatter feature name
        assert "heating.circuits.0.heating.curve.slope" in captured.out
        assert "setCurve" in captured.out
        assert "slope" in captured.out
        assert "min: 0.2" in captured.out


@pytest.mark.asyncio
async def test_cmd_get_consumption(mock_cli_context, capsys):
    """Test getting consumption data."""

    args = Namespace(
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
        metric="summary",
    )

    # Mock consumption features
    consumption_features = [
        Feature(
            name="analytics.heating.power.consumption.total",
            value=15.5,
            unit="kilowattHour",
            is_enabled=True,
            is_ready=True,
        ),
        Feature(
            name="analytics.heating.power.consumption.heating",
            value=10.0,
            unit="kilowattHour",
            is_enabled=True,
            is_ready=True,
        ),
    ]
    mock_cli_context.client.get_consumption.return_value = consumption_features

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context

        await cmd_get_consumption(args)

        captured = capsys.readouterr()
        assert "analytics.heating.power.consumption.total" in captured.out
        assert "15.5" in captured.out


@pytest.mark.asyncio
async def test_cmd_list_mock_devices(capsys):
    """Test listing mock devices."""

    args = Namespace(
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
    )

    with patch("vi_api_client.cli.MockViClient.get_available_mock_devices") as mock_get:
        mock_get.return_value = ["MockDev1", "MockDev2"]

        await cmd_list_mock_devices(args)

        captured = capsys.readouterr()
        assert "Available Mock Devices:" in captured.out
        assert "- MockDev1" in captured.out
        assert "- MockDev2" in captured.out
