"""Live and shared client workflows for the Viessmann API."""

import difflib
import logging
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import unquote, urlsplit

from ._adapter import CommandAdapter, DiscoveryAdapter, LiveAdapter
from ._types import FeatureValue, JsonValue
from .auth import AbstractAuth
from .const import DEFAULT_EVENT_HISTORY_MAX_PAGES, EVENT_HISTORY_MAX_LIMIT
from .exceptions import (
    ViCommandRejectedError,
    ViDeviceCommunicationError,
    ViError,
    ViResponseError,
)
from .models import (
    Device,
    DeviceCommand,
    EventHistoryPage,
    EventHistoryWindow,
    Feature,
    FeatureConstraints,
    Gateway,
    GatewayDeviceRefreshResult,
    Installation,
    InstallationEvent,
    OnOffWriteCommand,
    ParameterWriteCommand,
    WriteCommand,
    command_map_of,
    write_command_of,
)
from .parsing import (
    owning_api_feature_name,
    parse_api_features,
    parse_command_outcome,
    parse_device,
    parse_event_history_page,
    parse_gateway,
    parse_installation,
    validate_feature_entry,
)
from .privacy import redact_sensitive
from .validation import (
    reject_non_json_parameters,
    validate_json_value,
    validate_value_constraints,
)

_LOGGER = logging.getLogger(__name__)

# Error types that affect one device, so a per-device refresh records them for
# that device instead of aborting the whole gateway refresh.
_DEVICE_SPECIFIC_ERROR_TYPES = frozenset(
    {"DEVICE_COMMUNICATION_ERROR", "DEVICE_NOT_FOUND", "PACKAGE_NOT_PAID_FOR"}
)


