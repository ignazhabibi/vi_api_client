"""CLI for Viessmann Client."""

import argparse
import asyncio
import json
import logging
import math
import os
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path

import aiohttp

from vi_api_client import (
    FixtureViClient,
    OAuth,
    ViClient,
    ViNotFoundError,
    ViResponseError,
    ViValidationError,
)

from .credentials import CredentialDocument
from .models import (
    CommandResponse,
    Device,
    EventHistoryPage,
    Feature,
    FeatureControl,
    InstallationEvent,
)
from .utils import format_feature, parse_cli_params
from .validation import validate_json_value

# Default file to store tokens and config
DEFAULT_REDIRECT_URI = "http://localhost:4200/"
TOKEN_FILE = "tokens.json"

_LOGGER = logging.getLogger(__name__)


def _argument_value(args: argparse.Namespace, name: str) -> object:
    """Return one parsed command line argument, or None when absent.

    Arguments come from the dynamic argparse namespace; each caller
    narrows an argument to its concrete expected shape. Path-shaped
    arguments are read separately.
    """
    return getattr(args, name, None)


def _str_argument(args: argparse.Namespace, name: str) -> str:
    """Return a required string command line argument.

    Raises:
        ValueError: If the argument is absent or not a string.
    """
    value = _argument_value(args, name)
    if not isinstance(value, str):
        raise ValueError(f"Command line argument '{name}' must be a string")
    return value


def _optional_str_argument(args: argparse.Namespace, name: str) -> str | None:
    """Return an optional string command line argument."""
    value = _argument_value(args, name)
    return value if isinstance(value, str) else None


def _flag_argument(args: argparse.Namespace, name: str) -> bool:
    """Return a boolean command line flag."""
    return _argument_value(args, name) is True


def _token_file_argument(args: argparse.Namespace) -> str | Path:
    """Return the credential document path argument.

    Raises:
        ValueError: If the argument is absent or not a path.
    """
    value: object = getattr(args, "token_file", None)
    if isinstance(value, str | Path):
        return value
    raise ValueError("Command line argument 'token_file' must be a path")


def _params_argument(args: argparse.Namespace) -> list[str]:
    """Return the exec command's parameter list argument.

    The list is validated at the dynamic argparse boundary; the exec
    subparser always provides the argument, so absent means empty.

    Raises:
        ValueError: If the argument is present but not a list of strings.
    """
    try:
        value = validate_json_value(
            getattr(args, "params", None), path="Command parameters"
        )
    except ViResponseError as error:
        raise ValueError(str(error)) from error
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Command line argument 'params' must be a list")
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Command line argument 'params' must be a list of strings")
        strings.append(item)
    return strings


def _installation_id_argument(args: argparse.Namespace) -> str | None:
    """Return the installation ID argument in its API string form.

    The parser reports ``--installation-id`` as an integer while the API
    reports installation identifiers as strings, so both shapes normalize
    to text. Identifiers that are absent or empty stay absent.
    """
    value = _argument_value(args, "installation_id")
    if isinstance(value, str):
        return value or None
    if isinstance(value, int) and not isinstance(value, bool) and value:
        return str(value)
    return None


def _positive_int_argument(args: argparse.Namespace, name: str) -> int | None:
    """Return an optional positive integer command line argument.

    Raises:
        ValueError: If the argument is present but not a positive integer.
    """
    value = _argument_value(args, name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"Command line argument '{name}' must be a positive integer")
    return value


def _print_diagnostic(args: argparse.Namespace, message: str) -> None:
    """Print setup information without contaminating requested JSON output."""
    print(message, file=sys.stderr if _flag_argument(args, "json") else sys.stdout)


@dataclass
class CLIContext:
    """Context for CLI commands."""

    session: aiohttp.ClientSession | None
    client: ViClient | FixtureViClient
    # Found IDs (either from args or auto-discovery)
    inst_id: str | None
    gw_serial: str | None
    dev_id: str | None


async def create_session(args: argparse.Namespace) -> aiohttp.ClientSession:
    """Create aiohttp session with optional insecure SSL.

    Args:
        args: Parsed command line arguments.

    Returns:
        An aiohttp ClientSession configured according to args.
    """
    if _flag_argument(args, "insecure"):
        _print_diagnostic(args, "WARNING: SSL verification disabled via --insecure")
        connector = aiohttp.TCPConnector(ssl=False)
        return aiohttp.ClientSession(connector=connector)
    return aiohttp.ClientSession()


