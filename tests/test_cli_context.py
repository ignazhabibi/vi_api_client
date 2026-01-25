from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vi_api_client.cli import CLIContext, setup_client_context
from vi_api_client.models import Device, Gateway


@pytest.mark.asyncio
async def test_cli_context_mock_mode():
    """Test CLI context in mock mode (no API calls)."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        mock_device="Vitodens200W",
        client_id=None,
        redirect_uri=None,
        token_file="tokens.json",
        insecure=False,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
    )

    # Act: Execute the function being tested.
    async with setup_client_context(args) as ctx:
        # Assert: Verify the results match expectations.
        assert isinstance(ctx, CLIContext)
        assert ctx.inst_id == "99999"
        assert ctx.gw_serial == "MOCK_GATEWAY"
        assert ctx.dev_id == "0"


@pytest.mark.asyncio
async def test_cli_context_explicit_ids():
    """Test CLI context with explicit IDs (no auto-discovery)."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        mock_device=None,
        client_id="test_id",
        redirect_uri="http://localhost",
        token_file="tokens.json",
        insecure=False,
        installation_id="123",
        gateway_serial="serial",
        device_id="dev1",
    )

    # We mock OAuth and creating session to avoid FS/Net
    with (
        patch("vi_api_client.cli.OAuth"),
        patch(
            "vi_api_client.cli.create_session", new_callable=AsyncMock
        ) as mock_create_session,
    ):
        mock_session = MagicMock()
        mock_create_session.return_value.__aenter__.return_value = mock_session

        # Act: Execute the function being tested.
        async with setup_client_context(args) as ctx:
            # Assert: Verify the results match expectations.
            assert ctx.inst_id == "123"
            assert ctx.gw_serial == "serial"
            assert ctx.dev_id == "dev1"
            # Should NOT define autodiscovery


@pytest.mark.asyncio
async def test_cli_context_autodiscovery():
    """Test CLI context auto-discovery by mocking the Client completely."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        mock_device=None,
        client_id="test_id",
        redirect_uri="http://localhost",
        token_file="tokens.json",
        insecure=False,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
    )

    # We patch Client so we don't need real Auth or Network
    with (
        patch("vi_api_client.cli.ViClient") as MockClientCls,
        patch("vi_api_client.cli.OAuth"),
        patch("vi_api_client.cli.load_config", return_value={}),
    ):
        # Setup the mock client instance
        mock_client = MockClientCls.return_value

        # Configure async methods
        mock_client.get_gateways = AsyncMock(
            return_value=[
                Gateway(serial="GW123", version="1", status="ok", installation_id="100")
            ]
        )
        mock_client.get_devices = AsyncMock(
            return_value=[
                Device(
                    id="0",
                    gateway_serial="GW123",
                    installation_id="100",
                    model_id="m1",
                    device_type="heating",
                    status="ok",
                )
            ]
        )

        # Act: Execute the function being tested.
        async with setup_client_context(args) as ctx:
            # Assert: Verify the results match expectations.
            # Verify context values derived from mock client responses
            assert ctx.inst_id == "100"
            assert ctx.gw_serial == "GW123"
            assert ctx.dev_id == "0"

            # Verify client method calls
            mock_client.get_gateways.assert_called_once()
            mock_client.get_devices.assert_called_once_with("100", "GW123")
