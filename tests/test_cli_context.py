"""Tests for CLI client-context setup and auto-discovery."""

from argparse import Namespace
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vi_api_client.cli import CLIContext, create_session, setup_client_context
from vi_api_client.models import Device, Gateway


def _context_args(tmp_path: Path, **overrides: Any) -> Namespace:
    """Build a live-mode CLI argument namespace with optional overrides."""
    arguments: dict[str, Any] = {
        "fixture_device": None,
        "client_id": "test_id",
        "redirect_uri": "http://localhost",
        "token_file": tmp_path / "tokens.json",
        "insecure": False,
        "installation_id": None,
        "gateway_serial": None,
        "device_id": None,
        "json": False,
    }
    arguments.update(overrides)
    return Namespace(**arguments)


def _gateway(serial: str, installation_id: str) -> Gateway:
    """Build one gateway discovery snapshot."""
    return Gateway(
        serial=serial, version="1", status="ok", installation_id=installation_id
    )


def _device(device_id: str, installation_id: str, gateway_serial: str) -> Device:
    """Build one device discovery snapshot."""
    return Device(
        id=device_id,
        gateway_serial=gateway_serial,
        installation_id=installation_id,
        model_id="m1",
        device_type="heating",
        status="ok",
    )


@contextmanager
def _scripted_discovery_client(
    gateways: list[Gateway], devices: list[Device]
) -> Iterator[MagicMock]:
    """Patch the CLI boundary with scripted gateway and device responses."""
    with (
        patch("vi_api_client.cli.ViClient") as client_cls,
        patch("vi_api_client.cli.OAuth"),
    ):
        client = client_cls.return_value
        client.get_gateways = AsyncMock(return_value=gateways)
        client.get_devices = AsyncMock(return_value=devices)
        yield client


@pytest.mark.asyncio
async def test_cli_context_fixture_mode_does_not_create_oauth_or_session(tmp_path):
    """Fixture mode should stay offline without credentials or token access."""
    # Arrange: Point fixture mode at malformed tokens and fail if live setup is used.
    token_file = tmp_path / "tokens.json"
    token_file.write_text("{invalid", encoding="utf-8")
    args = _context_args(tmp_path, fixture_device="Vitodens200W", token_file=token_file)

    with (
        patch("vi_api_client.cli.OAuth") as mock_oauth,
        patch(
            "vi_api_client.cli.create_session", new_callable=AsyncMock
        ) as mock_create_session,
    ):
        mock_create_session.side_effect = AssertionError(
            "Fixture mode must not create an HTTP session"
        )

        # Act: Build a context for the bundled fixture device.
        async with setup_client_context(args) as ctx:
            # Assert: The fixture context uses deterministic offline defaults.
            assert isinstance(ctx, CLIContext)
            assert ctx.session is None
            assert ctx.inst_id == "99999"
            assert ctx.gw_serial == "MOCK_GATEWAY"
            assert ctx.dev_id == "0"

    # Assert: No live authentication or HTTP session should be initialized.
    mock_oauth.assert_not_called()
    mock_create_session.assert_not_called()


@pytest.mark.asyncio
async def test_cli_context_fixture_mode_routes_diagnostics_to_stderr_for_json(
    tmp_path, capsys
):
    """Fixture diagnostics must not contaminate requested JSON output."""
    # Arrange: Request JSON output for a fixture device and patch live boundaries.
    args = _context_args(tmp_path, fixture_device="Vitodens200W", json=True)

    with (
        patch("vi_api_client.cli.OAuth") as mock_oauth,
        patch(
            "vi_api_client.cli.create_session", new_callable=AsyncMock
        ) as mock_create_session,
    ):
        # Act: Build the fixture context for JSON-driven commands.
        async with setup_client_context(args):
            pass

    # Assert: The diagnostic goes to stderr and no live boundary is touched.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Using Fixture Device: Vitodens200W" in captured.err
    mock_oauth.assert_not_called()
    mock_create_session.assert_not_called()


@pytest.mark.asyncio
async def test_cli_context_explicit_ids_skip_discovery(tmp_path):
    """Explicit IDs should build the context without discovery requests."""
    # Arrange: Supply every identifier and mock the live session boundary.
    args = _context_args(
        tmp_path,
        installation_id="123",
        gateway_serial="serial",
        device_id="dev1",
    )

    with (
        patch("vi_api_client.cli.OAuth"),
        patch(
            "vi_api_client.cli.create_session", new_callable=AsyncMock
        ) as mock_create_session,
    ):
        mock_session = MagicMock()
        mock_create_session.return_value.__aenter__.return_value = mock_session

        # Act: Build a context from explicit identifiers.
        async with setup_client_context(args) as ctx:
            # Assert: The context exposes exactly the supplied identifiers.
            assert ctx.inst_id == "123"
            assert ctx.gw_serial == "serial"
            assert ctx.dev_id == "dev1"


