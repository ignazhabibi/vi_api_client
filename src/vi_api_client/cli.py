"""CLI for Viessmann Client."""

import argparse
import asyncio
import json
import logging
import math
import os
import sys
import textwrap
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import NamedTuple, cast

import aiohttp

from vi_api_client import (
    FixtureViClient,
    OAuth,
    ViClient,
    ViNotFoundError,
    ViValidationError,
)

from ._types import JsonValue
from .credentials import CredentialDocument
from .models import (
    CommandResponse,
    Device,
    Feature,
    FeatureControl,
    InstallationEvent,
)
from .utils import format_feature, parse_cli_params

# Default file to store tokens and config
DEFAULT_REDIRECT_URI = "http://localhost:4200/"
TOKEN_FILE = "tokens.json"
# Default safety limit on pages fetched for one event history window.
DEFAULT_EVENT_HISTORY_MAX_PAGES = 50

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

    async with await create_session(args) as session:
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
    """Get client_id and redirect_uri from args or file."""
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
    fixture_device = args.fixture_device
    if fixture_device:
        client = FixtureViClient(fixture_device)
        inst_id = args.installation_id or "99999"
        gw_serial = args.gateway_serial or "MOCK_GATEWAY"
        dev_id = args.device_id or "0"
        _print_diagnostic(args, f"Using Fixture Device: {fixture_device}")
        yield CLIContext(None, client, inst_id, gw_serial, dev_id)
        return

    client_id, redirect_uri = get_client_config(args)

    async with await create_session(args) as session:
        auth = OAuth(client_id, redirect_uri, args.token_file, session)

        client = ViClient(auth)
        inst_id = args.installation_id
        gw_serial = args.gateway_serial
        dev_id = args.device_id

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

        yield CLIContext(session, client, inst_id, gw_serial, dev_id)


@_reports_errors("listing devices")
async def cmd_list_devices(args: argparse.Namespace) -> bool:
    """List installations and devices.

    Fetches and prints all installations, gateways, and devices.

    Args:
        args: Parsed command line arguments.
    """
    # Does not use full context discovery, just client
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

    return True


@_reports_errors("listing features")
async def cmd_list_features(args: argparse.Namespace) -> bool:
    """List all features for a device.

    Supports filtering and formatting options.

    Args:
        args: Parsed command line arguments including enabled, values, json flags.
    """
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
            _print_simple_feature_list(features, device.id)

    return True


def _print_simple_feature_list(features: Sequence[Feature], dev_id: str) -> None:
    """Print a simple list of feature names."""
    print(f"Found {len(features)} Features for device {dev_id}:")
    for feature in features:
        print(f"- {feature.name}")


@_reports_errors("fetching feature")
async def cmd_get_feature(args: argparse.Namespace) -> bool:
    """Get a specific feature.

    Fetches and displays every feature matching a feature name or an API
    feature name; an API feature name can match several features.

    Args:
        args: Parsed command line arguments including feature_name and raw flag.
    """
    feature_name: str = args.feature_name
    async with setup_client_context(args) as ctx:
        device = _transient_device(ctx)
        features = await ctx.client.get_features(device, feature_names=[feature_name])
        if not features:
            print(f"Feature '{feature_name}' not found.")
            return False

        if args.raw:
            documents = [
                {
                    "name": feature.name,
                    "value": feature.value,
                    "unit": feature.unit,
                    "control": str(feature.control) if feature.control else None,
                }
                for feature in features
            ]
            # One match keeps the single-object document; several matches
            # print one JSON array so the output stays one JSON document.
            print(
                json.dumps(
                    documents[0] if len(documents) == 1 else documents,
                    indent=2,
                    default=str,
                )
            )
            return True

        for feature in features:
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

    return True


@_reports_errors("setting feature")
async def cmd_set(args: argparse.Namespace) -> bool:
    """Set a feature value (User Friendly).

    Sets a feature to a new value using the high-level set_feature API.

    Args:
        args: Parsed command line arguments including feature_name and value.
    """
    feature_name: str = args.feature_name
    raw_value: str = args.value
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

        return _print_command_result(result)


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