async def cmd_login(args: argparse.Namespace) -> bool:
    """Handle login command.

    Guides the user through OAuth authorization flow.

    Args:
        args: Parsed command line arguments including client_id and redirect_uri.
    """
    client_id, redirect_uri = get_client_config(args)
    token_file = _token_file_argument(args)

    async with await create_session(args) as session:
        auth = OAuth(client_id, redirect_uri, token_file, websession=session)
        url = auth.get_authorization_url()

        print(f"Please visit the following URL to log in:\n\n{url}\n")
        print(f"After verifying, you will be redirected to {redirect_uri}?code=...")
        code = input("Paste the 'code' parameter from the URL here: ").strip()

        await auth.async_fetch_details_from_code(code)

    CredentialDocument(Path(token_file)).update(
        {"client_id": client_id, "redirect_uri": redirect_uri}
    )
    print(f"Successfully authenticated! Tokens and config saved to {token_file}")
    return True


def get_client_config(args: argparse.Namespace) -> tuple[str, str]:
    """Get client_id and redirect_uri from args or file."""
    config = CredentialDocument(Path(_token_file_argument(args))).read()

    configured_client_id = config.get("client_id")
    configured_redirect_uri = config.get("redirect_uri")
    client_id = _optional_str_argument(args, "client_id") or os.getenv(
        "VIESSMANN_CLIENT_ID"
    )
    if not client_id and isinstance(configured_client_id, str):
        client_id = configured_client_id
    redirect_uri = _optional_str_argument(args, "redirect_uri") or os.getenv(
        "VIESSMANN_REDIRECT_URI"
    )
    if not redirect_uri and isinstance(configured_redirect_uri, str):
        redirect_uri = configured_redirect_uri
    redirect_uri = redirect_uri or DEFAULT_REDIRECT_URI

    if not client_id:
        _print_diagnostic(
            args,
            "Error: Client ID not found. Provide via --client-id or "
            "VIESSMANN_CLIENT_ID env var.",
        )
        _print_diagnostic(args, "Please run 'login' first or provide --client-id.")
        sys.exit(1)

    return client_id, redirect_uri


@asynccontextmanager
async def setup_client_context(
    args: argparse.Namespace, discover: bool = True
) -> AsyncGenerator[CLIContext]:
    """Creates Session, Auth, Client AND performs Auto-Discovery if needed."""
    fixture_device = _optional_str_argument(args, "fixture_device")
    if fixture_device:
        client = FixtureViClient(fixture_device)
        inst_id = _installation_id_argument(args) or "99999"
        gw_serial = _optional_str_argument(args, "gateway_serial") or "MOCK_GATEWAY"
        dev_id = _optional_str_argument(args, "device_id") or "0"
        _print_diagnostic(args, f"Using Fixture Device: {fixture_device}")
        yield CLIContext(None, client, inst_id, gw_serial, dev_id)
        return

    client_id, redirect_uri = get_client_config(args)

    async with await create_session(args) as session:
        auth = OAuth(client_id, redirect_uri, _token_file_argument(args), session)

        client = ViClient(auth)
        inst_id = _installation_id_argument(args)
        gw_serial = _optional_str_argument(args, "gateway_serial")
        dev_id = _optional_str_argument(args, "device_id")

        # Perform Auto-Discovery if IDs are missing.
        if discover and not (inst_id and gw_serial and dev_id):
            gateways = await client.get_gateways()
            if not gateways:
                _print_diagnostic(args, "No gateways found.")
                raise ValueError("No gateways found.")

            if gw_serial:
                gateway = next(
                    (gateway for gateway in gateways if gateway.serial == gw_serial),
                    None,
                )
                if not gateway:
                    raise ValueError(f"Gateway '{gw_serial}' not found.")
                if inst_id and gateway.installation_id != inst_id:
                    raise ValueError(
                        f"Gateway '{gw_serial}' does not belong to installation "
                        f"'{inst_id}'."
                    )
            elif inst_id:
                gateway = next(
                    (
                        gateway
                        for gateway in gateways
                        if gateway.installation_id == inst_id
                    ),
                    None,
                )
                if not gateway:
                    raise ValueError(f"No gateway found for installation '{inst_id}'.")
            else:
                gateway = gateways[0]

            inst_id = gateway.installation_id
            gw_serial = gateway.serial

            if not dev_id:
                devices = await client.get_devices(inst_id, gw_serial)
                if not devices:
                    raise ValueError("No devices found.")

                # Prefer device "0" (heating system).
                target_dev = next(
                    (device for device in devices if device.id == "0"),
                    devices[0],
                )
                dev_id = target_dev.id
                _print_diagnostic(
                    args,
                    f"Auto-selected Context: Inst={inst_id}, GW={gw_serial}, "
                    f"Dev={dev_id}",
                )

        # Defensive: discovery above either raises or completes every ID.
        if discover and not (inst_id and gw_serial and dev_id):  # pragma: no cover
            raise ValueError(
                "Installation ID, gateway serial, and device ID are required when "
                "auto-discovery is disabled."
            )

        yield CLIContext(session, client, inst_id, gw_serial, dev_id)


