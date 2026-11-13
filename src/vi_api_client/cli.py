"""CLI for Viessmann Client."""

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import textwrap
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC
from functools import wraps
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import aiohttp

from vi_api_client import (
    FixtureViClient,
    OAuth,
    ViClient,
    ViCommandRejectedError,
    ViNotFoundError,
    ViValidationError,
)

from ._feature_diff import diff_features, feature_entries, format_feature_diff
from ._types import JsonValue
from .const import DEFAULT_EVENT_HISTORY_MAX_PAGES
from .credentials import CredentialDocument
from .models import (
    CommandPairWriteBinding,
    CommandParameter,
    Device,
    EventHistoryWindow,
    Feature,
    FeatureConstraints,
    FeatureDeprecation,
    Gateway,
    InstallationEvent,
    ScheduleConstraints,
    WriteBinding,
    parse_instant,
)
from .parsing import owning_api_feature_name
from .utils import format_feature, parse_cli_params

DEFAULT_REDIRECT_URI = "http://localhost:4200/"
# Tokens and the OAuth client configuration are stored together in this file.
DEFAULT_TOKEN_FILE = "tokens.json"  # noqa: S105 - a file name, not a secret

_MAIN_EPILOG = """\
examples:
  vi-client login --client-id <your-client-id>
  vi-client list-devices
  vi-client list-features --available --values
  vi-client set heating.circuits.0.heating.curve.slope 1.2
  vi-client list-features --fixture-device Vitocal250A

Run 'vi-client <command> --help' for the options of one command.
"""
# Default safety limit on pages fetched for one event history window.
# diff-features sources besides file paths.
_LIVE_SOURCE = "live"
_FIXTURE_SOURCE_PREFIX = "fixture:"

_LOGGER = logging.getLogger(__name__)


type _Command = Callable[[argparse.Namespace], Awaitable[bool]]


def _positive_int(text: str) -> int:
    """Parse a positive integer command line value for argparse.

    Raises:
        argparse.ArgumentTypeError: If the value is not a positive integer.
    """
    try:
        value = int(text)
    except ValueError:
        value = 0
    if value <= 0:
        raise argparse.ArgumentTypeError(f"'{text}' is not a positive integer")
    return value


def _reports_errors(action: str) -> Callable[[_Command], _Command]:
    """Report command failures as a False result instead of a traceback.

    Args:
        action: What the command does, used in the logged error message.
    """

    def decorate(command: _Command) -> _Command:
        @wraps(command)
        async def run(args: argparse.Namespace) -> bool:
            try:
                return await command(args)
            except ViValidationError as error:
                print(f"Validation failed: {error}")
            except ViNotFoundError as error:
                print(f"Not found: {error}")
            except Exception as error:
                _LOGGER.error("Error %s: %s", action, error)
            return False

        return run

    return decorate


def _print_diagnostic(args: argparse.Namespace, message: str) -> None:
    """Print setup information without contaminating requested JSON output."""
    print(message, file=sys.stderr if args.json else sys.stdout)


def _print_json(document: object) -> None:
    """Print one compact JSON document as the command's only standard output."""
    print(json.dumps(document))


@dataclass
class CLIContext:
    """Context for CLI commands."""

    session: aiohttp.ClientSession | None
    client: ViClient | FixtureViClient
    # Target IDs from arguments or auto-discovery; None when not resolved.
    installation_id: str | None
    gateway_serial: str | None
    device_id: str | None


def create_session(args: argparse.Namespace) -> aiohttp.ClientSession:
    """Create aiohttp session with optional insecure SSL.

    Args:
        args: Parsed command line arguments.

    Returns:
        An aiohttp ClientSession configured according to args.
    """
    if args.insecure:
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
    token_file = args.token_file

    async with create_session(args) as session:
        auth = OAuth(client_id, redirect_uri, token_file, websession=session)
        url = auth.get_authorization_url()

        print(f"Please visit the following URL to log in:\n\n{url}\n")
        print(f"After verifying, you will be redirected to {redirect_uri}?code=...")
        code = input("Paste the 'code' parameter from the URL here: ").strip()

        await auth.async_exchange_code_for_tokens(code)

    CredentialDocument(Path(token_file)).update(
        {"client_id": client_id, "redirect_uri": redirect_uri}
    )
    print(f"Successfully authenticated! Tokens and config saved to {token_file}")
    return True


def get_client_config(args: argparse.Namespace) -> tuple[str, str]:
    """Return the client ID and redirect URI from arguments, environment, or file.

    Raises:
        ValueError: If no client ID is configured anywhere.
    """
    config = CredentialDocument(Path(args.token_file)).read()

    configured_client_id = config.get("client_id")
    configured_redirect_uri = config.get("redirect_uri")
    client_id = args.client_id or os.getenv("VIESSMANN_CLIENT_ID")
    if not client_id and isinstance(configured_client_id, str):
        client_id = configured_client_id
    redirect_uri = args.redirect_uri or os.getenv("VIESSMANN_REDIRECT_URI")
    if not redirect_uri and isinstance(configured_redirect_uri, str):
        redirect_uri = configured_redirect_uri
    redirect_uri = redirect_uri or DEFAULT_REDIRECT_URI

    if not client_id:
        raise ValueError(
            "Client ID not found. Provide it via --client-id or the "
            "VIESSMANN_CLIENT_ID environment variable, or run 'login' first."
        )

    return client_id, redirect_uri