@pytest.mark.asyncio
async def test_cli_context_autodiscovery_routes_context_to_stderr_for_json(
    tmp_path, capsys
):
    """Auto-discovery should work offline and route diagnostics for JSON output."""
    # Arrange: Script one gateway and one device for full auto-discovery.
    args = _context_args(tmp_path, json=True)

    with _scripted_discovery_client(
        [_gateway("GW123", "100")],
        [_device("0", "100", "GW123")],
    ) as client:
        # Act: Discover installation, gateway, and device from scratch.
        async with setup_client_context(args) as ctx:
            # Assert: The discovered context matches the scripted discovery data.
            assert (ctx.inst_id, ctx.gw_serial, ctx.dev_id) == ("100", "GW123", "0")

        client.get_gateways.assert_called_once()
        client.get_devices.assert_called_once_with("100", "GW123")

    # Assert: JSON output callers receive the setup context as a diagnostic.
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Auto-selected Context: Inst=100, GW=GW123, Dev=0" in captured.err


@pytest.mark.parametrize(
    "partial_scope",
    [
        {"installation_id": None, "gateway_serial": "GW-B"},
        {"installation_id": "B", "gateway_serial": None},
    ],
    ids=["gateway-serial-scope", "installation-id-scope"],
)
@pytest.mark.asyncio
async def test_cli_context_discovery_completes_partial_scope(
    tmp_path, partial_scope: dict[str, Any]
):
    """A supplied gateway or installation scope should discover its missing IDs."""
    # Arrange: Script two gateways and one device on the second gateway.
    args = _context_args(tmp_path, **partial_scope)

    with _scripted_discovery_client(
        [_gateway("GW-A", "A"), _gateway("GW-B", "B")],
        [_device("0", "B", "GW-B")],
    ) as client:
        # Act: Complete the partial scope through discovery.
        async with setup_client_context(args) as ctx:
            # Assert: The context resolves to the scoped gateway and its device.
            assert (ctx.inst_id, ctx.gw_serial, ctx.dev_id) == ("B", "GW-B", "0")

        client.get_devices.assert_called_once_with("B", "GW-B")


@pytest.mark.asyncio
async def test_cli_context_discovery_skips_device_lookup_when_device_id_given(tmp_path):
    """A supplied device ID should complete the scope without device discovery."""
    # Arrange: Provide the gateway scope and the device ID up front.
    args = _context_args(tmp_path, gateway_serial="GW-B", device_id="7")

    with _scripted_discovery_client(
        [_gateway("GW-A", "A"), _gateway("GW-B", "B")],
        [],
    ) as client:
        # Act: Complete the gateway scope through discovery.
        async with setup_client_context(args) as ctx:
            # Assert: The supplied device ID completes the context.
            assert (ctx.inst_id, ctx.gw_serial, ctx.dev_id) == ("B", "GW-B", "7")

        # Assert: No device discovery request is needed.
        client.get_devices.assert_not_called()


@pytest.mark.asyncio
async def test_cli_context_normalizes_integer_installation_scope(tmp_path):
    """An integer installation argument should match its API string form."""
    # Arrange: The parser reports --installation-id as an integer.
    args = _context_args(tmp_path, installation_id=123)

    with _scripted_discovery_client(
        [_gateway("GW-A", "120"), _gateway("GW-B", "123")],
        [_device("0", "123", "GW-B")],
    ) as client:
        # Act: Discover with the integer installation argument.
        async with setup_client_context(args) as ctx:
            # Assert: The normalized installation ID matches its API form.
            assert (ctx.inst_id, ctx.gw_serial, ctx.dev_id) == ("123", "GW-B", "0")

        client.get_devices.assert_called_once_with("123", "GW-B")