async def cmd_list_devices(args: argparse.Namespace) -> bool:
    """List installations and devices.

    Fetches and prints all installations, gateways, and devices.

    Args:
        args: Parsed command line arguments.
    """
    # Does not use full context discovery, just client
    try:
        async with setup_client_context(args, discover=False) as ctx:
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
        return False

    return True


async def cmd_list_features(args: argparse.Namespace) -> bool:
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
            features = await ctx.client.get_features(
                device, only_enabled=_flag_argument(args, "enabled")
            )

            if _flag_argument(args, "values"):
                if _flag_argument(args, "json"):
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
            elif _flag_argument(args, "json"):
                print(json.dumps([f.name for f in features]))
            else:
                _print_simple_feature_list(features, device.id)

    except Exception as e:
        _LOGGER.error("Error listing features: %s", e)
        return False

    return True


def _print_simple_feature_list(features: Sequence[Feature], dev_id: str) -> None:
    """Print a simple list of feature names."""
    print(f"Found {len(features)} Features for device {dev_id}:")
    for feature in features:
        print(f"- {feature.name}")


async def cmd_get_feature(args: argparse.Namespace) -> bool:
    """Get a specific feature.

    Fetches and displays details for a single feature by name.

    Args:
        args: Parsed command line arguments including feature_name and raw flag.
    """
    feature_name = _str_argument(args, "feature_name")
    try:
        async with setup_client_context(args) as ctx:
            device = _transient_device(ctx)
            features = await ctx.client.get_features(
                device, feature_names=[feature_name]
            )
            if not features:
                raise ViNotFoundError(f"Feature '{feature_name}' not found.")
            feature = features[0]

            if _flag_argument(args, "raw"):
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
        print(f"Feature '{feature_name}' not found.")
        return False
    except Exception as e:
        _LOGGER.error("Error fetching feature: %s", e)
        return False

    return True


async def cmd_set(args: argparse.Namespace) -> bool:  # noqa: PLR0911
    """Set a feature value (User Friendly).

    Sets a feature to a new value using the high-level set_feature API.

    Args:
        args: Parsed command line arguments including feature_name and value.
    """
    feature_name = _str_argument(args, "feature_name")
    raw_value = _str_argument(args, "value")
    try:
        async with setup_client_context(args) as ctx:
            target = await _fetch_target_feature(ctx, feature_name)
            if target is None:
                return False
            device, feature = target

            if not feature.control:
                print(f"Error: Feature '{feature.name}' is read-only (no control).")
                return False

            print(f"Setting '{feature.name}' to '{raw_value}'...")
            # We show this for transparency but it's not needed by user
            print(
                f"  (Command: {feature.control.command_name}, "
                f"Param: {feature.control.param_name})"
            )

            target_val = _parse_set_value(raw_value, feature)

            result, _updated_device = await ctx.client.set_feature(
                device, feature, target_val
            )

            if result.success:
                print("✅ Success!")
                return True

            print("❌ Failed!")
            if result.message:
                print(f"Message: {result.message}")
            if result.reason:
                print(f"Reason: {result.reason}")
            return False

    except ViValidationError as e:
        print(f"Validation failed: {e}")
        return False
    except ViNotFoundError as e:
        print(f"Not found: {e}")
        return False
    except Exception as e:
        _LOGGER.error("Error setting feature: %s", e)
        return False


