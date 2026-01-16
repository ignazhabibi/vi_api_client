
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from argparse import Namespace
import json

from vi_api_client.cli import cmd_exec, cmd_get_feature, cmd_list_features
from vi_api_client.models import Feature, Device
from vi_api_client.exceptions import ViValidationError, ViNotFoundError

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
async def test_cmd_exec_success(mock_cli_context, capsys):
    """Test successful command execution via CLI."""
    args = Namespace(
        feature_name="heating.curve",
        command_name="setCurve",
        params=["slope=1.4"],
        token_file="tokens.json",
        client_id=None,
        redirect_uri=None,
        insecure=False,
        mock_device=None,
        installation_id=None, gateway_serial=None, device_id=None # Context defaults
    )

    # Mock get_feature return
    mock_cli_context.client.get_feature.return_value = {
        "feature": "heating.curve",
        "commands": {
            "setCurve": {
                "uri": "uri",
                "params": {"slope": {"type": "number"}}
            }
        }
    }
    mock_cli_context.client.execute_command.return_value = {"success": True}

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context
        
        await cmd_exec(args)
        
        # Verify calls
        mock_cli_context.client.get_feature.assert_called_with(99, "GW1", "DEV1", "heating.curve")
        mock_cli_context.client.execute_command.assert_called()
        
        # Verify output
        captured = capsys.readouterr()
        assert "Success!" in captured.out
        assert "true" in captured.out.lower()

@pytest.mark.asyncio
async def test_cmd_exec_validation_error(mock_cli_context, capsys):
    """Test that ValidationErrors are printed nicely."""
    args = Namespace(
        feature_name="heating.curve",
        command_name="setCurve",
        params=["slope=invalid"],
        token_file="tokens.json", 
        client_id=None, redirect_uri=None, insecure=False, mock_device=None,
        installation_id=None, gateway_serial=None, device_id=None
    )

    # The exception is raised during parsing or execution.
    # If parsing fails inside cmd_exec (it does 'from .parsers import parse_cli_params'), that raises ValueError.
    # cmd_exec catches ValueError at the start.
    
    # Let's test ViValidationError raised by client.execute_command
    mock_cli_context.client.get_feature.return_value = {
        "feature": "heating.curve",
        "commands": {"setCurve": {"uri": "uri", "params": {}}}
    }
    
    error = ViValidationError("Simulated Validation Error")
    mock_cli_context.client.execute_command.side_effect = error

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context
        
        await cmd_exec(args)
        
        captured = capsys.readouterr()
        assert "Validation failed: Simulated Validation Error" in captured.out

@pytest.mark.asyncio
async def test_cmd_get_feature_not_found(mock_cli_context, capsys):
    """Test finding feature failure handling."""
    args = Namespace(
        feature_name="missing.feature",
        token_file="tokens.json",
        client_id=None, redirect_uri=None, insecure=False, mock_device=None,
        installation_id=None, gateway_serial=None, device_id=None,
        raw=False
    )

    mock_cli_context.client.get_feature.side_effect = ViNotFoundError("Feature missing")

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
        client_id=None, redirect_uri=None, insecure=False, mock_device=None,
        installation_id=None, gateway_serial=None, device_id=None,
        enabled=False, values=False, json=True
    )

    mock_cli_context.client.get_features.return_value = [
        Feature(name="f1", is_enabled=True, is_ready=True, properties={}),
        Feature(name="f2", is_enabled=True, is_ready=True, properties={})
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
        client_id=None, redirect_uri=None, insecure=False, mock_device=None,
        installation_id=None, gateway_serial=None, device_id=None,
        enabled=True, values=False, json=True
    )

    mock_cli_context.client.get_features.return_value = [
        Feature(name="f_enabled", is_enabled=True, is_ready=True, properties={})
    ]

    with patch("vi_api_client.cli.setup_client_context") as mock_setup:
        mock_setup.return_value.__aenter__.return_value = mock_cli_context
        
        await cmd_list_features(args)
        
        # Verify call used only_enabled=True
        mock_cli_context.client.get_features.assert_called_with(
            99, "GW1", "DEV1", only_enabled=True
        )