@pytest.mark.asyncio
async def test_cli_context_treats_empty_installation_scope_as_absent(tmp_path):
    """An empty installation argument should stay absent during discovery."""
    # Arrange: Provide an empty installation ID that must not scope discovery.
    args = _context_args(tmp_path, installation_id="")

    with _scripted_discovery_client(
        [_gateway("GW-A", "A")],
        [_device("0", "A", "GW-A")],
    ):
        # Act: Discover without a usable installation scope.
        async with setup_client_context(args) as ctx:
            # Assert: Auto-discovery selects the first gateway and its device.
            assert (ctx.inst_id, ctx.gw_serial, ctx.dev_id) == ("A", "GW-A", "0")


@pytest.mark.asyncio
async def test_cli_context_rejects_mismatched_partial_scope(tmp_path):
    """Partially specified IDs must not be combined across installations."""
    # Arrange: Supply a gateway serial that belongs to another installation.
    args = _context_args(tmp_path, installation_id="A", gateway_serial="GW-B")

    with _scripted_discovery_client(
        [_gateway("GW-A", "A"), _gateway("GW-B", "B")],
        [],
    ) as client:
        # Act and assert: The mismatched pair rejects before device discovery.
        with pytest.raises(
            ValueError,
            match="Gateway 'GW-B' does not belong to installation 'A'",
        ):
            async with setup_client_context(args):
                pass

        client.get_devices.assert_not_called()


@pytest.mark.parametrize(
    ("gateways", "devices", "message"),
    [
        ([], [], "No gateways found."),
        ([_gateway("GW-A", "A")], [], "No devices found."),
    ],
    ids=["no-gateways", "no-devices"],
)
@pytest.mark.asyncio
async def test_cli_context_reports_empty_discovery_results(
    tmp_path, gateways: list[Gateway], devices: list[Device], message: str
):
    """Empty discovery collections should surface actionable errors."""
    # Arrange: Script an empty gateway or device discovery result.
    args = _context_args(tmp_path)

    # Act and assert: The empty collection rejects with its specific message.
    with (
        _scripted_discovery_client(gateways, devices),
        pytest.raises(ValueError, match=message),
    ):
        async with setup_client_context(args):
            pass


@pytest.mark.asyncio
async def test_cli_context_reports_unknown_gateway_scope(tmp_path):
    """An unknown gateway serial should name the missing gateway."""
    # Arrange: Request a gateway that discovery does not return.
    args = _context_args(tmp_path, gateway_serial="GW-X")

    # Act and assert: The unknown serial rejects with a specific message.
    with (
        _scripted_discovery_client([_gateway("GW-A", "A")], []),
        pytest.raises(ValueError, match=r"Gateway 'GW-X' not found\."),
    ):
        async with setup_client_context(args):
            pass


@pytest.mark.asyncio
async def test_cli_context_reports_gatewayless_installation_scope(tmp_path):
    """An installation without gateways should name the installation."""
    # Arrange: Scope discovery to an installation that owns no gateway.
    args = _context_args(tmp_path, installation_id="B")

    # Act and assert: The gatewayless installation rejects by name.
    with (
        _scripted_discovery_client([_gateway("GW-A", "A")], []),
        pytest.raises(ValueError, match=r"No gateway found for installation 'B'\."),
    ):
        async with setup_client_context(args):
            pass


@pytest.mark.asyncio
async def test_cli_context_falls_back_to_first_device_without_id_zero(tmp_path):
    """Discovery should prefer device '0' but accept the first alternative."""
    # Arrange: Script a gateway whose only device has a non-zero ID.
    args = _context_args(tmp_path)

    with _scripted_discovery_client(
        [_gateway("GW-A", "A")],
        [_device("7", "A", "GW-A")],
    ):
        # Act: Discover the device for the gateway.
        async with setup_client_context(args) as ctx:
            # Assert: The first device is selected when '0' is absent.
            assert (ctx.inst_id, ctx.gw_serial, ctx.dev_id) == ("A", "GW-A", "7")


@pytest.mark.asyncio
async def test_cli_context_without_discovery_leaves_ids_absent(tmp_path):
    """Disabled discovery should yield a usable context with optional IDs absent."""
    # Arrange: Omit every identifier while disabling auto-discovery.
    args = _context_args(tmp_path)

    with _scripted_discovery_client([_gateway("GW-A", "A")], []) as client:
        # Act: Build a context without discovery.
        async with setup_client_context(args, discover=False) as ctx:
            # Assert: The context carries no identifiers.
            assert ctx.inst_id is None
            assert ctx.gw_serial is None
            assert ctx.dev_id is None

        # Assert: No discovery request was made for the context.
        client.get_gateways.assert_not_called()
        client.get_devices.assert_not_called()


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