def _parse_set_value(raw_value: str, feature: Feature) -> bool | float | int | str:
    """Convert a CLI value according to the target command parameter type.

    Args:
        raw_value: Value provided after the CLI ``set`` command.
        feature: Writable feature that supplies the command metadata.

    Returns:
        The value in the type expected by the target command.

    Raises:
        ViValidationError: If a numeric or boolean command value is malformed.
    """
    # Defensive: cmd_set rejects read-only features before parsing values.
    if feature.control is None:  # pragma: no cover
        return raw_value

    value_type = feature.control.value_type
    if value_type is None:
        value_type = _infer_feature_value_type(feature)

    if value_type == "boolean":
        normalized_value = raw_value.casefold()
        if normalized_value == "true":
            return True
        if normalized_value == "false":
            return False
        raise ViValidationError(
            f"Value '{raw_value}' for '{feature.name}' must be true or false."
        )

    if value_type == "integer":
        try:
            return int(raw_value)
        except ValueError as error:
            raise ViValidationError(
                f"Value '{raw_value}' for '{feature.name}' must be an integer."
            ) from error

    if value_type == "number":
        try:
            value = float(raw_value)
        except ValueError as error:
            raise ViValidationError(
                f"Value '{raw_value}' for '{feature.name}' must be a number."
            ) from error
        if not math.isfinite(value):
            raise ViValidationError(
                f"Value '{raw_value}' for '{feature.name}' must be finite."
            )
        return value

    return raw_value


def _infer_feature_value_type(feature: Feature) -> str:
    """Infer a legacy feature's command type when API metadata is unavailable."""
    if isinstance(feature.value, bool):
        return "boolean"
    if isinstance(feature.value, int | float):
        return "number"
    if feature.control and any(
        constraint is not None
        for constraint in (
            feature.control.min,
            feature.control.max,
            feature.control.step,
        )
    ):
        return "number"
    return "string"


async def cmd_exec(args: argparse.Namespace) -> bool:  # noqa: PLR0911
    """Execute a command (Advanced).

    Executes a raw command with parameters. For advanced users.

    Args:
        args: Parsed command line arguments including feature_name,
            command_name, and params.
    """
    feature_name = _str_argument(args, "feature_name")
    command_name = _str_argument(args, "command_name")
    params = _params_argument(args)

    # 1. Parse params
    try:
        params_dict = parse_cli_params(params) if params else {}
    except ValueError as e:
        print(f"Error parsing parameters: {e}")
        return False

    try:
        async with setup_client_context(args) as ctx:
            # 2. Find Feature
            target = await _fetch_target_feature(ctx, feature_name)
            if target is None:
                return False
            _device, feature = target

            control = feature.control
            if control is None:
                print(f"Error: Feature '{feature.name}' is read-only (no control).")
                return False

            # 3. Validate Command Name
            if control.command_name != command_name:
                print(
                    f"Warning: Feature expects command '{control.command_name}'"
                    f", but you specified '{command_name}'."
                )
                print("Error: Features only expose their primary control command.")
                return False

            print(f"Executing '{command_name}' on {feature.name}...")

            # 4. Execute the explicitly supplied parameter set unchanged.
            print(f"Using execute_command with params: {params_dict}")
            result = await ctx.client.execute_command(feature, params_dict)

            return _print_command_result(result)

    except ViValidationError as e:
        print(f"Validation failed: {e}")
        return False
    except ViNotFoundError as e:
        print(f"Not found: {e}")
        return False
    except Exception as e:
        _LOGGER.error("Error executing command: %s", e)
        return False


async def _fetch_target_feature(
    ctx: CLIContext, name: str
) -> tuple[Device, Feature] | None:
    """Fetch a target feature together with its complete device context."""
    device = _transient_device(ctx)
    features = await ctx.client.get_features(device)
    device_snapshot = replace(device, features=features)
    feature = device_snapshot.get_feature(name)
    if feature is None:
        print(f"Error: Feature '{name}' not found.")
        return None
    return device_snapshot, feature


def _transient_device(ctx: CLIContext) -> Device:
    """Create a transient device object from context."""
    if not (ctx.inst_id and ctx.gw_serial and ctx.dev_id):
        raise ValueError("A device context is required for this command.")

    return Device(
        id=ctx.dev_id,
        gateway_serial=ctx.gw_serial,
        installation_id=ctx.inst_id,
        model_id="transient",
        device_type="unknown",
        status="online",
    )