@asynccontextmanager
async def setup_client_context(
    args: argparse.Namespace, discover: bool = True
) -> AsyncGenerator[CLIContext]:
    """Yield a client and the target IDs, discovering missing IDs if requested.

    Fixture devices use fixed placeholder IDs. With ``discover``, a live
    client fills missing IDs from the account's gateways and devices.
    """
    if args.fixture_device:
        client = FixtureViClient(args.fixture_device)
        _print_diagnostic(args, f"Using Fixture Device: {args.fixture_device}")
        yield CLIContext(
            None,
            client,
            args.installation_id or "99999",
            args.gateway_serial or "MOCK_GATEWAY",
            args.device_id or "0",
        )
        return

    client_id, redirect_uri = get_client_config(args)

    async with create_session(args) as session:
        auth = OAuth(client_id, redirect_uri, args.token_file, session)
        client = ViClient(auth)
        installation_id = args.installation_id
        gateway_serial = args.gateway_serial
        device_id = args.device_id

        if discover and not (installation_id and gateway_serial and device_id):
            gateway = _select_gateway(
                await client.get_gateways(), installation_id, gateway_serial
            )
            installation_id = gateway.installation_id
            gateway_serial = gateway.serial
            if not device_id:
                device_id = _select_device_id(
                    await client.get_devices(installation_id, gateway_serial)
                )
                _print_diagnostic(
                    args,
                    f"Auto-selected Context: Inst={installation_id}, "
                    f"GW={gateway_serial}, Dev={device_id}",
                )

        yield CLIContext(session, client, installation_id, gateway_serial, device_id)


def _select_gateway(
    gateways: Sequence[Gateway],
    installation_id: str | None,
    gateway_serial: str | None,
) -> Gateway:
    """Return the requested gateway, or the first gateway when none is requested.

    Raises:
        ValueError: If no gateway matches or the requested gateway belongs to
            another installation.
    """
    if not gateways:
        raise ValueError("No gateways found.")

    if gateway_serial:
        gateway = next(
            (candidate for candidate in gateways if candidate.serial == gateway_serial),
            None,
        )
        if gateway is None:
            raise ValueError(f"Gateway '{gateway_serial}' not found.")
        if installation_id and gateway.installation_id != installation_id:
            raise ValueError(
                f"Gateway '{gateway_serial}' does not belong to installation "
                f"'{installation_id}'."
            )
        return gateway

    if installation_id:
        gateway = next(
            (
                candidate
                for candidate in gateways
                if candidate.installation_id == installation_id
            ),
            None,
        )
        if gateway is None:
            raise ValueError(f"No gateway found for installation '{installation_id}'.")
        return gateway

    return gateways[0]


def _select_device_id(devices: Sequence[Device]) -> str:
    """Return device "0", usually the heating system, or else the first device.

    Raises:
        ValueError: If the gateway has no devices.
    """
    if not devices:
        raise ValueError("No devices found.")
    return next((device.id for device in devices if device.id == "0"), devices[0].id)


@_reports_errors("listing devices")
async def cmd_list_devices(args: argparse.Namespace) -> bool:
    """List installations and devices.

    Fetches and prints all installations, gateways, and devices.

    Args:
        args: Parsed command line arguments.
    """
    # Lists every installation, gateway, and device, so no target is needed.
    async with setup_client_context(args, discover=False) as context:
        installations = await context.client.get_installations()
        print(f"Found {len(installations)} installations:")
        for installation in installations:
            print(
                f"- ID: {installation.id}, "
                f"Description: {installation.description}, "
                f"Alias: {installation.alias}"
            )

        gateways = await context.client.get_gateways()
        print(f"\nFound {len(gateways)} gateways:")
        for gateway in gateways:
            print(
                f"- Serial: {gateway.serial} "
                f"(Inst: {gateway.installation_id}), "
                f"Version: {gateway.version}, Status: {gateway.status}"
            )

            devices = await context.client.get_devices(
                gateway.installation_id, gateway.serial
            )
            print(f"Found {len(devices)} devices:")
            for device in devices:
                print(
                    f"- ID: {device.id}, Model: {device.model_id}, "
                    f"Type: {device.device_type}, "
                    f"Status: {device.status}"
                )

    return True


@_reports_errors("listing features")
async def cmd_list_features(args: argparse.Namespace) -> bool:
    """List all features for a device.

    Supports filtering and formatting options.

    Args:
        args: Parsed command line arguments including the enabled, deprecated,
            values, and json flags.
    """
    async with setup_client_context(args) as context:
        device = _transient_device(context)
        features = await context.client.get_features(
            device, only_available=args.available
        )
    is_deprecated_listing: bool = args.deprecated
    if is_deprecated_listing:
        features = [feature for feature in features if feature.deprecation is not None]

    if args.json and args.values:
        _print_json(
            [
                {
                    "name": feature.name,
                    "value": feature.value,
                    "unit": feature.unit,
                    "formatted": format_feature(feature),
                    "writable": feature.is_writable,
                    "deprecation": _deprecation_document(feature.deprecation),
                }
                for feature in features
            ]
        )
    elif args.json:
        _print_json([feature.name for feature in features])
    elif args.values:
        print(f"Found {len(features)} Features for device {device.id}:")
        for feature in features:
            formatted_value = format_feature(feature)
            if len(formatted_value) > 80:
                formatted_value = formatted_value[:77] + "..."
            writable_mark = "*" if feature.is_writable else " "
            suffix = _removal_suffix(feature) if is_deprecated_listing else ""
            print(f"{writable_mark} {feature.name:<75}: {formatted_value}{suffix}")
        print("(* = writable)")
    else:
        print(f"Found {len(features)} Features for device {device.id}:")
        for feature in features:
            suffix = _removal_suffix(feature) if is_deprecated_listing else ""
            print(f"- {feature.name}{suffix}")

    return True


