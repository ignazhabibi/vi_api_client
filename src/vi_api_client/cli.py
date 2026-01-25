"""CLI for Viessmann Client."""

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp

from vi_api_client import (
    MockViClient,
    OAuth,
    ViClient,
    ViNotFoundError,
    ViValidationError,
)

from .models import CommandResponse, Device
from .utils import format_feature, parse_cli_params

# Default file to store tokens and config
TOKEN_FILE = "tokens.json"

logging.basicConfig(level=logging.INFO, format="%(message)s")
_LOGGER = logging.getLogger(__name__)


@dataclass
class CLIContext:
    """Context for CLI commands."""

    session: aiohttp.ClientSession
    client: ViClient | MockViClient
    # Found IDs (either from args or auto-discovery)
    inst_id: str
    gw_serial: str
    dev_id: str


def load_config(token_file: str) -> dict[str, Any]:
    """Load configuration from token file.

    Args:
        token_file: Path to the JSON token file.

    Returns:
        Dictionary containing tokens and config, or empty dict if missing.
    """
    try:
        with Path(token_file).open() as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


async def create_session(args) -> aiohttp.ClientSession:
    """Create aiohttp session with optional insecure SSL.

    Args:
        args: Parsed command line arguments.

    Returns:
        An aiohttp ClientSession configured according to args.
    """
    if args.insecure:
        print("WARNING: SSL verification disabled via --insecure")
        connector = aiohttp.TCPConnector(ssl=False)
        return aiohttp.ClientSession(connector=connector)
    return aiohttp.ClientSession()


async def cmd_login(args) -> None:
    """Handle login command.

    Guides the user through OAuth authorization flow.

    Args:
        args: Parsed command line arguments including client_id and redirect_uri.
    """
    client_id = args.client_id
    redirect_uri = args.redirect_uri

    auth = OAuth(client_id, redirect_uri, args.token_file)
    url = auth.get_authorization_url()

    print(f"Please visit the following URL to log in:\n\n{url}\n")
    print(f"After verifying, you will be redirected to {redirect_uri}?code=...")
    code = input("Paste the 'code' parameter from the URL here: ").strip()

    async with await create_session(args) as session:
        auth.websession = session
        await auth.async_fetch_details_from_code(code)

    print(f"Successfully authenticated! Tokens and config saved to {args.token_file}")


def get_client_config(args) -> tuple[str, str]:
    """Get client_id and redirect_uri from args or file."""
    config = load_config(args.token_file)

    client_id = (
        args.client_id or os.getenv("VIESSMANN_CLIENT_ID") or config.get("client_id")
    )
    redirect_uri = (
        args.redirect_uri
        or os.getenv("VIESSMANN_REDIRECT_URI")
        or config.get("redirect_uri")
    )

    if not client_id:
        print(
            "Error: Client ID not found. Provide via --client-id or "
            "VIESSMANN_CLIENT_ID env var."
        )
        print("Please run 'login' first or provide --client-id.")
        sys.exit(1)

    return client_id, redirect_uri


def get_client_config_safe(args: argparse.Namespace) -> tuple[str, str]:
    """Get config but return empty strings if missing (for mock mode).

    Args:
        args: Parsed command line arguments.

    Returns:
        Tuple of (client_id, redirect_uri).
    """
    # For mock mode, we don't strictly need client_id, so we can be lenient.
    if args.mock_device:
        return "mock_id", "mock_uri"
    return get_client_config(args)


@asynccontextmanager
async def setup_client_context(
    args, discover: bool = True
) -> AsyncGenerator[CLIContext, None]:
    """Creates Session, Auth, Client AND performs Auto-Discovery if needed."""
    client_id, redirect_uri = get_client_config_safe(args)

    async with await create_session(args) as session:
        auth = OAuth(client_id, redirect_uri, args.token_file, session)

        # Initialize Client
        if args.mock_device:
            client = MockViClient(args.mock_device, auth)
            # Default mock IDs
            inst_id = getattr(args, "installation_id", None) or "99999"
            gw_serial = getattr(args, "gateway_serial", None) or "MOCK_GATEWAY"
            dev_id = getattr(args, "device_id", None) or "0"
            print(f"Using Mock Device: {args.mock_device}")
        else:
            client = ViClient(auth)
            inst_id = getattr(args, "installation_id", None)
            gw_serial = getattr(args, "gateway_serial", None)
            dev_id = getattr(args, "device_id", None)

            # Perform Auto-Discovery if IDs are missing
            if discover and not (inst_id and gw_serial and dev_id):
                gateways = await client.get_gateways()
                if not gateways:
                    print("No gateways found.")
                    raise ValueError("No gateways found.")

                if not gw_serial:
                    gw_serial = gateways[0].serial
                if not inst_id:
                    inst_id = gateways[0].installation_id

                if not dev_id:
                    devices = await client.get_devices(inst_id, gw_serial)
                    if not devices:
                        raise ValueError("No devices found.")

                    # Prefer Device "0" (Heating System)
                    # Prefer Device "0" (Heating System)
                    target_dev = next(
                        (device for device in devices if device.id == "0"),
                        devices[0],
                    )
                    dev_id = target_dev.id
                    print(
                        f"Auto-selected Context: Inst={inst_id}, GW={gw_serial}, "
                        f"Dev={dev_id}"
                    )

        yield CLIContext(session, client, inst_id, gw_serial, dev_id)