def _print_command_result(result: CommandResponse) -> bool:
    """Print the result of a command execution."""
    if result.success:
        print("✅ Success!")
    else:
        print("❌ Failed!")

    if result.message:
        print(f"Message: {result.message}")
    if result.reason:
        print(f"Reason: {result.reason}")

    return result.success


async def cmd_list_writable(args: argparse.Namespace) -> bool:
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
                # Defensive: is_writable implies a control is present.
                if ctrl is None:  # pragma: no cover
                    continue
                print(f"- {feature.name}")
                print(f"    Param:   {ctrl.param_name} (via {ctrl.command_name})")
                _print_feature_constraints(ctrl)
                print("")

    except Exception as e:
        _LOGGER.error("Error listing writable features: %s", e)
        return False

    return True


def _print_feature_constraints(ctrl: FeatureControl) -> None:
    """Helper to print constraints for a feature control.

    Args:
        ctrl: The FeatureControl command metadata containing constraints.
    """
    constraints: list[str] = []
    if ctrl.min is not None:
        constraints.append(f"min: {ctrl.min}")
    if ctrl.max is not None:
        constraints.append(f"max: {ctrl.max}")
    if ctrl.step is not None:
        constraints.append(f"step: {ctrl.step}")
    if ctrl.options:
        constraints.append(f"options: {ctrl.options}")

    # String Constraints
    if ctrl.min_length is not None:
        constraints.append(f"min_length: {ctrl.min_length}")
    if ctrl.max_length is not None:
        constraints.append(f"max_length: {ctrl.max_length}")
    if ctrl.pattern is not None:
        constraints.append(f"pattern: {ctrl.pattern}")

    if constraints:
        print(f"    Constraints: {', '.join(constraints)}")


async def cmd_list_events(args: argparse.Namespace) -> bool:
    """List one page of an installation's event history.

    Prints a readable first-page summary for the requested rolling ``--days``
    window, or one JSON document with the complete events and pagination
    metadata with ``--json``.

    Args:
        args: Parsed command line arguments including days and json flag.
    """
    try:
        days = _positive_int_argument(args, "days")
        limit = _positive_int_argument(args, "limit")
    except ValueError as error:
        _print_diagnostic(args, f"Error: {error}")
        return False
    if days is None:
        _print_diagnostic(args, "Error: Command line argument 'days' is required")
        return False

    try:
        async with setup_client_context(args, discover=False) as ctx:
            installation_id = ctx.inst_id
            if installation_id is None:
                installations = await ctx.client.get_installations()
                if not installations:
                    raise ValueError("No installations found.")
                installation_id = installations[0].id
                _print_diagnostic(
                    args, f"Auto-selected installation: {installation_id}"
                )

            page = await ctx.client.get_event_history(
                installation_id, days=days, limit=limit
            )

            if _flag_argument(args, "json"):
                print(
                    json.dumps(
                        {
                            "installationId": installation_id,
                            "events": [dict(event.fields) for event in page.events],
                            "nextCursor": page.next_cursor,
                        }
                    )
                )
            else:
                _print_event_summary(page, installation_id, days)

    except Exception as e:
        _LOGGER.error("Error listing events: %s", e)
        return False

    return True


def _print_event_summary(
    page: EventHistoryPage, installation_id: str, days: int
) -> None:
    """Print a readable summary of one event history page."""
    print(
        f"Found {len(page.events)} event(s) for installation "
        f"{installation_id} (last {days} days):"
    )
    for event in page.events:
        gateway = f", gateway {event.gateway_serial}" if event.gateway_serial else ""
        print(f"- {event.event_timestamp} {event.event_type}{gateway}")
        details = _format_event_details(event)
        if details:
            print(f"    {details}")
    if page.next_cursor:
        print(
            "More events may be available "
            f"(next cursor: {page.next_cursor}); run with --json to copy it"
        )