def _removal_suffix(feature: Feature) -> str:
    """Return a listed feature's removal notice as a line suffix.

    The removal date comes first and the info text after it; a missing date
    or info is left out.
    """
    deprecation = feature.deprecation
    if deprecation is None:
        return ""
    notice = "removal"
    if deprecation.removal_date is not None:
        notice += f" {deprecation.removal_date.isoformat()}"
    if deprecation.info:
        notice += f": {deprecation.info}"
    return f" ({notice})"


@_reports_errors("fetching feature")
async def cmd_get_feature(args: argparse.Namespace) -> bool:
    """Get a specific feature.

    Fetches and displays every feature matching a feature name or an API
    feature name; an API feature name can match several features. In text
    mode, every command the matching features' API features report is listed
    with its parameter names, required markers, and constraints, read from
    one refreshed device snapshot.

    Args:
        args: Parsed command line arguments including feature_name and json flag.
    """
    feature_name: str = args.feature_name
    async with setup_client_context(args) as context:
        device = _transient_device(context)
        features = await context.client.get_features(
            device, only_available=False, feature_names=[feature_name]
        )
        if not features:
            print(f"Feature '{feature_name}' not found.")
            return False

        if args.json:
            documents = [
                {
                    "name": feature.name,
                    "value": feature.value,
                    "unit": feature.unit,
                    "constraints": (
                        asdict(feature.constraints) if feature.constraints else None
                    ),
                    "command": _command_document(feature.write_binding),
                    "deprecation": _deprecation_document(feature.deprecation),
                }
                for feature in features
            ]
            # One match keeps the single-object document; several matches
            # print one JSON array so the output stays one JSON document.
            _print_json(documents[0] if len(documents) == 1 else documents)
            return True

        # The command listing reads the snapshot's internal command list, so
        # the text mode refreshes the device once after the selection; the
        # feature selection above keeps the client's name matching.
        snapshot = await context.client.refresh_device(device, only_available=False)

    api_feature_sections: list[str] = []
    for feature in features:
        print(f"- {feature.name}: {format_feature(feature)}")
        if feature.deprecation is not None:
            print(f"  Deprecated: {_deprecation_text(feature.deprecation)}")
        binding = feature.write_binding
        if binding is not None:
            api_feature_name: str | None = binding.api_feature_name
            constraints = feature.constraints or FeatureConstraints()
            if isinstance(binding, CommandPairWriteBinding):
                print(f"  Writable via commands: {_pair_commands_text(binding)}")
            else:
                print(f"  Writable via command: {binding.command_name}")
                print(f"  Target param: {binding.parameter_name}")
            if constraints.min is not None:
                print(
                    f"  Constraints: min={constraints.min}, max={constraints.max}, "
                    f"step={constraints.step}"
                )
            if constraints.options:
                print(f"  Options: {constraints.options}")
        else:
            api_feature_name = owning_api_feature_name(snapshot, feature.name)
        if api_feature_name is not None and (
            api_feature_name not in api_feature_sections
        ):
            api_feature_sections.append(api_feature_name)

    for api_feature in api_feature_sections:
        _print_command_list(snapshot, api_feature)

    return True


def _deprecation_document(
    deprecation: FeatureDeprecation | None,
) -> dict[str, str | None] | None:
    """Return the JSON ``deprecation`` entry of a feature's removal notice."""
    if deprecation is None:
        return None
    removal_date = deprecation.removal_date
    return {
        "removal_date": removal_date.isoformat() if removal_date else None,
        "info": deprecation.info,
    }


def _deprecation_text(deprecation: FeatureDeprecation) -> str:
    """Return a removal notice as one line with its date and info text."""
    removal = (
        f"removal announced for {deprecation.removal_date.isoformat()}"
        if deprecation.removal_date
        else "removal announced without a date"
    )
    return f"{removal} ({deprecation.info})" if deprecation.info else removal


def _command_document(binding: WriteBinding | None) -> dict[str, str] | None:
    """Return the JSON ``command`` entry of a feature's write binding.

    A parameter command prints its name and parameter; an on/off command
    pair prints both command names.
    """
    if binding is None:
        return None
    if isinstance(binding, CommandPairWriteBinding):
        return {"on": binding.on_command_name, "off": binding.off_command_name}
    return {"name": binding.command_name, "parameter": binding.parameter_name}


def _pair_commands_text(binding: CommandPairWriteBinding) -> str:
    """Return both commands of an on/off pair for text output."""
    return f"{binding.on_command_name} (on), {binding.off_command_name} (off)"


def _print_command_list(device: Device, api_feature_name: str) -> None:
    """Print the commands of one API feature with their parameter rules.

    A ``*`` marks a required parameter.
    """
    command_names = device.get_command_names(api_feature_name)
    if not command_names:
        return
    print(f"  Commands of {api_feature_name}:")
    for command_name in command_names:
        parameters = device.get_command_parameters(api_feature_name, command_name)
        # Every listed name is a reported command, so it has a parameter list.
        assert parameters is not None  # noqa: S101
        parameter_labels = ", ".join(
            f"{parameter.name}{'*' if parameter.required is not False else ''}"
            for parameter in parameters
        )
        print(f"  - {command_name}: {parameter_labels or 'none'}")
        for parameter in parameters:
            _print_parameter_rules(parameter)


def _print_parameter_rules(parameter: CommandParameter) -> None:
    """Print a command parameter's reported type and constraint rules."""
    details: list[str] = []
    constraints = parameter.constraints
    if constraints is not None:
        if constraints.value_type is not None:
            details.append(constraints.value_type)
        details.extend(_constraint_rules_text(constraints))
        details.extend(_schedule_rules_text(constraints.schedule))
    if details:
        print(f"      {parameter.name}: {', '.join(details)}")