async def cmd_list_devices(args) -> None:
    """List installations and devices.

    Fetches and prints all installations, gateways, and devices.

    Args:
        args: Parsed command line arguments.
    """
    # Does not use full context discovery, just client
    async with setup_client_context(args, discover=False) as ctx:
        try:
            installations = await ctx.client.get_installations()
            print(f"Found {len(installations)} installations:")
            for installation in installations:
                print(
                    f"- ID: {installation.id}, "
                    f"Description: {installation.description}, "
                    f"Alias: {installation.alias}"
                )

            gateways = await ctx.client.get_gateways()
            print(f"\nFound {len(gateways)} gateways:")
            for gateway in gateways:
                print(
                    f"- Serial: {gateway.serial} "
                    f"(Inst: {gateway.installation_id}), "
                    f"Version: {gateway.version}, Status: {gateway.status}"
                )

                devices = await ctx.client.get_devices(
                    gateway.installation_id, gateway.serial
                )
                print(f"Found {len(devices)} devices:")
                for device in devices:
                    print(
                        f"- ID: {device.id}, Model: {device.model_id}, "
                        f"Type: {device.device_type}, "
                        f"Status: {device.status}"
                    )

        except Exception as e:
            _LOGGER.error("Error listing devices: %s", e)


async def cmd_list_features(args) -> None:
    """List all features for a device.

    Supports filtering and formatting options.

    Args:
        args: Parsed command line arguments including enabled, values, json flags.
    """
    try:
        async with setup_client_context(args) as ctx:
            # Transient Device for API call
            device = _transient_device(ctx)

            # NOTE: get_features now returns FLATTENED features directly.
            features = await ctx.client.get_features(device, only_enabled=args.enabled)

            if args.values:
                if args.json:
                    # Output clean JSON list of objects
                    out_data = [
                        {
                            "name": item.name,
                            "value": item.value,
                            "unit": item.unit,
                            "formatted": format_feature(item),
                            "writable": item.is_writable,
                        }
                        for item in features
                    ]
                    print(json.dumps(out_data))
                else:
                    print(f"Found {len(features)} Features for device {ctx.dev_id}:")

                    for item in features:
                        val = format_feature(item)
                        if len(val) > 80:
                            val = val[:77] + "..."
                        writable_mark = "*" if item.is_writable else " "
                        print(f"{writable_mark} {item.name:<75}: {val}")
                    print("(* = writable)")

            # Simple Listing
            elif args.json:
                print(json.dumps([f.name for f in features]))
            else:
                _print_simple_feature_list(features, ctx.dev_id)

    except Exception as e:
        _LOGGER.error("Error listing features: %s", e)


def _print_simple_feature_list(features: list[Any], dev_id: str) -> None:
    """Print a simple list of feature names."""
    print(f"Found {len(features)} Features for device {dev_id}:")
    for feature in features:
        print(f"- {feature.name}")


async def cmd_get_feature(args) -> None:
    """Get a specific feature.

    Fetches and displays details for a single feature by name.

    Args:
        args: Parsed command line arguments including feature_name and raw flag.
    """
    try:
        async with setup_client_context(args) as ctx:
            device = _transient_device(ctx)
            features = await ctx.client.get_features(
                device, feature_names=[args.feature_name]
            )
            if not features:
                raise ViNotFoundError(f"Feature '{args.feature_name}' not found.")
            feature = features[0]

            if args.raw:
                # Show internal object structure
                print(
                    json.dumps(
                        {
                            "name": feature.name,
                            "value": feature.value,
                            "unit": feature.unit,
                            "control": str(feature.control)
                            if feature.control
                            else None,
                        },
                        indent=2,
                        default=str,
                    )
                )
            else:
                print(f"- {feature.name}: {format_feature(feature)}")
                if feature.control:
                    ctrl = feature.control
                    print(f"  Writable via command: {ctrl.command_name}")
                    print(f"  Target param: {ctrl.param_name}")
                    if ctrl.min is not None:
                        print(
                            f"  Constraints: min={ctrl.min}, max={ctrl.max}, "
                            f"step={ctrl.step}"
                        )
                    if ctrl.options:
                        print(f"  Options: {ctrl.options}")

    except ViNotFoundError:  # Catch before Exception
        print(f"Feature '{args.feature_name}' not found.")
    except Exception as e:
        _LOGGER.error("Error fetching feature: %s", e)