def _format_event_details(event: InstallationEvent) -> str | None:
    """Return a readable summary of one event body, or None when empty.

    Event bodies depend on the event type; the known feature-change shape is
    summarized field by field and any other body falls back to compact JSON.

    Args:
        event: The event whose body is summarized.

    Returns:
        A one-line detail text, or None when the event has no body.
    """
    body = event.body
    if body is None:
        return None
    if not isinstance(body, dict):
        return _truncate_event_details(json.dumps(body))
    feature_name = body.get("featureName")
    if not isinstance(feature_name, str) or not feature_name:
        return _truncate_event_details(json.dumps(body))
    details = f"feature: {feature_name}"
    command_name = body.get("commandName")
    if isinstance(command_name, str) and command_name:
        details += f", command: {command_name}"
    command_body = body.get("commandBody")
    if command_body is not None:
        details += f", params: {json.dumps(command_body)}"
    return _truncate_event_details(details)


def _truncate_event_details(details: str) -> str:
    """Keep one event detail line within the readable summary width."""
    if len(details) > 120:
        return details[:117] + "..."
    return details


async def cmd_list_fixture_devices(args: argparse.Namespace) -> bool:
    """List available fixture devices.

    Lists fixture files that can be used for offline testing.

    Args:
        args: Parsed command line arguments (unused but required for dispatch).
    """
    devices = FixtureViClient.get_available_fixture_devices()
    print("Available Fixture Devices:")
    for device in devices:
        print(f"- {device}")
    return True


async def async_main() -> int:  # noqa: PLR0915
    """Main CLI entrypoint."""
    # Parent parser for common arguments
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--client-id", help="OAuth Client ID (optional if saved)"
    )
    common_parser.add_argument("--redirect-uri", help="OAuth Redirect URI")
    common_parser.add_argument(
        "--token-file", default=TOKEN_FILE, help="Path to save/load tokens"
    )
    common_parser.add_argument(
        "--insecure", action="store_true", help="Disable SSL verification"
    )
    common_parser.add_argument(
        "--fixture-device", help="Use a fixture device (e.g. Vitodens200W)"
    )
    common_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging for the CLI and the library",
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

    # List Installation Events
    parser_events = subparsers.add_parser(
        "list-events",
        help="List one page of the installation event history",
        parents=[common_parser],
    )
    parser_events.add_argument(
        "--installation-id", type=int, help="Installation ID (optional)"
    )
    parser_events.add_argument(
        "--days",
        type=int,
        required=True,
        help="Rolling lookback window in days",
    )
    parser_events.add_argument(
        "--limit",
        type=int,
        help="Page limit (1-1000; provider default when omitted)",
    )
    parser_events.add_argument(
        "--json",
        action="store_true",
        help="Output one JSON document with full events and pagination metadata",
    )

    # List available fixture devices
    subparsers.add_parser(
        "list-fixture-devices",
        help="List available fixture devices",
        parents=[common_parser],
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

    logging.basicConfig(
        level=logging.DEBUG if _flag_argument(args, "verbose") else logging.INFO,
        format="%(message)s",
    )

    if not _optional_str_argument(args, "command"):
        parser.print_help()
        return 0

    try:
        return await _dispatch_command(args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        _LOGGER.error("Error executing command: %s", e)
        return 1


async def _dispatch_command(args: argparse.Namespace) -> int:
    """Dispatch command to appropriate handler."""
    command = _optional_str_argument(args, "command")

    # Pre-checks for login
    if command == "login" and (
        not _optional_str_argument(args, "client_id")
        and not os.getenv("VIESSMANN_CLIENT_ID")
    ):
        # Check config one last time before failing
        config = CredentialDocument(Path(_token_file_argument(args))).read()
        if not config.get("client_id"):
            print(
                "Error: --client-id is required for initial login "
                "(or use VIESSMANN_CLIENT_ID env var)."
            )
            return 1

    handlers: dict[str, Callable[[argparse.Namespace], Awaitable[bool]]] = {
        "login": cmd_login,
        "list-devices": cmd_list_devices,
        "list-features": cmd_list_features,
        "get-feature": cmd_get_feature,
        "list-events": cmd_list_events,
        "list-fixture-devices": cmd_list_fixture_devices,
        "list-writable": cmd_list_writable,
        "set": cmd_set,
        "exec": cmd_exec,
    }

    if command is None or command not in handlers:
        return 2

    return 0 if await handlers[command](args) else 1


def main() -> None:
    """Entry point for console_scripts."""
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":  # pragma: no cover
    main()