class ViClient:
    """Read and control Viessmann devices through the Climate Solutions API.

    Every read returns new, immutable snapshots, and a successful command
    returns a locally updated device snapshot; nothing is changed in place.
    The client keeps no cache and adds no retries or rate limiting, so the
    caller owns polling cadence and retry policy (ADR 0002). Requests go
    through ``LiveAdapter``; ``FixtureViClient`` applies the same rules to
    bundled offline data.
    """

    def __init__(self, auth: AbstractAuth) -> None:
        """Initialize the client.

        Args:
            auth: Authentication handler providing the access token.
        """
        live_adapter = LiveAdapter(auth)
        self._discovery_adapter: DiscoveryAdapter = live_adapter
        self._command_adapter: CommandAdapter = live_adapter

    async def get_installations(self) -> list[Installation]:
        """Return the installations the account can access.

        Returns:
            List of Installation objects available to the user.

        Raises:
            ViResponseError: If a successful response violates the API contract.
        """
        _LOGGER.debug("Fetching installations")
        response = await self._discovery_adapter.get_installations()
        installations = [
            parse_installation(installation_data)
            for installation_data in self._response_items(
                response, resource="Installation"
            )
        ]
        _LOGGER.debug("Found %s installations", len(installations))
        return installations

    async def get_gateways(self) -> list[Gateway]:
        """Return the gateways of all installations.

        Returns:
            List of Gateway objects found (across all installations).

        Raises:
            ViResponseError: If a successful response violates the API contract.
        """
        _LOGGER.debug("Fetching gateways")
        response = await self._discovery_adapter.get_gateways()
        gateways = [
            parse_gateway(gateway_data)
            for gateway_data in self._response_items(response, resource="Gateway")
        ]
        _LOGGER.debug("Found %s gateways", len(gateways))
        return gateways

    async def get_devices(
        self, installation_id: str, gateway_serial: str
    ) -> list[Device]:
        """Return the devices behind one gateway.

        The devices carry no features; load them with `refresh_device` or
        `get_full_installation_status`.

        Args:
            installation_id: ID of the installation.
            gateway_serial: Serial number of the gateway.

        Returns:
            Device snapshots without features.

        Raises:
            ViResponseError: If a successful response violates the API contract.
        """
        response = await self._discovery_adapter.get_devices(
            installation_id, gateway_serial
        )
        devices = [
            parse_device(device_data, gateway_serial, installation_id)
            for device_data in self._response_items(response, resource="Device")
        ]

        return devices

    async def get_features(
        self,
        device: Device,
        only_available: bool = True,
        feature_names: list[str] | None = None,
    ) -> list[Feature]:
        """Return a device's features.

        Names are matched locally after fetching the device's features, so live
        and fixture-backed clients select the same features. The API's
        server-side name filter is not used: it selects API features, while
        callers mostly address the flat features parsed from them.

        Because ``only_available`` defaults to ``True``, a targeted read with
        ``feature_names`` returns no disabled or not-ready feature unless the
        caller passes ``only_available=False``.

        Args:
            device: The device to fetch features for.
            only_available: If True, only return enabled and ready features.
            feature_names: Optional feature names or API feature names to
                return. An API feature name selects every feature parsed from
                that API feature. Unknown names select nothing.

        Returns:
            List of Feature objects (flattened).

        Raises:
            ViResponseError: If the API response is malformed or the returned
                features contain duplicate names.
        """
        api_features = await self._read_api_features(device, only_available)
        features, _ = parse_api_features(
            api_features, only_available=only_available, feature_names=feature_names
        )
        self._reject_duplicate_feature_names(features)
        if only_available:
            features = [feature for feature in features if feature.is_available]

        _LOGGER.debug(
            "Fetched %s API features -> %s features", len(api_features), len(features)
        )
        return features

    async def get_full_installation_status(
        self, installation_id: str, only_available: bool = True
    ) -> list[Device]:
        """Return all devices of an installation with their features.

        Args:
            installation_id: ID of the installation to scan.
            only_available: Whether to skip disabled and not-ready features
                (default: True).

        Returns:
            List of Devices with their `features` list populated.

        Raises:
            ViResponseError: If a successful response violates the API contract.
        """
        gateways = await self.get_gateways()
        all_devices: list[Device] = []

        for gateway in gateways:
            if gateway.installation_id != installation_id:
                continue

            devices = await self.get_devices(installation_id, gateway.serial)
            for device in devices:
                all_devices.append(
                    await self.refresh_device(device, only_available=only_available)
                )

        return all_devices

    async def get_event_history(
        self,
        installation_id: str,
        *,
        days: int | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> EventHistoryPage:
        """Fetch one page of an installation's event history.

        Args:
            installation_id: ID of the installation.
            days: Rolling lookback window in days. Required unless ``cursor``
                is supplied; mutually exclusive with ``cursor``.
            cursor: Opaque continuation cursor reported by a previous page.
            limit: Optional page size between 1 and the documented maximum.
                When omitted, the provider default applies.

        Returns:
            One event history page with its events and the next cursor when
            the provider reported one.

        Raises:
            ValueError: If both or neither of ``days`` and ``cursor`` are
                supplied, the lookback is not positive, or the limit is
                outside the documented range.
            ViResponseError: If the successful response violates the API
                contract.
        """
        if not installation_id:
            raise ValueError("Installation ID must be a non-empty string")
        if (days is None) == (cursor is None):
            raise ValueError("Provide exactly one of 'days' or 'cursor'")
        if days is not None and days <= 0:
            raise ValueError("'days' must be a positive lookback window")
        if cursor == "":
            raise ValueError("'cursor' must be a non-empty string")
        if limit is not None and not 1 <= limit <= EVENT_HISTORY_MAX_LIMIT:
            raise ValueError(f"'limit' must be between 1 and {EVENT_HISTORY_MAX_LIMIT}")

        params: dict[str, int | str] = {}
        # Exactly one of the two window arguments is set at this point.
        if cursor is not None:
            params["cursor"] = cursor
        if days is not None:
            params["lastNDays"] = days
        if limit is not None:
            params["limit"] = limit

        _LOGGER.debug(
            "Fetching event history page for installation %s", installation_id
        )
        response = await self._discovery_adapter.get_event_history(
            installation_id, params
        )
        return parse_event_history_page(
            self._response_object(response, resource="Event history")
        )

    async def get_event_history_window(
        self,
        installation_id: str,
        days: int,
        *,
        limit: int | None = None,
        max_pages: int = DEFAULT_EVENT_HISTORY_MAX_PAGES,
    ) -> EventHistoryWindow:
        """Collect an installation's event history across one lookback window.

        The first request uses the rolling lookback; every following request
        passes only the continuation cursor the previous page reported, so
        the lookback is never restarted. Pages are requested one after the
        other, and the traversal stops at the first page without a cursor or
        at the page safety limit.

        Args:
            installation_id: ID of the installation.
            days: Rolling lookback window in days.
            limit: Optional page size between 1 and the documented maximum,
                applied to every request. When omitted, the provider default
                applies.
            max_pages: Safety limit on the number of pages fetched.

        Returns:
            The event history window with every collected event and the
            number of pages fetched. Its ``next_cursor`` is ``None`` when the
            provider reported no further page; otherwise the safety limit
            stopped the traversal and the window is incomplete.

        Raises:
            ValueError: If the installation ID is empty, the lookback or the
                page safety limit is not positive, or the limit is outside
                the documented range.
            ViResponseError: If a successful response violates the API
                contract.
        """
        if not installation_id:
            raise ValueError("Installation ID must be a non-empty string")
        if days <= 0:
            raise ValueError("'days' must be a positive lookback window")
        if limit is not None and not 1 <= limit <= EVENT_HISTORY_MAX_LIMIT:
            raise ValueError(f"'limit' must be between 1 and {EVENT_HISTORY_MAX_LIMIT}")
        if max_pages <= 0:
            raise ValueError("'max_pages' must be a positive page safety limit")

        events: list[InstallationEvent] = []
        next_cursor: str | None = None
        pages_fetched = 0
        while pages_fetched < max_pages:
            if next_cursor is None:
                page = await self.get_event_history(
                    installation_id, days=days, limit=limit
                )
            else:
                page = await self.get_event_history(
                    installation_id, cursor=next_cursor, limit=limit
                )
            pages_fetched += 1
            events.extend(page.events)
            next_cursor = page.next_cursor
            if next_cursor is None:
                break
        return EventHistoryWindow(events, next_cursor, pages_fetched)

    async def refresh_device(
        self, device: Device, only_available: bool = True
    ) -> Device:
        """Return a refreshed device snapshot from an API feature read.

        The snapshot carries the device's reported commands per API feature,
        needed by ``Device.get_command_names``; when ``only_available`` is
        set, it carries the commands of enabled and ready API features.

        Args:
            device: The device object to refresh.
            only_available: Whether to fetch only available features (enabled
                and ready).

        Returns:
            A refreshed device snapshot with features from the API response.

        Raises:
            ViResponseError: If the API response is malformed or contains
                duplicate feature names.
        """
        features, commands = await self._parse_device_features(device, only_available)
        return replace(device, features=features, _commands_by_api_feature=commands)

    async def _parse_device_features(
        self, device: Device, only_available: bool
    ) -> tuple[list[Feature], dict[str, tuple[DeviceCommand, ...]]]:
        """Return one feature read's flat features and reported commands.

        Raises:
            ViResponseError: If the API response is malformed or the returned
                features contain duplicate names.
        """
        api_features = await self._read_api_features(device, only_available)
        features, commands = parse_api_features(
            api_features, only_available=only_available
        )
        self._reject_duplicate_feature_names(features)
        if only_available:
            features = [feature for feature in features if feature.is_available]
        return features, commands

    async def _read_api_features(
        self, device: Device, only_available: bool
    ) -> list[dict[str, Any]]:
        """Return one device feature read's raw API feature entries.

        Raises:
            ViResponseError: If the response is malformed.
        """
        payload = {
            "skipDisabled": only_available,
            "skipNotReady": only_available,
        }
        _LOGGER.debug(
            "Fetching features for device %s (available=%s)",
            device.id,
            only_available,
        )
        response = await self._discovery_adapter.get_features(device, payload)
        return self._response_items(response, resource="Feature")

    async def export_device_fixture(self, device: Device) -> dict[str, JsonValue]:
        """Return a device's raw API features as an anonymized fixture document.

        The features are always read fresh from the API, including disabled
        and not-ready features, and kept in their raw API shape. The device
        only identifies what to read: features already on ``device`` are not
        used. The document is redacted with `redact_sensitive`, so it carries no
        installation IDs, serials, or coordinates and can be shared, for
        example as a new fixture.

        Args:
            device: The device to export.

        Returns:
            ``{"device": {...}, "data": [...]}``: the device's ``modelId``,
            ``deviceType``, ``roles``, and the UTC ``capturedAt`` date, and
            the redacted raw API features.

        Raises:
            ViResponseError: If the API response is malformed.
        """
        response = await self._discovery_adapter.get_features(
            device, {"skipDisabled": False, "skipNotReady": False}
        )
        api_features = self._response_items(response, resource="Feature")
        document: dict[str, JsonValue] = {
            "device": {
                "modelId": device.model_id,
                "deviceType": device.device_type,
                "roles": list(device.roles),
                "capturedAt": datetime.now(UTC).date().isoformat(),
            },
            "data": validate_json_value(api_features, path="features"),
        }
        # Redaction keeps the document's shape, so the result is still an object.
        return cast("dict[str, JsonValue]", redact_sensitive(document))

    async def refresh_gateway_devices(
        self, devices: list[Device]
    ) -> GatewayDeviceRefreshResult:
        """Refresh available features for devices on one gateway.

        Args:
            devices: Existing devices belonging to one installation and gateway.

        Returns:
            The successfully refreshed devices and any device-specific failures.

        Raises:
            ValueError: If devices span multiple scopes or contain duplicate IDs.
            ViResponseError: If a successful response violates the API contract.
            ViError: If a global API or connection failure aborts the refresh.
        """
        if not devices:
            return GatewayDeviceRefreshResult([], {})

        self._validate_gateway_devices(devices)

        payload = {
            "includeDevicesFeatures": True,
            "skipDisabled": True,
            "skipNotReady": True,
        }
        # All devices share one installation and gateway (validated above).
        installation_id = devices[0].installation_id
        gateway_serial = devices[0].gateway_serial
        try:
            response = await self._discovery_adapter.get_gateway_features(
                installation_id, gateway_serial, payload
            )
        except ViDeviceCommunicationError:
            # The API reports unreachable gateways and device outages as
            # DEVICE_COMMUNICATION_ERROR; the per-device refreshes below
            # find the devices that still answer.
            _LOGGER.debug(
                "Gateway-wide feature fetch failed with a device "
                "communication error; falling back to individual device "
                "refreshes"
            )
            return await self._refresh_devices_individually(devices)

        requested_device_ids = {device.id for device in devices}
        api_features_by_device_id = self._group_api_features_by_device(
            response, requested_device_ids
        )

        missing_devices: list[Device] = []
        refreshed_devices_by_id: dict[str, Device] = {}
        for device in devices:
            api_features = api_features_by_device_id.get(device.id)
            if api_features is None:
                missing_devices.append(device)
                continue
            features, commands = parse_api_features(api_features, only_available=True)
            self._reject_duplicate_feature_names(features)
            # The skip hints are only hints: filter locally like refresh_device.
            features = [feature for feature in features if feature.is_available]
            refreshed_devices_by_id[device.id] = replace(
                device, features=features, _commands_by_api_feature=commands
            )

        if missing_devices:
            _LOGGER.debug(
                "Gateway feature response omitted %s requested device(s); "
                "refreshing them individually",
                len(missing_devices),
            )
        fallback_result = await self._refresh_devices_individually(missing_devices)
        refreshed_devices_by_id.update(
            (device.id, device) for device in fallback_result.refreshed_devices
        )

        # Keep the caller's device order across both refresh paths.
        refreshed_devices = [
            refreshed_devices_by_id[device.id]
            for device in devices
            if device.id in refreshed_devices_by_id
        ]

        return GatewayDeviceRefreshResult(
            refreshed_devices, fallback_result.errors_by_device_id
        )

    async def set_feature(
        self, device: Device, feature: Feature | str, target_value: FeatureValue
    ) -> Device:
        """Set a current device feature and return the command-updated snapshot.

        Resolves ``feature`` against ``device`` before validating its current
        command availability, constraints, and required sibling parameters.
        The feature argument is only a name reference: a supplied ``Feature``
        is resolved by its name in the current snapshot, and its stale value
        or constraints do not participate.

        Args:
            device: The device allowing context lookup for dependencies.
            feature: A feature or feature name identifying the current device
                feature to write.
            target_value: The JSON value to write.

        Returns:
            The command-updated device snapshot with the feature value set
            locally, without an API read-back. A rejected command raises, so
            the caller keeps its previous snapshot.

        Raises:
            ValueError: If the named feature is absent or unavailable, a
                required sibling is absent, unavailable, or has no value, the
                target value violates its constraints, or the target value is
                not a JSON value.
            ViCommandRejectedError: If the API rejects the command.
            ViResponseError: If the command URI is outside the Vi API or the
                successful command response violates the API contract.
        """
        feature_name = feature if isinstance(feature, str) else feature.name
        current_feature = device.get_feature(feature_name)
        if current_feature is None:
            raise ValueError(f"Feature '{feature_name}' is not present on the device.")

        binding = self._require_command_binding(device, current_feature)
        constraints = self._require_constraints(current_feature)
        if isinstance(binding, OnOffWriteCommand):
            # The boolean type check is the pair's whole validation; which
            # direction the device allows right now is the API's decision.
            validate_value_constraints(
                constraints,
                f"{binding.on_command_name}/{binding.off_command_name}",
                target_value,
            )
            is_on = target_value is True
            command_name = (
                binding.on_command_name if is_on else binding.off_command_name
            )
            uri = binding.on_uri if is_on else binding.off_uri
            # A pair command is sent without parameters; an optional
            # parameter of the on command is deliberately left out.
            payload: dict[str, JsonValue] = {}
        else:
            command_name, uri = binding.command_name, binding.uri
            payload = self._resolve_command_payload(device, binding, target_value)
            reject_non_json_parameters(payload)
            validate_value_constraints(constraints, command_name, target_value)
        _LOGGER.debug(
            "Setting %s to %s via %s", current_feature.name, target_value, command_name
        )

        await self._send_command(uri, payload)

        # Preserve all other feature values from the input snapshot.
        updated_feature = replace(current_feature, value=target_value)
        updated_features = [
            updated_feature
            if existing_feature.name == current_feature.name
            else existing_feature
            for existing_feature in device.features
        ]
        return replace(device, features=updated_features)

    @staticmethod
    def _require_constraints(feature: Feature) -> FeatureConstraints:
        """Return the constraint rules a writable feature always carries.

        A writable feature is created only by the parser, which attaches
        constraints with the write command; the default keeps the check total.
        """
        return feature.constraints or FeatureConstraints()

    async def run_command(
        self,
        device: Device,
        api_feature_name: str,
        command_name: str,
        parameters: dict[str, JsonValue] | None = None,
    ) -> None:
        """Run one reported command of a device's API feature.

        The command is resolved from the snapshot's internal command list, so
        the device must come from `refresh_device`, `refresh_gateway_devices`,
        or `get_full_installation_status`. Reported executability and
        deprecation are not checked: a command reported as non-executable is
        sent like any other, and the API decides. No snapshot is changed; a
        successful run raises nothing.

        Args:
            device: The device snapshot carrying the API feature's commands.
            api_feature_name: The API feature name, such as
                ``heating.circuits.0.heating.curve``.
            command_name: The command name the API feature reports.
            parameters: The command parameters to send. Every required
                parameter must be supplied; every supplied value is checked
                against the parameter's reported type and constraints with
                the same rules `set_feature` applies to its value.

        Raises:
            ValueError: If the snapshot carries no commands, the API feature
                or command is unknown, a parameter name is not reported, a
                required parameter is missing, or a parameter value violates
                the JSON value contract or the reported constraints.
            ViCommandRejectedError: If the API rejects the command.
            ViResponseError: If the command URI is outside the Vi API or the
                successful command response violates the API contract.
        """
        payload = dict(parameters or {})
        command_map = command_map_of(device)
        if not command_map:
            raise ValueError(
                "The device snapshot has no commands; refresh the device with "
                "refresh_device, refresh_gateway_devices, or "
                "get_full_installation_status to obtain a snapshot with commands."
            )
        reported_commands = command_map.get(api_feature_name, ())
        command = next(
            (
                reported
                for reported in reported_commands
                if reported.name == command_name
            ),
            None,
        )
        if command is None:
            raise self._unknown_command_error(
                api_feature_name, command_name, command_map
            )
        _LOGGER.debug("Running %s of %s", command_name, api_feature_name)
        self._validate_command_parameters(command, payload)
        await self._send_command(command.uri, payload)

    @staticmethod
    def _unknown_command_error(
        api_feature_name: str,
        command_name: str,
        command_map: Mapping[str, tuple[DeviceCommand, ...]],
    ) -> ValueError:
        """Return the error for a name the snapshot does not resolve.

        An API feature without reported commands and an unknown name raise
        the same message: the snapshot lists every API feature, including
        the ones reporting none. An unknown command names its API feature's
        reported commands, and a name that resolves to no API feature names
        the API features with commands whose names are close to it, followed
        by those it nests in or contains, so a typo or a flat feature name
        points to its API feature.
        """
        reported_commands = command_map.get(api_feature_name, ())
        if not reported_commands:
            names_with_commands = sorted(
                name for name, commands in command_map.items() if commands
            )
            # Likely typo fixes come first, then API features one name nests
            # in or contains.
            close = difflib.get_close_matches(
                api_feature_name, names_with_commands, n=3, cutoff=0.8
            )
            related = [
                name
                for name in names_with_commands
                if name not in close
                and (
                    api_feature_name.startswith(f"{name}.")
                    or name.startswith(f"{api_feature_name}.")
                )
            ]
            similar = [*close, *related]
            detail = (
                f"; API features with commands: {', '.join(similar)}" if similar else ""
            )
            return ValueError(
                f"API feature '{api_feature_name}' reports no commands{detail}"
            )
        available_commands = ", ".join(
            sorted(command.name for command in reported_commands)
        )
        return ValueError(
            f"API feature '{api_feature_name}' reports no command '{command_name}'; "
            f"available commands: {available_commands}"
        )

    def _validate_command_parameters(
        self, command: DeviceCommand, parameters: dict[str, JsonValue]
    ) -> None:
        """Check supplied parameter names and their values against the report.

        The JSON value contract is checked before the constraints, so a
        value JSON cannot represent never produces a constraint message.

        Raises:
            ValueError: If a parameter name is not reported, a required
                parameter is missing, a value is not a JSON value, or a
                value violates the parameter's reported type or constraints.
        """
        reported_by_name = {
            parameter.name: parameter for parameter in command.parameters
        }
        for parameter_name in parameters:
            if parameter_name not in reported_by_name:
                raise ValueError(
                    f"Command '{command.name}' does not report parameter "
                    f"'{parameter_name}'."
                )
        for reported in command.parameters:
            if reported.required is not False and reported.name not in parameters:
                raise ValueError(
                    f"Command '{command.name}' is missing required parameter "
                    f"'{reported.name}'."
                )
        reject_non_json_parameters(parameters)
        for parameter_name, value in parameters.items():
            parameter = reported_by_name[parameter_name]
            validate_value_constraints(
                parameter.constraints or FeatureConstraints(),
                command.name,
                value,
            )

    # Reads: unpack API responses and parse API features into flat features.

    @staticmethod
    def _response_object(response: object, *, resource: str) -> dict[str, Any]:
        """Return an API response that must be a JSON object.

        ``resource`` names the response in error messages only.

        Raises:
            ViResponseError: If the response is not an object.
        """
        if not isinstance(response, dict):
            raise ViResponseError(f"{resource} response must be an object")
        # Runtime-checked container from the transport boundary; known fields
        # are re-validated field by field by the parsers.
        return cast("dict[str, Any]", response)

    @classmethod
    def _response_items(
        cls, response: object, *, resource: str
    ) -> list[dict[str, Any]]:
        """Return the entries of an API response shaped as ``{"data": [...]}``.

        ``resource`` names the response in error messages only.

        Raises:
            ViResponseError: If the response is not an object, its ``data`` is
                not a list, or an entry is not an object.
        """
        data = cls._response_object(response, resource=resource).get("data")
        if not isinstance(data, list):
            raise ViResponseError(f"{resource} response data must be a list")
        items = cast("list[object]", data)
        if not all(isinstance(item, dict) for item in items):
            raise ViResponseError(f"{resource} response data entries must be objects")
        return cast("list[dict[str, Any]]", data)

    @staticmethod
    def _reject_duplicate_feature_names(features: list[Feature]) -> None:
        """Reject API features that would give a device two features of one name.

        Raises:
            ViResponseError: If two features share a name.
        """
        seen_names: set[str] = set()
        for feature in features:
            if feature.name in seen_names:
                raise ViResponseError(
                    f"Duplicate feature name in API response: {feature.name}"
                )
            seen_names.add(feature.name)

    # Gateway-scoped device refresh.

    @staticmethod
    def _validate_gateway_devices(devices: list[Device]) -> None:
        """Validate that devices form one unambiguous gateway-scoped request."""
        installation_ids = {device.installation_id for device in devices}
        gateway_serials = {device.gateway_serial for device in devices}
        device_ids = [device.id for device in devices]
        if len(installation_ids) != 1 or len(gateway_serials) != 1:
            raise ValueError("Devices must belong to the same installation and gateway")
        if len(set(device_ids)) != len(device_ids):
            raise ValueError("Devices must have unique IDs")

    def _group_api_features_by_device(
        self, response: object, requested_device_ids: set[str]
    ) -> dict[str, list[dict[str, Any]]]:
        """Validate a gateway response and group its API features by device ID.

        Only requested devices with at least one API feature appear as keys;
        gateway-owned API features and other devices are dropped.
        """
        api_features_by_device_id: dict[str, list[dict[str, Any]]] = {}
        for api_feature in self._response_items(response, resource="Gateway feature"):
            device_id = self._device_id_from_feature_uri(api_feature.get("uri"))
            validate_feature_entry(api_feature)
            if device_id in requested_device_ids:
                api_features_by_device_id.setdefault(device_id, []).append(api_feature)
        return api_features_by_device_id

    @staticmethod
    def _device_id_from_feature_uri(uri: object) -> str | None:
        """Return the decoded device ID from a device feature URI."""
        if not isinstance(uri, str) or not uri:
            raise ViResponseError("Gateway feature entry has no valid URI")

        try:
            raw_path_segments = urlsplit(uri).path.split("/")
            # unquote keeps malformed escapes such as "%ZZ" instead of failing,
            # so they are rejected explicitly.
            if any(
                re.search(r"%(?![0-9A-Fa-f]{2})", segment)
                for segment in raw_path_segments
            ):
                raise ViResponseError(
                    "Gateway feature entry has an invalid encoded URI"
                )
            path_segments = [
                unquote(segment, errors="strict") for segment in raw_path_segments
            ]
        except ValueError as error:
            raise ViResponseError("Gateway feature entry has an invalid URI") from error

        devices_segment_positions = [
            position
            for position, segment in enumerate(path_segments)
            if segment == "devices"
        ]
        if not devices_segment_positions:
            return None
        if len(devices_segment_positions) != 1:
            raise ViResponseError("Gateway feature URI has ambiguous device ownership")

        device_id_position = devices_segment_positions[0] + 1
        device_id = (
            path_segments[device_id_position]
            if device_id_position < len(path_segments)
            else ""
        )
        if not device_id:
            raise ViResponseError("Gateway feature URI has no device ID")
        return device_id

    async def _refresh_devices_individually(
        self, devices: list[Device]
    ) -> GatewayDeviceRefreshResult:
        """Refresh devices individually and isolate known device failures."""
        refreshed_devices: list[Device] = []
        errors_by_device_id: dict[str, ViError] = {}

        for device in devices:
            try:
                refreshed_device = await self.refresh_device(device)
            except ViError as error:
                if error.error_type not in _DEVICE_SPECIFIC_ERROR_TYPES:
                    raise
                errors_by_device_id[device.id] = error
                _LOGGER.debug(
                    "Individual refresh failed for device %s: %s", device.id, error
                )
            else:
                refreshed_devices.append(refreshed_device)

        return GatewayDeviceRefreshResult(refreshed_devices, errors_by_device_id)

    # Feature commands: check availability, build and validate the payload, send it.

    @staticmethod
    def _require_command_binding(device: Device, feature: Feature) -> WriteCommand:
        """Validate a feature's command availability and return its write command.

        A hand-built feature has no write command, so it is read-only. So is
        a feature whose API feature reports only commands that do more than
        store a value; its error names them as the `run_command` alternative.

        Raises:
            ValueError: If the feature cannot currently execute a command.
        """
        if not feature.is_writable:
            api_feature_name = owning_api_feature_name(device, feature.name)
            command_names = (
                device.get_command_names(api_feature_name) if api_feature_name else ()
            )
            if not command_names:
                raise ValueError(f"Feature '{feature.name}' is read-only.")
            raise ValueError(
                f"Feature '{feature.name}' is read-only. API feature "
                f"'{api_feature_name}' reports the commands "
                f"{', '.join(command_names)}; send one with run_command."
            )
        if not feature.is_enabled:
            raise ValueError(f"Feature '{feature.name}' is disabled.")
        if not feature.is_ready:
            raise ValueError(f"Feature '{feature.name}' is not ready.")

        binding = write_command_of(feature)
        # is_writable means a write command exists; the assert only narrows the type.
        assert binding is not None  # noqa: S101
        return binding

    @staticmethod
    def _resolve_command_payload(
        device: Device, binding: ParameterWriteCommand, target_value: FeatureValue
    ) -> dict[str, JsonValue]:
        """Resolve all parameters required for a command.

        Includes the target value itself and any dependencies found on the device.

        Args:
            device: The device object for dependency lookup.
            binding: The internal write command of the feature being set.
            target_value: The main value to set.

        Returns:
            Dictionary of JSON command parameters to be sent as payload.

        Raises:
            ValueError: If a required sibling feature is absent, disabled, not
                ready, or has no value.
        """
        payload = {binding.parameter_name: target_value}

        for parameter in binding.required_parameters:
            if parameter == binding.parameter_name:
                continue

            # Other required parameters come from sibling features, e.g. the
            # current 'shift' when setting a heating curve's 'slope'.
            sibling_name = f"{binding.api_feature_name}.{parameter}"
            sibling = device.get_feature(sibling_name)
            dependency_label = (
                f"Required dependency '{sibling_name}' for command "
                f"'{binding.command_name}'"
            )
            if sibling is None:
                raise ValueError(f"{dependency_label} is not present on the device.")
            if not sibling.is_enabled:
                raise ValueError(f"{dependency_label} is disabled.")
            if not sibling.is_ready:
                raise ValueError(f"{dependency_label} is not ready.")
            if sibling.value is None:
                raise ValueError(f"{dependency_label} has no value.")

            payload[parameter] = sibling.value
            _LOGGER.debug(
                "Resolved dependency '%s' with value %s", parameter, sibling.value
            )
        return payload

    async def _send_command(self, uri: str, payload: dict[str, JsonValue]) -> None:
        """Send a prepared command payload and raise on a rejected command.

        Args:
            uri: The API URI of the bound command.
            payload: The validated command parameters to send.

        Raises:
            ViCommandRejectedError: If the API answers with a rejected
                command response.
            ViResponseError: If the command response violates the API contract.
        """
        response = await self._command_adapter.send_command(uri, payload)
        outcome = parse_command_outcome(
            self._response_object(response, resource="Command")
        )
        if outcome.success:
            _LOGGER.debug(
                "Command succeeded (reason: %s): %s",
                outcome.reason,
                outcome.message,
            )
            return
        raise ViCommandRejectedError(outcome.message, outcome.reason)
