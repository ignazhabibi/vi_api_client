from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vi_api_client.cli import CLIContext, create_session, setup_client_context
from vi_api_client.models import Device, Gateway


@pytest.mark.asyncio
async def test_cli_context_mock_mode_does_not_create_oauth_or_session(tmp_path):
    """Mock mode should stay offline without credentials or token access."""
    # Arrange: Point mock mode at malformed tokens and fail if live setup is used.
    token_file = tmp_path / "tokens.json"
    token_file.write_text("{invalid", encoding="utf-8")
    args = Namespace(
        mock_device="Vitodens200W",
        client_id=None,
        redirect_uri=None,
        token_file=token_file,
        insecure=False,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
    )

    with (
        patch("vi_api_client.cli.OAuth") as mock_oauth,
        patch(
            "vi_api_client.cli.create_session", new_callable=AsyncMock
        ) as mock_create_session,
    ):
        mock_create_session.side_effect = AssertionError(
            "Mock mode must not create an HTTP session"
        )

        # Act: Build a context for the bundled mock device.
        async with setup_client_context(args) as ctx:
            # Assert: The mock context should use deterministic offline defaults.
            assert isinstance(ctx, CLIContext)
            assert ctx.session is None
            assert ctx.inst_id == "99999"
            assert ctx.gw_serial == "MOCK_GATEWAY"
            assert ctx.dev_id == "0"

    # Assert: No live authentication or HTTP session should be initialized.
    mock_oauth.assert_not_called()
    mock_create_session.assert_not_called()


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
async def test_cli_context_autodiscovery_routes_context_to_stderr_for_json(
    tmp_path, capsys
):
    """Test CLI context auto-discovery by mocking the Client completely."""
    # Arrange: Create mock client, device, and fixture data for test.
    args = Namespace(
        mock_device=None,
        client_id="test_id",
        redirect_uri="http://localhost",
        token_file=tmp_path / "tokens.json",
        insecure=False,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
        json=True,
    )

    # We patch Client so we don't need real Auth or Network
    with (
        patch("vi_api_client.cli.ViClient") as MockClientCls,
        patch("vi_api_client.cli.OAuth"),
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

    # Assert: JSON output callers receive setup context as a diagnostic.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Auto-selected Context: Inst=100, GW=GW123, Dev=0" in captured.err


@pytest.mark.asyncio
async def test_cli_context_routes_insecure_warning_to_stderr_for_json(capsys):
    """Insecure TLS warnings must not contaminate requested JSON output."""
    # Arrange: Request JSON output while disabling TLS verification.
    args = Namespace(insecure=True, json=True)

    # Act: Create and close the session without making a network request.
    session = await create_session(args)
    await session.close()

    # Assert: The warning remains a visible stderr diagnostic.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "WARNING: SSL verification disabled via --insecure" in captured.err


@pytest.mark.asyncio
async def test_cli_context_discovery_uses_provided_gateway_scope(tmp_path):
    """A supplied gateway serial should determine its missing installation ID."""
    args = Namespace(
        mock_device=None,
        client_id="test_id",
        redirect_uri="http://localhost",
        token_file=tmp_path / "tokens.json",
        insecure=False,
        installation_id=None,
        gateway_serial="GW-B",
        device_id=None,
    )

    with (
        patch("vi_api_client.cli.ViClient") as mock_client_cls,
        patch("vi_api_client.cli.OAuth"),
    ):
        mock_client = mock_client_cls.return_value
        mock_client.get_gateways = AsyncMock(
            return_value=[
                Gateway(serial="GW-A", version="1", status="ok", installation_id="A"),
                Gateway(serial="GW-B", version="1", status="ok", installation_id="B"),
            ]
        )
        mock_client.get_devices = AsyncMock(
            return_value=[
                Device(
                    id="0",
                    gateway_serial="GW-B",
                    installation_id="B",
                    model_id="m1",
                    device_type="heating",
                    status="ok",
                )
            ]
        )

        async with setup_client_context(args) as ctx:
            assert (ctx.inst_id, ctx.gw_serial, ctx.dev_id) == ("B", "GW-B", "0")

        mock_client.get_devices.assert_called_once_with("B", "GW-B")


@pytest.mark.asyncio
async def test_cli_context_discovery_uses_provided_installation_scope(tmp_path):
    """A supplied installation ID should select one of its gateways."""
    args = Namespace(
        mock_device=None,
        client_id="test_id",
        redirect_uri="http://localhost",
        token_file=tmp_path / "tokens.json",
        insecure=False,
        installation_id="B",
        gateway_serial=None,
        device_id=None,
    )

    with (
        patch("vi_api_client.cli.ViClient") as mock_client_cls,
        patch("vi_api_client.cli.OAuth"),
    ):
        mock_client = mock_client_cls.return_value
        mock_client.get_gateways = AsyncMock(
            return_value=[
                Gateway(serial="GW-A", version="1", status="ok", installation_id="A"),
                Gateway(serial="GW-B", version="1", status="ok", installation_id="B"),
            ]
        )
        mock_client.get_devices = AsyncMock(
            return_value=[
                Device(
                    id="0",
                    gateway_serial="GW-B",
                    installation_id="B",
                    model_id="m1",
                    device_type="heating",
                    status="ok",
                )
            ]
        )

        async with setup_client_context(args) as ctx:
            assert (ctx.inst_id, ctx.gw_serial, ctx.dev_id) == ("B", "GW-B", "0")

        mock_client.get_devices.assert_called_once_with("B", "GW-B")


@pytest.mark.asyncio
async def test_cli_context_rejects_mismatched_partial_scope(tmp_path):
    """Partially specified IDs must not be combined across installations."""
    args = Namespace(
        mock_device=None,
        client_id="test_id",
        redirect_uri="http://localhost",
        token_file=tmp_path / "tokens.json",
        insecure=False,
        installation_id="A",
        gateway_serial="GW-B",
        device_id=None,
    )

    with (
        patch("vi_api_client.cli.ViClient") as mock_client_cls,
        patch("vi_api_client.cli.OAuth"),
    ):
        mock_client = mock_client_cls.return_value
        mock_client.get_gateways = AsyncMock(
            return_value=[
                Gateway(serial="GW-A", version="1", status="ok", installation_id="A"),
                Gateway(serial="GW-B", version="1", status="ok", installation_id="B"),
            ]
        )

        with pytest.raises(
            ValueError,
            match="Gateway 'GW-B' does not belong to installation 'A'",
        ):
            async with setup_client_context(args):
                pass

        mock_client.get_devices.assert_not_called()