async def cmd_get_consumption(args) -> None:
    """Get consumption data.

    Fetches energy consumption for today based on the selected metric.

    Args:
        args: Parsed command line arguments including metric (summary, total, etc.).
    """
    try:
        async with setup_client_context(args) as ctx:
            print(f"Fetching consumption (Metric: {args.metric})...")
            device = _transient_device(ctx)

            # Helper for CLI "today"
            now = datetime.now()
            start_dt = now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            end_dt = now.replace(
                hour=23, minute=59, second=59, microsecond=999999
            ).isoformat()

            result = await ctx.client.get_consumption(
                device, start_dt, end_dt, metric=args.metric
            )
            if isinstance(result, list):
                print(f"Feature expanded to {len(result)} items:")

                for feature in result:
                    print(f"- {feature.name}: {format_feature(feature)}")
            else:
                print(f"Feature: {result.name}")
                print(f"Value: {format_feature(result)}")
    except Exception as e:
        _LOGGER.error("Error fetching consumption: %s", e)


async def cmd_set(args) -> None:
    """Set a feature value (User Friendly).

    Sets a feature to a new value using the high-level set_feature API.

    Args:
        args: Parsed command line arguments including feature_name and value.
    """
    try:
        async with setup_client_context(args) as ctx:
            device = _transient_device(ctx)
            features = await ctx.client.get_features(
                device, feature_names=[args.feature_name]
            )
            if not features:
                print(f"Error: Feature '{args.feature_name}' not found.")
                return
            feature = features[0]

            if not feature.control:
                print(f"Error: Feature '{feature.name}' is read-only (no control).")
                return

            print(f"Setting '{feature.name}' to '{args.value}'...")
            # We show this for transparency but it's not needed by user
            print(
                f"  (Command: {feature.control.command_name}, "
                f"Param: {feature.control.param_name})"
            )

            # Simple type conversion
            target_val = args.value
            with suppress(ValueError):
                target_val = float(args.value)

            result = await ctx.client.set_feature(device, feature, target_val)

            if result.success:
                print("✅ Success!")
            else:
                print("❌ Failed!")
                if result.message:
                    print(f"Message: {result.message}")
                if result.reason:
                    print(f"Reason: {result.reason}")

    except ViValidationError as e:
        print(f"Validation failed: {e}")
    except ViNotFoundError as e:
        print(f"Not found: {e}")
    except Exception as e:
        _LOGGER.error("Error setting feature: %s", e)


async def cmd_exec(args) -> None:
    """Execute a command (Advanced).

    Executes a raw command with parameters. For advanced users.

    Args:
        args: Parsed command line arguments including feature_name,
            command_name, and params.
    """
    # 1. Parse params
    try:
        params_dict = parse_cli_params(args.params) if args.params else {}
    except ValueError as e:
        print(f"Error parsing parameters: {e}")
        return

    try:
        async with setup_client_context(args) as ctx:
            # 2. Find Feature
            feature = await _fetch_target_feature(ctx, args.feature_name)
            if not feature:
                return

            if not feature.control:
                print(f"Error: Feature '{feature.name}' is read-only (no control).")
                return

            # 3. Validate Command Name
            if feature.control.command_name != args.command_name:
                print(
                    f"Warning: Feature expects command '{feature.control.command_name}'"
                    f", but you specified '{args.command_name}'."
                )
                print("Error: Features only expose their primary control command.")
                return

            print(f"Executing '{args.command_name}' on {feature.name}...")

            # 4. Determine Value
            target_val = _determine_target_value(args.params, params_dict, feature)

            # 5. Execute
            if target_val is not None:
                print(f"Using high-level set_feature(target={target_val})...")
                result = await ctx.client.set_feature(
                    # Reconstruct transient device if needed, or use ctx
                    _transient_device(ctx),
                    feature,
                    target_val,
                )
            else:
                print(f"Using low-level POST with params: {params_dict}")
                result = await ctx.client.connector.post(
                    feature.control.uri, params_dict
                )
                result = CommandResponse.from_api(result)

            _print_command_result(result)

    except ViValidationError as e:
        print(f"Validation failed: {e}")
    except ViNotFoundError as e:
        print(f"Not found: {e}")
    except Exception as e:
        _LOGGER.error("Error executing command: %s", e)


