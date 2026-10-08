"""Live and shared client workflows for the Viessmann API."""

import itertools
import logging
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import unquote, urlsplit

from ._adapter import CommandAdapter, DiscoveryAdapter, LiveAdapter
from ._types import FeatureValue, JsonValue
from .auth import AbstractAuth
from .const import EVENT_HISTORY_MAX_LIMIT
from .exceptions import ViError, ViResponseError, ViValidationError
from .models import (
    CommandResponse,
    Device,
    EventHistoryPage,
    Feature,
    FeatureControl,
    Gateway,
    GatewayDeviceRefreshResult,
    Installation,
    ScheduleConstraints,
)
from .parsing import api_feature_to_flat_features, validate_feature_entry
from .utils import mask_identifiers
from .validation import validate_json_value

_LOGGER = logging.getLogger(__name__)

# Error types that affect one device, so a per-device refresh records them for
# that device instead of aborting the whole gateway refresh.
_DEVICE_SPECIFIC_ERROR_TYPES = frozenset(
    {"DEVICE_COMMUNICATION_ERROR", "DEVICE_NOT_FOUND", "PACKAGE_NOT_PAID_FOR"}
)

_SCHEDULE_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_SCHEDULE_TIME_PATTERN = re.compile(r"([01][0-9]|2[0-4]):([0-5][0-9])")