@_reports_errors("executing command")
async def cmd_exec(args: argparse.Namespace) -> bool:
    """Execute a command (Advanced).

    Executes a raw command with parameters. For advanced users.

    Args:
        args: Parsed command line arguments including feature_name,
            command_name, and params.
    """
    feature_name: str = args.feature_name
    command_name: str = args.command_name
    params: list[str] = args.params

    # 1. Parse params
    try:
        params_dict = parse_cli_params(params) if params else {}
    except ValueError as e:
        print(f"Error parsing parameters: {e}")
        return False

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


@_reports_errors("listing writable features")
async def cmd_list_writable(args: argparse.Namespace) -> bool:
    """List all writable features for a device.

    Displays features that have a control block (can be modified).

    Args:
        args: Parsed command line arguments.
    """
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

    async with setup_client_context(args, discover=False) as ctx:
        installation_id = ctx.inst_id
        if installation_id is None:
            installations = await ctx.client.get_installations()
            if not installations:
                raise ValueError("No installations found.")
            installation_id = installations[0].id
            _print_diagnostic(args, f"Auto-selected installation: {installation_id}")

        window = await _collect_event_history(
            ctx.client, installation_id, days, limit, max_pages
        )

        if args.json:
            print(
                json.dumps(
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
            )
        else:
            _print_event_summary(window, installation_id, days, max_pages)

    return True


class EventHistoryWindow(NamedTuple):
    """The events and pagination state of one bounded traversal."""

    events: list[InstallationEvent]
    next_cursor: str | None
    pages_fetched: int

    @property
    def earliest_timestamp(self) -> str | None:
        """Return the earliest event timestamp, or None without events.

        Timestamps are compared as points in time, so differing ISO-8601
        offsets or precision still order correctly.
        """
        return self._extreme_timestamps()[0]

    @property
    def latest_timestamp(self) -> str | None:
        """Return the latest event timestamp, or None without events.

        Timestamps are compared as points in time, so differing ISO-8601
        offsets or precision still order correctly.
        """
        return self._extreme_timestamps()[1]

    def _extreme_timestamps(self) -> tuple[str | None, str | None]:
        """Return the earliest and latest event timestamps as time points.

        An unparsable provider timestamp falls back to a lexical comparison
        for the whole window rather than mixing the two orderings.
        """
        timestamps = [event.event_timestamp for event in self.events]
        if not timestamps:
            return None, None
        parsed = [_parse_instant(timestamp) for timestamp in timestamps]
        if all(instant is not None for instant in parsed):
            instants = cast("list[datetime]", parsed)
            earliest = min(zip(instants, timestamps, strict=True))[1]
            latest = max(zip(instants, timestamps, strict=True))[1]
            return earliest, latest
        return min(timestamps), max(timestamps)


def _parse_instant(timestamp: str) -> datetime | None:
    """Return one event timestamp as a point in time, or None when unparsable.

    Naive timestamps are read as UTC so they compare with aware ones.
    """
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


async def _collect_event_history(
    client: ViClient | FixtureViClient,
    installation_id: str,
    days: int,
    limit: int | None,
    max_pages: int,
) -> EventHistoryWindow:
    """Follow the continuation cursor across event history pages.

    The first request uses the rolling lookback window; every subsequent
    request passes only the opaque continuation cursor so the lookback is
    never restarted.

    Args:
        client: The client serving the event history reads.
        installation_id: ID of the installation to read.
        days: Rolling lookback window in days for the first request.
        limit: Optional per-page size applied to every request.
        max_pages: Safety limit on the number of pages fetched.

    Returns:
        The traversal result. A remaining cursor of None means the provider
        reported no further page; otherwise the safety limit stopped the
        traversal and the result is incomplete.
    """
    events: list[InstallationEvent] = []
    next_cursor: str | None = None
    pages_fetched = 0
    while pages_fetched < max_pages:
        if next_cursor is None:
            page = await client.get_event_history(
                installation_id, days=days, limit=limit
            )
        else:
            page = await client.get_event_history(
                installation_id, cursor=next_cursor, limit=limit
            )
        pages_fetched += 1
        events.extend(page.events)
        next_cursor = page.next_cursor
        if next_cursor is None:
            return EventHistoryWindow(events, None, pages_fetched)
    return EventHistoryWindow(events, next_cursor, pages_fetched)


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
    instant = _parse_instant(timestamp)
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
    instant = _parse_instant(timestamp)
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


def build_parser() -> argparse.ArgumentParser:
    """Build the command line parser with one handler per subcommand."""
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

    # Parent parsers for the optional installation and device target
    installation_parser = argparse.ArgumentParser(add_help=False)
    installation_parser.add_argument(
        "--installation-id", help="Installation ID (optional)"
    )
    device_parser = argparse.ArgumentParser(
        add_help=False, parents=[installation_parser]
    )
    device_parser.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    device_parser.add_argument("--device-id", help="Device ID (optional)")

    parser = argparse.ArgumentParser(description="Viessmann API CLI")
    # Commands without a target or JSON option still read these attributes.
    parser.set_defaults(
        handler=None,
        verbose=False,
        installation_id=None,
        gateway_serial=None,
        device_id=None,
        json=False,
    )
    subparsers = parser.add_subparsers(dest="command")

    # Login
    subparsers.add_parser(
        "login", help="Authenticate with Viessmann", parents=[common_parser]
    ).set_defaults(handler=cmd_login)

    # List Devices
    subparsers.add_parser(
        "list-devices", help="List installations and devices", parents=[common_parser]
    ).set_defaults(handler=cmd_list_devices)

    # List Features
    parser_features = subparsers.add_parser(
        "list-features",
        help="List all features for a device",
        parents=[common_parser, device_parser],
    )
    parser_features.add_argument(
        "--enabled", action="store_true", help="List only enabled features"
    )
    parser_features.add_argument(
        "--values", action="store_true", help="Show feature values"
    )
    parser_features.add_argument(
        "--json", action="store_true", help="Output JSON (for lists)"
    )
    parser_features.set_defaults(handler=cmd_list_features)

    # Get Feature
    parser_feature = subparsers.add_parser(
        "get-feature",
        help="Get a specific feature",
        parents=[common_parser, device_parser],
    )
    parser_feature.add_argument(
        "feature_name", help="Feature Name (e.g. heating.circuits.0)"
    )
    parser_feature.add_argument(
        "--raw", action="store_true", help="Show raw JSON response"
    )
    parser_feature.set_defaults(handler=cmd_get_feature)

    # List Installation Events
    parser_events = subparsers.add_parser(
        "list-events",
        help="List one page of the installation event history",
        parents=[common_parser, installation_parser],
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

    # List available fixture devices
    subparsers.add_parser(
        "list-fixture-devices",
        help="List available fixture devices",
        parents=[common_parser],
    ).set_defaults(handler=cmd_list_fixture_devices)

    # List Writable Features
    subparsers.add_parser(
        "list-writable",
        help="List all writable features (commands)",
        parents=[common_parser, device_parser],
    ).set_defaults(handler=cmd_list_writable)

    # Set Value
    parser_set = subparsers.add_parser(
        "set",
        help="Set a feature value",
        parents=[common_parser, device_parser],
    )
    parser_set.add_argument(
        "feature_name",
        help="Feature Name (e.g. heating.circuits.0.heating.curve.slope)",
    )
    parser_set.add_argument("value", help="Value to set")
    parser_set.set_defaults(handler=cmd_set)

    # Exec Command (Advanced)
    parser_exec = subparsers.add_parser(
        "exec",
        help="Execute a raw command (Advanced)",
        parents=[common_parser, device_parser],
    )
    parser_exec.add_argument("feature_name", help="Feature Name")
    parser_exec.add_argument("command_name", help="Command Name")
    parser_exec.add_argument("params", nargs="*", help="Parameters (key=value)")
    parser_exec.set_defaults(handler=cmd_exec)

    return parser


async def async_main() -> int:
    """Main CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args()

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
    except Exception as e:
        _LOGGER.error("Error executing command: %s", e)
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