async def _fetch_target_feature(ctx: CLIContext, name: str) -> Any | None:
    """Helper to fetch a single feature by name."""
    device = _transient_device(ctx)
    features = await ctx.client.get_features(device, feature_names=[name])
    if not features:
        print(f"Error: Feature '{name}' not found.")
        return None
    return features[0]


def _transient_device(ctx: CLIContext) -> Device:
    """Create a transient device object from context."""
    return Device(
        id=ctx.dev_id,
        gateway_serial=ctx.gw_serial,
        installation_id=ctx.inst_id,
        model_id="transient",
        device_type="unknown",
        status="online",
    )


def _determine_target_value(
    raw_params: list[str], params_dict: dict[str, Any], feature: Any
) -> Any | None:
    """Determine the target value from CLI params."""
    # Try from dict
    val = params_dict.get(feature.control.param_name)
    if val is not None:
        return val

    # Try raw scalar
    if raw_params and len(raw_params) == 1 and "=" not in raw_params[0]:
        raw_arg = raw_params[0]
        with suppress(ValueError):
            return float(raw_arg)
        return raw_arg

    return None


def _print_command_result(result: CommandResponse) -> None:
    """Print the result of a command execution."""
    if result.success:
        print("✅ Success!")
    else:
        print("❌ Failed!")

    if result.message:
        print(f"Message: {result.message}")
    if result.reason:
        print(f"Reason: {result.reason}")


async def cmd_list_writable(args) -> None:
    """List all writable features for a device.

    Displays features that have a control block (can be modified).

    Args:
        args: Parsed command line arguments.
    """
    try:
        async with setup_client_context(args) as ctx:
            # Fetch all features to introspect commands

            device = _transient_device(ctx)
            features = await ctx.client.get_features(device)

            writable_features = [feature for feature in features if feature.is_writable]

            print(f"\nFound {len(writable_features)} writable features:\n")

            for feature in writable_features:
                ctrl = feature.control
                print(f"- {feature.name}")
                print(f"    Param:   {ctrl.param_name} (via {ctrl.command_name})")
                _print_feature_constraints(ctrl)
                print("")

    except Exception as e:
        _LOGGER.error("Error listing writable features: %s", e)


def _print_feature_constraints(ctrl: Any) -> None:
    """Helper to print constraints for a feature control.

    Args:
        ctrl: The FeatureControl object containing constraint metadata.
    """
    constraints = []
    if ctrl.min is not None:
        constraints.append(f"min: {ctrl.min}")
    if ctrl.max is not None:
        constraints.append(f"max: {ctrl.max}")
    if ctrl.step is not None:
        constraints.append(f"step: {ctrl.step}")
    if ctrl.options:
        constraints.append(f"options: {ctrl.options}")

    # String Constraints
    if hasattr(ctrl, "min_length") and ctrl.min_length is not None:
        constraints.append(f"min_length: {ctrl.min_length}")
    if hasattr(ctrl, "max_length") and ctrl.max_length is not None:
        constraints.append(f"max_length: {ctrl.max_length}")
    if hasattr(ctrl, "pattern") and ctrl.pattern is not None:
        constraints.append(f"pattern: {ctrl.pattern}")

    if constraints:
        print(f"    Constraints: {', '.join(constraints)}")


async def cmd_list_mock_devices(args) -> None:
    """List available mock devices.

    Lists fixture files that can be used for offline testing.

    Args:
        args: Parsed command line arguments (unused but required for dispatch).
    """
    devices = MockViClient.get_available_mock_devices()
    print("Available Mock Devices:")
    for device in devices:
        print(f"- {device}")