# Float modulo can land just below the step (0.3 % 0.1 is about 0.1), so a
# remainder this close to either end of the step counts as aligned.
_STEP_TOLERANCE = 1e-9


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
            Installation.from_api(installation_data)
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
            Gateway.from_api(gateway_data)
            for gateway_data in self._response_items(response, resource="Gateway")
        ]
        _LOGGER.debug("Found %s gateways", len(gateways))
        return gateways

    async def get_devices(
        self,
        installation_id: str,
        gateway_serial: str,
        include_features: bool = False,
        only_enabled: bool = False,
    ) -> list[Device]:
        """Return the devices behind one gateway.

        Args:
            installation_id: ID of the installation.
            gateway_serial: Serial number of the gateway.
            include_features: Whether to automatically fetch features for all devices.
            only_enabled: If include_features is True, fetch only enabled and ready
                features.

        Returns:
            Device snapshots. When requested, device feature hydration produces
            new snapshots from the feature responses.

        Raises:
            ViResponseError: If a successful response violates the API contract.
        """
        response = await self._discovery_adapter.get_devices(
            installation_id, gateway_serial
        )
        devices = [
            Device.from_api(device_data, gateway_serial, installation_id)
            for device_data in self._response_items(response, resource="Device")
        ]

        if include_features:
            _LOGGER.debug(
                "Hydrating %s devices with features (only_enabled=%s)",
                len(devices),
                only_enabled,
            )
            return [
                await self.refresh_device(device, only_enabled=only_enabled)
                for device in devices
            ]

        return devices

    async def get_features(
        self,
        device: Device,
        only_enabled: bool = False,
        feature_names: list[str] | None = None,
    ) -> list[Feature]:
        """Return a device's features.

        Names are matched locally after fetching the device's features, so live
        and fixture-backed clients select the same features. The API's
        server-side name filter is not used: it selects API features, while
        callers mostly address the flat features parsed from them.

        Args:
            device: The device to fetch features for.
            only_enabled: If True, only return enabled/ready features.
            feature_names: Optional feature names or API feature names to
                return. An API feature name selects every feature parsed from
                that API feature. Unknown names select nothing.

        Returns:
            List of Feature objects (flattened).

        Raises:
            ViResponseError: If the API response is malformed or the returned
                features contain duplicate names.
        """
        payload = {
            "skipDisabled": only_enabled,
            "skipNotReady": only_enabled,
        }

        _LOGGER.debug(
            "Fetching features for device %s (enabled=%s)",
            device.id,
            only_enabled,
        )
        response = await self._discovery_adapter.get_features(device, payload)
        api_features = self._response_items(response, resource="Feature")

        features = self._api_features_to_flat_features(api_features, feature_names)
        self._reject_duplicate_feature_names(features)
        if only_enabled:
            features = [
                feature
                for feature in features
                if feature.is_enabled and feature.is_ready
            ]

        _LOGGER.debug(
            "Fetched %s API features -> %s features", len(api_features), len(features)
        )
        return features

    async def get_full_installation_status(
        self, installation_id: str, only_enabled: bool = True
    ) -> list[Device]:
        """Return all devices of an installation with their features.

        Args:
            installation_id: ID of the installation to scan.
            only_enabled: Whether to skip disabled features (default: True).

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

            devices = await self.get_devices(
                installation_id,
                gateway.serial,
                include_features=True,
                only_enabled=only_enabled,
            )
            all_devices.extend(devices)

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
        return EventHistoryPage.from_api(
            self._response_object(response, resource="Event history")
        )

    async def refresh_device(self, device: Device, only_enabled: bool = True) -> Device:
        """Return a refreshed device snapshot from an API feature read.

        Args:
            device: The device object to refresh.
            only_enabled: Whether to fetch only enabled features.

        Returns:
            A refreshed device snapshot with features from the API response.

        Raises:
            ViResponseError: If the API response is malformed or contains
                duplicate feature names.
        """
        features = await self.get_features(device, only_enabled=only_enabled)
        return replace(device, features=features)

    async def export_device_fixture(self, device: Device) -> dict[str, JsonValue]:
        """Return a device's raw API features as an anonymized fixture document.

        The features are always read fresh from the API, including disabled
        and not-ready features, and kept in their raw API shape. The device
        only identifies what to read: features already on ``device`` are not
        used. The document is masked with `mask_identifiers`, so it carries no
        installation IDs, serials, or coordinates and can be shared, for
        example as a new fixture.

        Args:
            device: The device to export.

        Returns:
            ``{"device": {...}, "data": [...]}``: the device's ``modelId``,
            ``deviceType``, and the UTC ``capturedAt`` date, and the masked
            raw API features.

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
                "capturedAt": datetime.now(UTC).date().isoformat(),
            },
            "data": validate_json_value(api_features, path="features"),
        }
        # Masking keeps the document's shape, so the result is still an object.
        return cast("dict[str, JsonValue]", mask_identifiers(document))

    async def refresh_gateway_devices(
        self, devices: list[Device]
    ) -> GatewayDeviceRefreshResult:
        """Refresh enabled and ready features for devices on one gateway.

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
        except ViValidationError as error:
            if error.error_type == "DEVICE_COMMUNICATION_ERROR":
                _LOGGER.debug(
                    "Gateway-wide feature fetch failed with %s; "
                    "falling back to individual device refreshes",
                    error.error_type,
                )
                return await self._refresh_devices_individually(devices)
            raise

        requested_device_ids = {device.id for device in devices}
        api_features_by_device_id = self._group_api_features_by_device(
            response, requested_device_ids
        )

        missing_devices: list[Device] = []
        updated_devices_by_id: dict[str, Device] = {}
        for device in devices:
            api_features = api_features_by_device_id.get(device.id)
            if api_features is None:
                missing_devices.append(device)
                continue
            features = self._api_features_to_flat_features(api_features)
            self._reject_duplicate_feature_names(features)
            updated_devices_by_id[device.id] = replace(device, features=features)

        if missing_devices:
            _LOGGER.debug(
                "Gateway feature response omitted %s requested device(s); "
                "refreshing them individually",
                len(missing_devices),
            )
        fallback_result = await self._refresh_devices_individually(missing_devices)
        updated_devices_by_id.update(
            (device.id, device) for device in fallback_result.updated_devices
        )

        # Keep the caller's device order across both refresh paths.
        updated_devices = [
            updated_devices_by_id[device.id]
            for device in devices
            if device.id in updated_devices_by_id
        ]

        return GatewayDeviceRefreshResult(
            updated_devices, fallback_result.errors_by_device_id
        )

    async def set_feature(
        self, device: Device, feature: Feature, target_value: FeatureValue
    ) -> tuple[CommandResponse, Device]:
        """Set a current device feature and return a command-updated snapshot.

        Resolves ``feature.name`` against ``device`` before validating its current
        command availability, constraints, and required sibling parameters.

        Args:
            device: The device allowing context lookup for dependencies.
            feature: A feature whose name identifies the current device feature.
            target_value: The JSON value to write.

        Returns:
            Tuple of (command_response, updated_device).
            - If successful: command-updated device snapshot with the feature
              value set locally, without an API read-back.
            - If failed: original device snapshot unchanged.

        Raises:
            ValueError: If the named feature is absent or unavailable, a
                required sibling is absent, unavailable, or has no value, the
                target value violates its constraints, or a command parameter
                value is not a JSON value.
            ViResponseError: If the command URI is outside the Vi API or the
                successful command response violates the API contract.
        """
        current_feature = device.get_feature(feature.name)
        if current_feature is None:
            raise ValueError(f"Feature '{feature.name}' is not present on the device.")

        control = self._require_command_control(current_feature)
        _LOGGER.debug(
            "Setting %s to %s via %s",
            current_feature.name,
            target_value,
            control.command_name,
        )

        payload = self._resolve_command_payload(device, control, target_value)
        self._reject_non_json_parameters(payload)
        self._validate_constraints(control, target_value)

        response = await self._send_command(control, payload)

        if response.success:
            # Preserve all other feature values from the input snapshot.
            updated_feature = replace(current_feature, value=target_value)
            updated_features = [
                updated_feature
                if existing_feature.name == current_feature.name
                else existing_feature
                for existing_feature in device.features
            ]
            updated_device = replace(device, features=updated_features)
            return response, updated_device

        _LOGGER.warning(
            "Setting %s via %s failed (reason: %s)",
            current_feature.name,
            control.command_name,
            response.reason,
        )
        return response, device

    async def execute_command(
        self, feature: Feature, parameters: dict[str, JsonValue]
    ) -> CommandResponse:
        """Execute an explicit available-feature command without changing parameters.

        Args:
            feature: A writable, enabled, and ready feature identifying the command.
            parameters: Complete command parameters to send exactly as supplied;
                every parameter value must be within the JSON value contract.

        Returns:
            The command response from the API.

        Raises:
            ValueError: If the feature is unavailable, the payload omits its
                target or a required parameter, or a parameter value is not a
                JSON value.
            ViResponseError: If the command URI is outside the Vi API or the
                successful command response violates the API contract.
        """
        control = self._require_command_control(feature)
        self._reject_missing_parameters(control, parameters)
        self._reject_non_json_parameters(parameters)
        _LOGGER.debug("Executing %s for %s", control.command_name, feature.name)
        return await self._send_command(control, parameters)

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
    def _api_features_to_flat_features(
        api_features: list[dict[str, Any]], feature_names: list[str] | None = None
    ) -> list[Feature]:
        """Parse one device's API features into flat features.

        A name selects a feature by its own name or by the name of the API
        feature it was parsed from; without names every feature is kept.

        Raises:
            ViResponseError: If an API feature is malformed.
        """
        requested_names = set(feature_names or ())
        selected_features: list[Feature] = []
        for api_feature in api_features:
            features = api_feature_to_flat_features(api_feature)
            # api_feature_to_flat_features validated the entry's API feature name.
            if not requested_names or api_feature["feature"] in requested_names:
                selected_features.extend(features)
            else:
                selected_features.extend(
                    feature for feature in features if feature.name in requested_names
                )
        return selected_features

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
        updated_devices: list[Device] = []
        errors_by_device_id: dict[str, ViError] = {}

        for device in devices:
            try:
                updated_device = await self.refresh_device(device, only_enabled=True)
            except ViError as error:
                if error.error_type not in _DEVICE_SPECIFIC_ERROR_TYPES:
                    raise
                errors_by_device_id[device.id] = error
                _LOGGER.debug(
                    "Individual refresh failed for device %s: %s", device.id, error
                )
            else:
                updated_devices.append(updated_device)

        return GatewayDeviceRefreshResult(updated_devices, errors_by_device_id)

    # Feature commands: check availability, build and validate the payload, send it.

    @staticmethod
    def _require_command_control(feature: Feature) -> FeatureControl:
        """Validate a feature's command availability and return its control.

        Raises:
            ValueError: If the feature cannot currently execute a command.
        """
        if not feature.is_writable:
            raise ValueError(f"Feature '{feature.name}' is read-only.")
        if not feature.is_enabled:
            raise ValueError(f"Feature '{feature.name}' is disabled.")
        if not feature.is_ready:
            raise ValueError(f"Feature '{feature.name}' is not ready.")

        control = feature.control
        # is_writable means a control exists; the assert only narrows the type.
        assert control is not None  # noqa: S101
        return control

    @staticmethod
    def _reject_missing_parameters(
        control: FeatureControl, parameters: dict[str, JsonValue]
    ) -> None:
        """Reject an explicit command payload that lacks a required parameter.

        Raises:
            ValueError: If the target or a required parameter is absent.
        """
        if control.parameter_name not in parameters:
            raise ValueError(
                f"Command '{control.command_name}' is missing target parameter "
                f"'{control.parameter_name}'."
            )
        for parameter in control.required_parameters:
            if parameter not in parameters:
                raise ValueError(
                    f"Command '{control.command_name}' is missing required parameter "
                    f"'{parameter}'."
                )

    @staticmethod
    def _resolve_command_payload(
        device: Device, control: FeatureControl, target_value: FeatureValue
    ) -> dict[str, JsonValue]:
        """Resolve all parameters required for a command.

        Includes the target value itself and any dependencies found on the device.

        Args:
            device: The device object for dependency lookup.
            control: The feature control definition.
            target_value: The main value to set.

        Returns:
            Dictionary of JSON command parameters to be sent as payload.

        Raises:
            ValueError: If a required sibling feature is absent, disabled, not
                ready, or has no value.
        """
        payload = {control.parameter_name: target_value}

        for parameter in control.required_parameters:
            if parameter == control.parameter_name:
                continue

            # Other required parameters come from sibling features, e.g. the
            # current 'shift' when setting a heating curve's 'slope'.
            sibling_name = f"{control.parent_feature_name}.{parameter}"
            sibling = device.get_feature(sibling_name)
            dependency_label = (
                f"Required dependency '{sibling_name}' for command "
                f"'{control.command_name}'"
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

    @staticmethod
    def _reject_non_json_parameters(parameters: dict[str, JsonValue]) -> None:
        """Reject command parameter values the JSON value contract excludes.

        The parameters come from the caller, so a violation is a ValueError
        rather than the ViResponseError the shared validator raises for API
        data.

        Raises:
            ValueError: If a parameter key or value is not JSON-compatible.
        """
        try:
            validate_json_value(parameters, path="Command parameters")
        except ViResponseError as error:
            raise ValueError(str(error)) from error

    def _validate_constraints(
        self, control: FeatureControl, value: FeatureValue
    ) -> None:
        """Validate value against all constraints using type-based dispatch.

        Args:
            control: The feature control definition containing constraints.
            value: The JSON value to check.

        Raises:
            ValueError: If value has the wrong type for the command parameter
                or violates any constraints.
        """
        if not _matches_value_type(control.value_type, value):
            raise ValueError(
                f"Value {value!r} is not of type '{control.value_type}' "
                f"expected by command '{control.command_name}'"
            )
        if control.options and value not in control.options:
            raise ValueError(
                f"Value {value} is not in allowed options: {control.options}"
            )

        if control.value_type == "Schedule":
            self._validate_schedule(control.schedule, value)
        elif isinstance(value, int | float):
            self._validate_numeric_constraints(control, value)
        elif isinstance(value, str):
            self._validate_string_constraints(control, value)

    @staticmethod
    def _validate_numeric_constraints(
        control: FeatureControl, value: int | float
    ) -> None:
        """Validate numeric bounds and step."""
        if control.min is not None and value < control.min:
            raise ValueError(f"Value {value} < min ({control.min})")
        if control.max is not None and value > control.max:
            raise ValueError(f"Value {value} > max ({control.max})")

        if control.step is not None and control.step > 0:
            base = control.min if control.min is not None else 0
            offset = value - base
            remainder = offset % control.step
            is_aligned = (
                remainder < _STEP_TOLERANCE
                or abs(remainder - control.step) < _STEP_TOLERANCE
            )

            if not is_aligned:
                raise ValueError(
                    f"Value {value} does not align with step {control.step} "
                    f"(starting from {base})"
                )

    @staticmethod
    def _validate_string_constraints(control: FeatureControl, value: str) -> None:
        """Validate string length and pattern."""
        if control.min_length is not None and len(value) < control.min_length:
            raise ValueError(
                f"Value length {len(value)} < min_length ({control.min_length})"
            )
        if control.max_length is not None and len(value) > control.max_length:
            raise ValueError(
                f"Value length {len(value)} > max_length ({control.max_length})"
            )
        if control.pattern and not re.fullmatch(control.pattern, value):
            raise ValueError(
                f"Value '{value}' does not match pattern '{control.pattern}'"
            )

    @staticmethod
    def _validate_schedule(
        constraints: ScheduleConstraints | None, value: FeatureValue
    ) -> None:
        """Validate a weekly plan against the general shape and the API rules.

        A plan maps every weekday to a list of time slots with ``start`` and
        ``end`` times in ``HH:MM``; ``24:00`` ends a slot at midnight. The API
        replaces the whole plan, so all seven days are required; a day without
        slots is an empty list. The rules the API reports per schedule (slot
        count, modes, time grid, overlaps) are checked when present. Other slot
        fields such as ``position`` are left to the API.

        Raises:
            ValueError: If the plan violates its shape or a reported rule.
        """
        if not isinstance(value, dict):
            raise ValueError("Schedule must be an object mapping weekdays to slots")
        rules = constraints or ScheduleConstraints()
        for day, slots in value.items():
            if day not in _SCHEDULE_DAYS:
                raise ValueError(f"Schedule day '{day}' is not one of mon to sun")
            if not isinstance(slots, list):
                raise ValueError(f"Schedule day '{day}' must be a list of slots")
            if rules.max_entries is not None and len(slots) > rules.max_entries:
                raise ValueError(
                    f"Schedule day '{day}' has {len(slots)} slots, "
                    f"at most {rules.max_entries} are allowed"
                )
            slot_times = [_schedule_slot_times(slot, day, rules) for slot in slots]
            if rules.overlap_allowed is False:
                ordered_times = sorted(slot_times)
                for (_, previous_end), (next_start, _) in itertools.pairwise(
                    ordered_times
                ):
                    if next_start < previous_end:
                        raise ValueError(f"Schedule day '{day}' slots overlap")
        missing_days = [day for day in _SCHEDULE_DAYS if day not in value]
        if missing_days:
            raise ValueError(
                f"Schedule must contain every weekday, missing: "
                f"{', '.join(missing_days)}"
            )

    async def _send_command(
        self, control: FeatureControl, payload: dict[str, JsonValue]
    ) -> CommandResponse:
        """Send a prepared command payload and parse the command response.

        Args:
            control: The feature control describing the command endpoint.
            payload: The validated command parameters to send.

        Returns:
            The parsed command response.

        Raises:
            ViResponseError: If the command response violates the API contract.
        """
        response = await self._command_adapter.execute_command(control, payload)
        return CommandResponse.from_api(
            self._response_object(response, resource="Command")
        )


def _matches_value_type(value_type: str | None, value: FeatureValue) -> bool:
    """Return whether a value fits the command parameter's reported type.

    Unknown or missing types are not checked. Booleans never count as numbers,
    and an integer parameter also accepts a whole-number float such as ``2.0``.
    """
    if isinstance(value, bool):
        return value_type not in {"number", "integer", "string"}
    match value_type:
        case "number":
            return isinstance(value, int | float)
        case "integer":
            return isinstance(value, int) or (
                isinstance(value, float) and value.is_integer()
            )
        case "string":
            return isinstance(value, str)
        case "boolean":
            return False
        case _:
            return True


def _schedule_slot_times(
    slot: JsonValue, day: str, rules: ScheduleConstraints
) -> tuple[int, int]:
    """Return a slot's start and end in minutes after checking its fields.

    Raises:
        ValueError: If the slot is not an object, its times are invalid or out
            of order, or its mode is not one of the reported modes.
    """
    if not isinstance(slot, dict):
        raise ValueError(f"Schedule day '{day}' slots must be objects")
    start = _schedule_minutes(slot.get("start"), day, rules.resolution)
    end = _schedule_minutes(slot.get("end"), day, rules.resolution)
    if start >= end:
        raise ValueError(f"Schedule day '{day}' slot must start before it ends")
    mode = slot.get("mode")
    if rules.modes is not None and mode not in rules.modes:
        raise ValueError(
            f"Schedule day '{day}' mode {mode!r} is not one of {list(rules.modes)}"
        )
    return start, end


def _schedule_minutes(time_text: object, day: str, resolution: int | None) -> int:
    """Return a schedule time of day in minutes after midnight.

    Raises:
        ValueError: If the time is not ``HH:MM`` up to ``24:00`` or does not
            follow the reported time grid.
    """
    match = (
        _SCHEDULE_TIME_PATTERN.fullmatch(time_text)
        if isinstance(time_text, str)
        else None
    )
    if match is None:
        raise ValueError(
            f"Schedule day '{day}' times must use HH:MM, got {time_text!r}"
        )
    minutes = int(match.group(1)) * 60 + int(match.group(2))
    if minutes > 24 * 60:
        raise ValueError(f"Schedule day '{day}' time {time_text} is after 24:00")
    if resolution is not None and minutes % resolution:
        raise ValueError(
            f"Schedule day '{day}' time {time_text} is not on the "
            f"{resolution}-minute grid"
        )
    return minutes