@_reports_errors("setting feature")
async def cmd_set(args: argparse.Namespace) -> bool:
    """Set a feature value (User Friendly).

    Sets a feature to a new value using the high-level set_feature API.

    Args:
        args: Parsed command line arguments including feature_name and value.
    """
    feature_name: str = args.feature_name
    raw_value: str = args.value
    async with setup_client_context(args) as context:
        target = await _fetch_writable_feature(context, feature_name)
        if target is None:
            return False
        device, feature, binding = target

        print(f"Setting '{feature.name}' to '{raw_value}'...")
        if isinstance(binding, CommandPairWriteBinding):
            print(f"  (Commands: {_pair_commands_text(binding)})")
        else:
            print(
                f"  (Command: {binding.command_name}, Param: {binding.parameter_name})"
            )

        try:
            value = _parse_set_value(raw_value, feature)
        except ValueError as error:
            print(f"Error: {error}")
            return False
        try:
            await context.client.set_feature(device, feature, value)
        except ViCommandRejectedError as error:
            _print_command_rejected(error)
            return False

    print("✅ Success!")
    return True


def _parse_set_value(raw_value: str, feature: Feature) -> JsonValue:
    """Convert a CLI value according to the target command parameter type.

    Args:
        raw_value: Value provided after the CLI ``set`` command.
        feature: Writable feature, named in validation errors.

    Returns:
        The value in the type expected by the target command.

    Raises:
        ValueError: If a numeric, boolean, or schedule command value is
            malformed.
    """
    constraints = feature.constraints or FeatureConstraints()
    value_type = constraints.value_type
    if value_type is None:
        value_type = _infer_feature_value_type(feature)

    if value_type == "Schedule":
        try:
            schedule: JsonValue = json.loads(raw_value)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"Value for '{feature.name}' must be a JSON schedule object."
            ) from error
        return schedule

    if value_type == "boolean":
        normalized_value = raw_value.casefold()
        if normalized_value == "true":
            return True
        if normalized_value == "false":
            return False
        raise ValueError(
            f"Value '{raw_value}' for '{feature.name}' must be true or false."
        )

    if value_type == "integer":
        try:
            return int(raw_value)
        except ValueError as error:
            raise ValueError(
                f"Value '{raw_value}' for '{feature.name}' must be an integer."
            ) from error

    if value_type == "number":
        try:
            value = float(raw_value)
        except ValueError as error:
            raise ValueError(
                f"Value '{raw_value}' for '{feature.name}' must be a number."
            ) from error
        if not math.isfinite(value):
            raise ValueError(
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
    constraints = feature.constraints
    if constraints and any(
        constraint is not None
        for constraint in (
            constraints.min,
            constraints.max,
            constraints.step,
        )
    ):
        return "number"
    return "string"


@_reports_errors("executing command")
async def cmd_exec(args: argparse.Namespace) -> bool:
    """Run a command of an API feature (Advanced).

    Runs any command the API feature reports, addressed by the API feature
    name, with the explicitly supplied parameters. For advanced users.

    Args:
        args: Parsed command line arguments including the API feature name,
            command name, and params.
    """
    api_feature_name: str = args.feature_name
    command_name: str = args.command_name

    try:
        parameters = parse_cli_params(args.params)
    except ValueError as error:
        print(f"Error parsing parameters: {error}")
        return False

    async with setup_client_context(args) as context:
        device = _transient_device(context)
        # Commands resolve on the device's command list, so the read is a
        # full refresh; unavailable API features stay addressable.
        device_snapshot = await context.client.refresh_device(
            device, only_available=False
        )

        print(f"Executing '{command_name}' on {api_feature_name}...")
        print(f"Using parameters: {parameters}")
        try:
            await context.client.run_command(
                device_snapshot, api_feature_name, command_name, parameters
            )
        except ViCommandRejectedError as error:
            _print_command_rejected(error)
            return False

    print("✅ Success!")
    return True


async def _fetch_writable_feature(
    context: CLIContext, name: str
) -> tuple[Device, Feature, WriteBinding] | None:
    """Fetch a writable feature together with its complete device snapshot.

    Prints the reason and returns None when the feature is missing or
    read-only. The snapshot is a full refresh so it carries the device's
    command list.
    """
    device = _transient_device(context)
    # The CLI resolves writability itself, so unavailable features are shown.
    device_snapshot = await context.client.refresh_device(device, only_available=False)
    feature = device_snapshot.get_feature(name)
    if feature is None:
        print(f"Error: Feature '{name}' not found.")
        return None
    binding = feature.write_binding
    if binding is None:
        api_feature_name = owning_api_feature_name(device_snapshot, feature.name)
        command_names = (
            device_snapshot.get_command_names(api_feature_name)
            if api_feature_name
            else ()
        )
        hint = (
            f" API feature '{api_feature_name}' reports the commands "
            f"{', '.join(command_names)}; run one with "
            f"'vi-client exec {api_feature_name} <command-name> [key=value ...]'."
            if command_names
            else ""
        )
        print(f"Error: Feature '{feature.name}' is read-only.{hint}")
        return None
    return device_snapshot, feature, binding


def _transient_device(context: CLIContext) -> Device:
    """Return a placeholder device carrying only the target IDs for API reads."""
    if not (context.installation_id and context.gateway_serial and context.device_id):
        raise ValueError("A device context is required for this command.")

    return Device(
        id=context.device_id,
        gateway_serial=context.gateway_serial,
        installation_id=context.installation_id,
        model_id="transient",
        device_type="unknown",
        status="online",
    )


def _print_command_rejected(error: ViCommandRejectedError) -> None:
    """Print the failure lines of a command the API rejected."""
    print("❌ Failed!")
    if error.message:
        print(f"Message: {error.message}")
    if error.reason:
        print(f"Reason: {error.reason}")


@_reports_errors("listing writable features")
async def cmd_list_writable(args: argparse.Namespace) -> bool:
    """List all writable features for a device.

    Displays the features a command can write.

    Args:
        args: Parsed command line arguments.
    """
    async with setup_client_context(args) as context:
        device = _transient_device(context)
        # The CLI resolves writability itself, so unavailable features are
        # shown; a full refresh keeps the snapshot complete with its
        # command list.
        device = await context.client.refresh_device(device, only_available=False)

    writable_features = [
        (feature, binding)
        for feature in device.features
        if (binding := feature.write_binding) is not None
    ]
    print(f"\nFound {len(writable_features)} writable features:\n")
    for feature, binding in writable_features:
        print(f"- {feature.name}")
        if isinstance(binding, CommandPairWriteBinding):
            print(f"    Commands: {_pair_commands_text(binding)}")
        else:
            print(f"    Param:   {binding.parameter_name} (via {binding.command_name})")
        _print_feature_constraints(feature.constraints or FeatureConstraints())
        print("")

    return True


def _print_feature_constraints(constraints: FeatureConstraints) -> None:
    """Print the numeric, option, and text constraints of a writable feature."""
    printed = _constraint_rules_text(constraints)
    if printed:
        print(f"    Constraints: {', '.join(printed)}")

    schedule_rules = _schedule_rules_text(constraints.schedule)
    if schedule_rules:
        print(f"    Schedule rules: {', '.join(schedule_rules)}")


def _constraint_rules_text(constraints: FeatureConstraints) -> list[str]:
    """Return one entry per reported numeric, option, and text rule."""
    printed: list[str] = []
    if constraints.min is not None:
        printed.append(f"min: {constraints.min}")
    if constraints.max is not None:
        printed.append(f"max: {constraints.max}")
    if constraints.step is not None:
        printed.append(f"step: {constraints.step}")
    if constraints.options:
        printed.append(f"options: {constraints.options}")
    if constraints.min_length is not None:
        printed.append(f"min_length: {constraints.min_length}")
    if constraints.max_length is not None:
        printed.append(f"max_length: {constraints.max_length}")
    if constraints.pattern is not None:
        printed.append(f"pattern: {constraints.pattern}")
    return printed


def _schedule_rules_text(schedule: ScheduleConstraints | None) -> list[str]:
    """Return one entry per reported schedule rule."""
    if schedule is None:
        return []
    return [
        f"{name}: {value}"
        for name, value in asdict(schedule).items()
        if value is not None
    ]


@_reports_errors("listing events")
async def cmd_list_events(args: argparse.Namespace) -> bool:
    """List the complete event history returned for a lookback window.

    Follows the continuation cursor across pages up to the configured page
    safety limit. Prints a readable summary for the requested rolling
    ``--days`` window, or one JSON document with the complete events and
    pagination metadata with ``--json``. A cursor remaining at the safety
    limit marks the result incomplete.

    Args:
        args: Parsed command line arguments including days, limit, max_pages,
            and the json flag.
    """
    days: int = args.days
    limit: int | None = args.limit
    max_pages: int = args.max_pages

    async with setup_client_context(args, discover=False) as context:
        installation_id = context.installation_id
        if installation_id is None:
            installations = await context.client.get_installations()
            if not installations:
                raise ValueError("No installations found.")
            installation_id = installations[0].id
            _print_diagnostic(args, f"Auto-selected installation: {installation_id}")

        window = await context.client.get_event_history_window(
            installation_id, days, limit=limit, max_pages=max_pages
        )

    if args.json:
        _print_json(
            {
                "installationId": installation_id,
                "events": [dict(event.fields) for event in window.events],
                "eventCount": len(window.events),
                "earliestEventTimestamp": window.earliest_timestamp,
                "latestEventTimestamp": window.latest_timestamp,
                "pagesFetched": window.pages_fetched,
                "paginationComplete": window.next_cursor is None,
                "nextCursor": window.next_cursor,
            }
        )
    else:
        _print_event_summary(window, installation_id, days, max_pages)

    return True


# Readable event lines wrap long details instead of truncating them.
_EVENT_LINE_WIDTH = 120
_EVENT_DETAIL_INDENT = "    "
_EVENT_CONTINUATION_INDENT = "      "


def _print_event_summary(
    window: EventHistoryWindow, installation_id: str, days: int, max_pages: int
) -> None:
    """Print a readable summary of a traversed event history window.

    A UTC date heading is printed whenever the date changes, so the
    returned event order is preserved exactly; every event line states
    its UTC time. Long details wrap onto indented continuation lines
    instead of being truncated, so complete command parameters and body
    values stay visible.
    """
    print(
        f"Found {len(window.events)} event(s) for installation "
        f"{installation_id} (last {days} days):"
    )
    gateway_serials = {
        event.gateway_serial for event in window.events if event.gateway_serial
    }
    show_gateway_label = len(gateway_serials) > 1
    previous_date_label: str | None = None
    for event in window.events:
        date_label = _event_date_label(event.event_timestamp)
        if date_label != previous_date_label:
            print()
            print(date_label)
            previous_date_label = date_label
        _print_event_lines(event, show_gateway_label)
    print()
    earliest_timestamp = window.earliest_timestamp
    latest_timestamp = window.latest_timestamp
    if earliest_timestamp is not None and latest_timestamp is not None:
        print(f"Earliest event: {earliest_timestamp}; latest event: {latest_timestamp}")
    if window.next_cursor is not None:
        print(
            f"Stopped at the safety limit of {max_pages} page(s); more events "
            f"may be available (next cursor: {window.next_cursor})"
        )
    else:
        print(
            f"Pagination completed after {window.pages_fetched} page(s); this "
            "does not confirm how far back the provider retained events"
        )


def _event_date_label(timestamp: str) -> str:
    """Return the UTC date group label for one event timestamp.

    A missing or unparsable timestamp groups under an explicit unknown
    label instead of an invented date.
    """
    instant = parse_instant(timestamp)
    if instant is None:
        return "Unknown date"
    return f"{instant.astimezone(UTC):%Y-%m-%d} (UTC)"


def _event_time_label(timestamp: str) -> str:
    """Return the explicit UTC time label for one event timestamp.

    A missing timestamp stays unknown and an unparsable one is shown as
    reported instead of an invented time.
    """
    if not timestamp:
        return "time unknown"
    instant = parse_instant(timestamp)
    if instant is None:
        return timestamp
    return f"{instant.astimezone(UTC):%H:%M:%S} UTC"


def _print_event_lines(event: InstallationEvent, show_gateway_label: bool) -> None:
    """Print one event header line and its wrapped detail lines."""
    header = f"- {_event_time_label(event.event_timestamp)} {event.event_type}"
    if show_gateway_label:
        serial = event.gateway_serial if event.gateway_serial else "unknown"
        header += f" gateway {serial}"
    print(header)
    wrapper = textwrap.TextWrapper(
        width=_EVENT_LINE_WIDTH,
        initial_indent=_EVENT_DETAIL_INDENT,
        subsequent_indent=_EVENT_CONTINUATION_INDENT,
        break_long_words=True,
        break_on_hyphens=False,
        # Wrapping must not drop whitespace inside values such as JSON
        # strings; spaces at wrap boundaries are part of the content.
        drop_whitespace=False,
    )
    for detail in _event_detail_lines(event):
        print("\n".join(wrapper.wrap(detail)))


def _event_detail_lines(event: InstallationEvent) -> list[str]:
    """Return the readable detail lines for one event body.

    Bodies of the known event types are rendered with the meaning the API
    already supplies; every other body, including unknown event types with
    feature-shaped fields, uses the complete JSON representation so no
    value is lost.

    Args:
        event: The event whose body is rendered.

    Returns:
        The detail lines describing the complete event body.
    """
    body = event.body
    if event.event_type == "gateway-online" and isinstance(body, dict):
        online = body.get("online")
        if isinstance(online, bool):
            return ["ONLINE" if online else "OFFLINE"]
    if event.event_type == "device-message-status" and isinstance(body, dict):
        status_lines = _device_message_status_lines(body)
        if status_lines is not None:
            return status_lines
    if event.event_type == "feature-changed" and isinstance(body, dict):
        feature_lines = _feature_change_lines(body)
        if feature_lines is not None:
            return feature_lines
    return [f"body: {json.dumps(body)}"]


def _device_message_status_lines(body: dict[str, JsonValue]) -> list[str] | None:
    """Return detail lines for one device-message-status body.

    The ACTIVE and ENDED labels describe the transition reported at the
    event timestamp, not the device's current state. An error description
    that merely repeats the code is omitted because it adds no
    information; the original API field stays available through the JSON
    output.

    Args:
        body: The event body with the provider's status fields.

    Returns:
        The detail lines, or None when the body carries no status data.
    """
    lines: list[str] = []
    code_text = _text_field(body, "errorCode")
    if code_text is not None:
        lines.append(f"code: {code_text}")
    active = body.get("active")
    if isinstance(active, bool):
        lines.append("ACTIVE" if active else "ENDED")
    identifiers: list[str] = []
    device_id = _text_field(body, "deviceId")
    if device_id is not None:
        identifiers.append(f"device: {device_id}")
    model_id = _text_field(body, "modelId")
    if model_id is not None:
        identifiers.append(f"model: {model_id}")
    if identifiers:
        lines.append(", ".join(identifiers))
    equipment_type = _text_field(body, "equipmentType")
    if equipment_type is not None:
        lines.append(f"equipment type: {equipment_type}")
    description = _text_field(body, "errorDescription")
    description_text = description.strip() if description is not None else ""
    code_comparison = code_text.casefold() if code_text is not None else None
    if description_text and description_text.casefold() != code_comparison:
        lines.append(f"description: {description_text}")
    return lines or None


def _text_field(body: dict[str, JsonValue], field_name: str) -> str | None:
    """Return a non-empty text event body field, or None otherwise.

    A missing, null, empty, or non-string field renders nothing rather
    than a fabricated or stringified value.
    """
    value = body.get(field_name)
    if isinstance(value, str) and value:
        return value
    return None


def _feature_change_lines(body: dict[str, JsonValue]) -> list[str] | None:
    """Return detail lines for one ``feature-changed`` event body.

    Command parameters stay visible even when the command name is absent.

    Args:
        body: The event body with the provider's feature-change fields.

    Returns:
        The detail lines, or None when the body is not a feature change.
    """
    feature_name = _text_field(body, "featureName")
    if feature_name is None:
        return None
    lines = [feature_name]
    command_name = _text_field(body, "commandName")
    if command_name is not None:
        lines.append(f"command: {command_name}")
    command_body = body.get("commandBody")
    if command_body is not None:
        lines.append(f"parameters: {json.dumps(command_body)}")
    return lines


@_reports_errors("exporting device fixture")
async def cmd_dump_device(args: argparse.Namespace) -> bool:
    """Print the selected device as an anonymized fixture document.

    The JSON document is the only standard output, so it can be redirected
    into a file; setup information goes to standard error.

    Args:
        args: Parsed command line arguments with the optional device target.
    """
    async with setup_client_context(args) as context:
        document = await _export_context_device(context)

    _print_json(document)
    return True


async def _export_context_device(context: CLIContext) -> dict[str, JsonValue]:
    """Return the anonymized fixture export of the context's target device.

    Raises:
        ValueError: If the gateway has no device with the target ID.
    """
    target = _transient_device(context)
    devices = await context.client.get_devices(
        target.installation_id, target.gateway_serial
    )
    device = next((device for device in devices if device.id == target.id), None)
    if device is None:
        raise ValueError(f"Device {target.id} not found.")
    return await context.client.export_device_fixture(device)


@_reports_errors("comparing device features")
async def cmd_diff_features(args: argparse.Namespace) -> bool:
    """Compare the API features of two fixtures, exports, or the live device.

    Values are not compared; see the CLI reference for the report sections.

    Args:
        args: Parsed command line arguments with the left and right sources.
    """
    left = await _load_feature_source(args, args.left)
    right = await _load_feature_source(args, args.right)
    for line in format_feature_diff(diff_features(left, right), args.left, args.right):
        print(line)
    return True


async def _load_feature_source(
    args: argparse.Namespace, source: str
) -> dict[str, Mapping[str, Any]]:
    """Return the raw API features of one diff-features source.

    ``live`` exports the selected device, ``fixture:<name>`` reads a bundled
    fixture, and any other source is a path to a fixture file or export.

    Raises:
        ValueError: If the fixture is unknown or the document has no features.
    """
    if source == _LIVE_SOURCE or source.startswith(_FIXTURE_SOURCE_PREFIX):
        source_args = args
        if source != _LIVE_SOURCE:
            source_args = argparse.Namespace(
                **{
                    **vars(args),
                    "fixture_device": source.removeprefix(_FIXTURE_SOURCE_PREFIX),
                }
            )
        async with setup_client_context(source_args) as context:
            document: object = await _export_context_device(context)
    else:
        document = json.loads(Path(source).read_text(encoding="utf-8"))
    return feature_entries(document)


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


def _installed_version() -> str:
    """Return the installed package version, or "unknown" without metadata."""
    try:
        return version("vi_api_client")
    except PackageNotFoundError:
        return "unknown"


def _shared_option_parsers() -> tuple[
    argparse.ArgumentParser, argparse.ArgumentParser, argparse.ArgumentParser
]:
    """Return the parent parsers for common, installation, and device options.

    The options are grouped so each command's help lists its own options
    first.
    """
    common_parser = argparse.ArgumentParser(add_help=False)
    auth_group = common_parser.add_argument_group("authentication")
    auth_group.add_argument("--client-id", help="OAuth client ID (optional if saved)")
    auth_group.add_argument("--redirect-uri", help="OAuth redirect URI")
    auth_group.add_argument(
        "--token-file",
        default=DEFAULT_TOKEN_FILE,
        help=f"Path to save and load tokens (default: {DEFAULT_TOKEN_FILE})",
    )
    auth_group.add_argument(
        "--insecure", action="store_true", help="Disable SSL verification"
    )
    general_group = common_parser.add_argument_group("general")
    general_group.add_argument(
        "--fixture-device",
        metavar="NAME",
        help="Use a bundled fixture device offline (see list-fixture-devices)",
    )
    general_group.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging for the CLI and the library",
    )

    # Parent parsers for the optional installation and device target
    target_title = "target (the first one found is used when omitted)"
    installation_parser = argparse.ArgumentParser(add_help=False)
    installation_parser.add_argument_group(target_title).add_argument(
        "--installation-id", metavar="ID", help="Installation ID"
    )
    device_parser = argparse.ArgumentParser(add_help=False)
    device_target_group = device_parser.add_argument_group(target_title)
    device_target_group.add_argument(
        "--installation-id", metavar="ID", help="Installation ID"
    )
    device_target_group.add_argument(
        "--gateway-serial", metavar="SERIAL", help="Gateway serial"
    )
    device_target_group.add_argument("--device-id", metavar="ID", help="Device ID")

    return common_parser, installation_parser, device_parser


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser with one handler per subcommand."""
    common_parser, installation_parser, device_parser = _shared_option_parsers()
    parser = argparse.ArgumentParser(
        prog="vi-client",
        description="Read and control Viessmann devices through the Viessmann API.",
        epilog=_MAIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_installed_version()}",
        help="Show the installed vi_api_client version and exit",
    )
    # Commands without a target or JSON option still read these attributes.
    parser.set_defaults(
        handler=None,
        verbose=False,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
        json=False,
    )
    subparsers = parser.add_subparsers(
        dest="command", title="commands", metavar="<command>"
    )

    def add_command(
        name: str,
        summary: str,
        examples: Sequence[str],
        parents: Sequence[argparse.ArgumentParser],
    ) -> argparse.ArgumentParser:
        """Add one subcommand whose help ends with usage examples."""
        return subparsers.add_parser(
            name,
            help=summary,
            description=summary,
            epilog="examples:\n" + "\n".join(f"  {example}" for example in examples),
            formatter_class=argparse.RawDescriptionHelpFormatter,
            parents=parents,
        )

    add_command(
        "login",
        "Sign in with the Viessmann account and save the tokens",
        ["vi-client login --client-id <your-client-id>"],
        [common_parser],
    ).set_defaults(handler=cmd_login)

    add_command(
        "list-devices",
        "List the installations, gateways, and devices of the account",
        [
            "vi-client list-devices",
            "vi-client list-devices --fixture-device Vitocal200A",
        ],
        [common_parser],
    ).set_defaults(handler=cmd_list_devices)

    parser_features = add_command(
        "list-features",
        "List the features of a device",
        [
            "vi-client list-features --available --values",
            "vi-client list-features --deprecated",
            "vi-client list-features --json --fixture-device Vitocal250A",
        ],
        [common_parser, device_parser],
    )
    parser_features.add_argument(
        "--available",
        "--enabled",
        dest="available",
        action="store_true",
        help="List only enabled and ready features (--enabled is an alias)",
    )
    parser_features.add_argument(
        "--deprecated",
        action="store_true",
        help="List only features with a removal notice, each with its notice",
    )
    parser_features.add_argument(
        "--values", action="store_true", help="Show feature values"
    )
    parser_features.add_argument(
        "--json", action="store_true", help="Print the features as JSON"
    )
    parser_features.set_defaults(handler=cmd_list_features)

    parser_feature = add_command(
        "get-feature",
        "Show one feature, or every feature of an API feature, with its commands",
        [
            "vi-client get-feature heating.circuits.0.heating.curve.slope",
            "vi-client get-feature heating.circuits.0.heating.curve --json",
        ],
        [common_parser, device_parser],
    )
    parser_feature.add_argument(
        "feature_name", help="Feature or API feature name, e.g. heating.circuits.0"
    )
    parser_feature.add_argument(
        "--json", action="store_true", help="Print the matching features as JSON"
    )
    parser_feature.set_defaults(handler=cmd_get_feature)

    parser_events = add_command(
        "list-events",
        "List the installation event history for a lookback window",
        [
            "vi-client list-events --days 7",
            "vi-client list-events --days 30 --limit 100 --json",
        ],
        [common_parser, installation_parser],
    )
    parser_events.add_argument(
        "--days",
        type=_positive_int,
        required=True,
        help="Rolling lookback window in days",
    )
    parser_events.add_argument(
        "--limit",
        type=_positive_int,
        help="Page limit (1-1000; provider default when omitted)",
    )
    parser_events.add_argument(
        "--max-pages",
        type=_positive_int,
        default=DEFAULT_EVENT_HISTORY_MAX_PAGES,
        help=(
            "Safety limit on pages fetched while following the cursor "
            f"(default: {DEFAULT_EVENT_HISTORY_MAX_PAGES})"
        ),
    )
    parser_events.add_argument(
        "--json",
        action="store_true",
        help="Output one JSON document with full events and pagination metadata",
    )
    parser_events.set_defaults(handler=cmd_list_events)

    add_command(
        "list-fixture-devices",
        "List the bundled fixture devices usable with --fixture-device",
        ["vi-client list-fixture-devices"],
        [common_parser],
    ).set_defaults(handler=cmd_list_fixture_devices)

    parser_diff = add_command(
        "diff-features",
        "Compare the features of two fixtures, exports, or the live device",
        [
            "vi-client diff-features fixture:Vitocal250A live",
            "vi-client diff-features fixture:Vitocal222S fixture:Vitocal250A",
        ],
        [common_parser, device_parser],
    )
    for side in ("left", "right"):
        parser_diff.add_argument(
            side, help="fixture:<name>, live, or a fixture or export JSON file"
        )
    parser_diff.set_defaults(handler=cmd_diff_features)

    add_command(
        "dump-device",
        "Print a device's features as an anonymized fixture (JSON)",
        ["vi-client dump-device > export.json"],
        [common_parser, device_parser],
    ).set_defaults(handler=cmd_dump_device, json=True)

    add_command(
        "list-writable",
        "List the writable features of a device",
        ["vi-client list-writable"],
        [common_parser, device_parser],
    ).set_defaults(handler=cmd_list_writable)

    parser_set = add_command(
        "set",
        "Set the value of a writable feature",
        [
            "vi-client set heating.circuits.0.heating.curve.slope 1.2",
            "vi-client set heating.dhw.temperature.main 50",
        ],
        [common_parser, device_parser],
    )
    parser_set.add_argument(
        "feature_name",
        help="Feature name, e.g. heating.circuits.0.heating.curve.slope",
    )
    parser_set.add_argument("value", help="Value to set")
    parser_set.set_defaults(handler=cmd_set)

    parser_exec = add_command(
        "exec",
        "Run a command of an API feature (advanced)",
        [
            "vi-client exec heating.circuits.0.heating.curve setCurve "
            "slope=1.2 shift=0",
            "vi-client exec heating.operating.programs.holiday unschedule",
        ],
        [common_parser, device_parser],
    )
    parser_exec.add_argument(
        "feature_name",
        help="API feature name, e.g. heating.circuits.0.heating.curve",
    )
    parser_exec.add_argument("command_name", help="Command name, e.g. setCurve")
    parser_exec.add_argument(
        "params", nargs="*", metavar="key=value", help="Command parameters"
    )
    parser_exec.set_defaults(handler=cmd_exec)

    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    """Main CLI entrypoint.

    Args:
        argv: The command line arguments without the program name. Defaults
            to ``sys.argv[1:]``.

    Returns:
        The process exit status.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
    )

    if args.handler is None:
        parser.print_help()
        return 0

    try:
        return await _dispatch_command(args)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        _LOGGER.error("Error executing command: %s", error)
        return 1


async def _dispatch_command(args: argparse.Namespace) -> int:
    """Run the parsed command's handler and map its result to an exit status."""
    handler: _Command = args.handler
    return 0 if await handler(args) else 1


def main() -> None:
    """Entry point for console_scripts."""
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":  # pragma: no cover
    main()