async def main() -> None:  # noqa: PLR0915
    """Main CLI entrypoint."""
    # Parent parser for common arguments
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--client-id", help="OAuth Client ID (optional if saved)"
    )
    common_parser.add_argument(
        "--redirect-uri", default="http://localhost:4200/", help="OAuth Redirect URI"
    )
    common_parser.add_argument(
        "--token-file", default=TOKEN_FILE, help="Path to save/load tokens"
    )
    common_parser.add_argument(
        "--insecure", action="store_true", help="Disable SSL verification"
    )
    common_parser.add_argument(
        "--mock-device", help="Use a mock device (e.g. Vitodens200W)"
    )

    parser = argparse.ArgumentParser(description="Viessmann API CLI")
    subparsers = parser.add_subparsers(dest="command")

    # Login
    subparsers.add_parser(
        "login", help="Authenticate with Viessmann", parents=[common_parser]
    )

    # List Devices
    subparsers.add_parser(
        "list-devices", help="List installations and devices", parents=[common_parser]
    )

    # List Features
    parser_features = subparsers.add_parser(
        "list-features", help="List all features for a device", parents=[common_parser]
    )
    parser_features.add_argument(
        "--installation-id", type=int, help="Installation ID (optional)"
    )
    parser_features.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    parser_features.add_argument("--device-id", help="Device ID (optional)")
    parser_features.add_argument(
        "--enabled", action="store_true", help="List only enabled features"
    )
    parser_features.add_argument(
        "--values", action="store_true", help="Show feature values"
    )
    parser_features.add_argument(
        "--json", action="store_true", help="Output JSON (for lists)"
    )

    # Get Feature
    parser_feature = subparsers.add_parser(
        "get-feature", help="Get a specific feature", parents=[common_parser]
    )
    parser_feature.add_argument(
        "feature_name", help="Feature Name (e.g. heating.circuits.0)"
    )
    parser_feature.add_argument(
        "--installation-id", type=int, help="Installation ID (optional)"
    )
    parser_feature.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    parser_feature.add_argument("--device-id", help="Device ID (optional)")
    parser_feature.add_argument(
        "--raw", action="store_true", help="Show raw JSON response"
    )

    # Get Consumption
    parser_consumption = subparsers.add_parser(
        "get-consumption",
        help="Get energy consumption for today",
        parents=[common_parser],
    )
    parser_consumption.add_argument(
        "--metric",
        default="summary",
        choices=["summary", "total", "heating", "dhw"],
        help="Metric to fetch",
    )
    parser_consumption.add_argument(
        "--installation-id", type=int, help="Installation ID (optional)"
    )
    parser_consumption.add_argument(
        "--gateway-serial", help="Gateway Serial (optional)"
    )
    parser_consumption.add_argument("--device-id", help="Device ID (optional)")

    # List available mock devices
    subparsers.add_parser(
        "list-mock-devices", help="List available mock devices", parents=[common_parser]
    )

    # List Writable Features
    parser_cmds = subparsers.add_parser(
        "list-writable",
        help="List all writable features (commands)",
        parents=[common_parser],
    )
    parser_cmds.add_argument(
        "--installation-id", type=int, help="Installation ID (optional)"
    )
    parser_cmds.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    parser_cmds.add_argument("--device-id", help="Device ID (optional)")

    # Set Value
    parser_set = subparsers.add_parser(
        "set",
        help="Set a feature value",
        parents=[common_parser],
    )
    parser_set.add_argument(
        "feature_name",
        help="Feature Name (e.g. heating.circuits.0.heating.curve.slope)",
    )
    parser_set.add_argument("value", help="Value to set")
    parser_set.add_argument(
        "--installation-id", type=int, help="Installation ID (optional)"
    )
    parser_set.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    parser_set.add_argument("--device-id", help="Device ID (optional)")

    # Exec Command (Advanced)
    parser_exec = subparsers.add_parser(
        "exec",
        help="Execute a raw command (Advanced)",
        parents=[common_parser],
    )
    parser_exec.add_argument("feature_name", help="Feature Name")
    parser_exec.add_argument("command_name", help="Command Name")
    parser_exec.add_argument("params", nargs="*", help="Parameters (key=value)")
    parser_exec.add_argument(
        "--installation-id", type=int, help="Installation ID (optional)"
    )
    parser_exec.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    parser_exec.add_argument("--device-id", help="Device ID (optional)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    with suppress(KeyboardInterrupt):
        await _dispatch_command(args)


async def _dispatch_command(args: argparse.Namespace) -> None:
    """Dispatch command to appropriate handler."""
    # Pre-checks for login
    if args.command == "login" and (
        not args.client_id and not os.getenv("VIESSMANN_CLIENT_ID")
    ):
        # Check config one last time before failing
        config = load_config(args.token_file)
        if not config.get("client_id"):
            print(
                "Error: --client-id is required for initial login "
                "(or use VIESSMANN_CLIENT_ID env var)."
            )
            sys.exit(1)

    handlers = {
        "login": cmd_login,
        "list-devices": cmd_list_devices,
        "list-features": cmd_list_features,
        "get-feature": cmd_get_feature,
        "get-consumption": cmd_get_consumption,
        "list-mock-devices": cmd_list_mock_devices,
        "list-writable": cmd_list_writable,
        "set": cmd_set,
        "exec": cmd_exec,
    }

    handler = handlers.get(args.command)
    if handler:
        await handler(args)


if __name__ == "__main__":
    asyncio.run(main())
