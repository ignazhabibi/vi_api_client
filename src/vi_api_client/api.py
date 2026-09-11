"""Viessmann API Client."""

import logging
import re
from dataclasses import replace
from typing import Any
from urllib.parse import unquote, urlsplit

from ._commands import _CommandAdapter, _LiveCommandAdapter
from ._discovery import _DiscoveryAdapter, _LiveDiscoveryAdapter
from .auth import AbstractAuth
from .connection import ViConnector
from .const import ENDPOINT_FEATURES
from .exceptions import ViError, ViResponseError, ViValidationError
from .models import (
    CommandResponse,
    Device,
    Feature,
    FeatureControl,
    Gateway,
    GatewayDeviceRefreshResult,
    Installation,
)
from .parsing import parse_feature_flat

_LOGGER = logging.getLogger(__name__)


class ViClient:
    """Client for Viessmann Climate Solutions API.

    Attributes:
        connector: Example connector instance handling auth and HTTP requests.
    """

    def __init__(self, auth: AbstractAuth) -> None:
        """Initialize the client.

        Args:
            auth: Authentication handler providing the access token.
        """
        self.connector = ViConnector(auth)
        self._discovery_adapter: _DiscoveryAdapter = _LiveDiscoveryAdapter(
            self.connector
        )
        self._command_adapter: _CommandAdapter = _LiveCommandAdapter(self.connector)

    async def get_installations(self) -> list[Installation]:
        """Get list of installations.

        Returns:
            List of Installation objects available to the user.
        """
        _LOGGER.debug("Fetching installations...")
        installations_data = await self._discovery_adapter.get_installations()
        installations = [
            Installation.from_api(installation_data)
            for installation_data in self._get_discovery_data(
                installations_data, "Installation"
            )
        ]
        _LOGGER.debug("Found %s installations", len(installations))
        return installations

    async def get_gateways(self) -> list[Gateway]:
        """Get list of gateways.

        Returns:
            List of Gateway objects found (across all installations).
        """
        _LOGGER.debug("Fetching gateways...")
        gateways_data = await self._discovery_adapter.get_gateways()
        gateways = [
            Gateway.from_api(gateway_data)
            for gateway_data in self._get_discovery_data(gateways_data, "Gateway")
        ]
        _LOGGER.debug("Found %s gateways", len(gateways))
        return gateways

    async def get_devices(
        self,
        installation_id: str,
        gateway_serial: str,
        include_features: bool = False,
        only_active_features: bool = False,
    ) -> list[Device]:
        """Get devices as typed objects.

        Args:
            installation_id: ID of the installation.
            gateway_serial: Serial number of the gateway.
            include_features: Whether to automatically fetch features for all devices.
            only_active_features: If include_features is True, fetch only enabled ones.

        Returns:
            List of Device objects (populated with features if requested).
        """
        devices_data = await self._discovery_adapter.get_devices(
            installation_id, gateway_serial
        )
        devices = [
            Device.from_api(device_data, gateway_serial, installation_id)
            for device_data in devices_data.get("data", [])
        ]

        if include_features:
            _LOGGER.debug(
                "Hydrating %s devices with features (active_only=%s)...",
                len(devices),
                only_active_features,
            )
            populated_devices = []
            for device in devices:
                features = await self.get_features(
                    device, only_enabled=only_active_features
                )
                populated_devices.append(replace(device, features=features))
            return populated_devices

        return devices

    async def get_features(
        self,
        device: Device,
        only_enabled: bool = False,
        feature_names: list[str] | None = None,
    ) -> list[Feature]:
        """Get features for a device as typed objects.

        Args:
            device: The device to fetch features for.
            only_enabled: If True, only return enabled/ready features.
            feature_names: Optional list of specific feature names to fetch.

        Returns:
            List of Feature objects (flattened).
        """
        payload: dict[str, bool | list[str]] = {
            "skipDisabled": only_enabled,
            "skipNotReady": only_enabled,
        }
        if feature_names:
            payload["filter"] = feature_names

        _LOGGER.debug(
            "Fetching features for device %s (enabled=%s)...",
            device.id,
            only_enabled,
        )
        response = await self._discovery_adapter.get_features(device, payload)
        raw_features = response.get("data", [])

        flat_features = []
        for raw_feature in raw_features:
            flat_features.extend(parse_feature_flat(raw_feature))

        filtered_features = [
            feature
            for feature in flat_features
            if (not only_enabled or (feature.is_enabled and feature.is_ready))
            and (not feature_names or feature.name in feature_names)
        ]

        _LOGGER.debug(
            "Fetched %s raw objects -> %s flat features",
            len(raw_features),
            len(filtered_features),
        )
        return filtered_features

    async def get_full_installation_status(
        self, installation_id: str, only_enabled: bool = True
    ) -> list[Device]:
        """Fetch full status of an installation (Gateways -> Devices -> Features).

        Args:
            installation_id: ID of the installation to scan.
            only_enabled: Whether to skip disabled features (default: True).

        Returns:
            List of Devices with their `features` list populated.
        """
        gateways = await self.get_gateways()
        all_devices = []

        for gateway in gateways:
            if gateway.installation_id != installation_id:
                continue

            devices = await self.get_devices(
                installation_id,
                gateway.serial,
                include_features=True,
                only_active_features=only_enabled,
            )
            all_devices.extend(devices)

        return all_devices

    async def update_device(self, device: Device, only_enabled: bool = True) -> Device:
        """Refresh the features of an existing device.

        Args:
            device: The device object to refresh.
            only_enabled: Whether to fetch only enabled features.

        Returns:
            A new Device instance with updated features (immutable update).
        """
        features = await self.get_features(device, only_enabled=only_enabled)
        return replace(device, features=features)

    async def update_gateway_devices(
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

        first_device = devices[0]
        url = (
            f"{ENDPOINT_FEATURES}/{first_device.installation_id}/gateways/"
            f"{first_device.gateway_serial}/features/filter"
        )
        payload = {
            "includeDevicesFeatures": True,
            "skipDisabled": True,
            "skipNotReady": True,
        }
        try:
            response = await self.connector.post(url, payload)
        except ViValidationError as error:
            if error.error_type == "DEVICE_COMMUNICATION_ERROR":
                return await self._refresh_devices_individually(devices)
            raise

        requested_device_ids = {device.id for device in devices}
        raw_features_by_device_id, seen_device_ids = self._group_gateway_features(
            response, requested_device_ids
        )

        updated_devices_by_id: dict[str, Device] = {}
        for device in devices:
            if device.id not in seen_device_ids:
                continue
            raw_features = raw_features_by_device_id[device.id]
            features = self._parse_gateway_device_features(device.id, raw_features)
            updated_devices_by_id[device.id] = replace(device, features=features)

        missing_devices = [
            device for device in devices if device.id not in seen_device_ids
        ]
        fallback_result = await self._refresh_devices_individually(missing_devices)
        updated_devices_by_id.update(
            {device.id: device for device in fallback_result.updated_devices}
        )
        updated_devices = [
            updated_devices_by_id[device.id]
            for device in devices
            if device.id in updated_devices_by_id
        ]

        return GatewayDeviceRefreshResult(
            updated_devices, fallback_result.errors_by_device_id
        )

    async def set_feature(
        self, device: Device, feature: Feature, target_value: Any
    ) -> tuple[CommandResponse, Device]:
        """Set a value for a feature and return optimistically updated device.

        Automatically resolves dependencies (other required parameters for the command)
        by looking them up in the device's feature list.

        Args:
            device: The device allowing context lookup for dependencies.
            feature: The feature to set.
            target_value: The value to write.

        Returns:
            Tuple of (command_response, updated_device).
            - If successful: device with optimistically updated feature value.
            - If failed: original device unchanged.

        Raises:
            ValueError: If feature is read-only or value is out of bounds.
        """
        if not feature.is_writable:
            raise ValueError(f"Feature '{feature.name}' is read-only.")

        control = feature.control
        assert control is not None
        _LOGGER.debug(
            "Setting %s to %s via %s",
            feature.name,
            target_value,
            control.command_name,
        )

        # 1. Prepare Payload (Dependency Resolution)
        payload = self._resolve_command_payload(device, control, target_value)

        # 2. Client-Side Validation
        self._validate_constraints(control, target_value)

        # 3. Execution
        response = await self._execute_command(control, payload)

        # 4. Optimistic Device Update
        if response.success:
            # Update feature value optimistically
            updated_feature = replace(feature, value=target_value)
            updated_features = [
                updated_feature
                if existing_feature.name == feature.name
                else existing_feature
                for existing_feature in device.features
            ]
            updated_device = replace(device, features=updated_features)
            return response, updated_device

        # Return unchanged device on failure
        return response, device

    async def execute_command(
        self, feature: Feature, parameters: dict[str, Any]
    ) -> CommandResponse:
        """Execute an explicit feature command without changing its parameters.

        Args:
            feature: A writable feature that identifies the command endpoint.
            parameters: Complete command parameters to send exactly as supplied.

        Returns:
            The command response from the API.

        Raises:
            ValueError: If the feature is read-only.
        """
        if not feature.is_writable:
            raise ValueError(f"Feature '{feature.name}' is read-only.")

        control = feature.control
        assert control is not None
        _LOGGER.debug("Executing %s for %s", control.command_name, feature.name)
        return await self._execute_command(control, parameters)

    # ------------------------------------------------------------------
    # Private Helper Methods
    # ------------------------------------------------------------------

    @staticmethod
    def _get_discovery_data(
        envelope: object, resource_name: str
    ) -> list[dict[str, Any]]:
        """Validate and return the data collection from a discovery envelope."""
        if not isinstance(envelope, dict):
            raise ViResponseError(f"{resource_name} response must be an object")
        data = envelope.get("data")
        if not isinstance(data, list) or not all(
            isinstance(item, dict) for item in data
        ):
            raise ViResponseError(f"{resource_name} response data must be a list")
        return data

    async def _execute_command(
        self, control: FeatureControl, payload: dict[str, Any]
    ) -> CommandResponse:
        """Execute a feature command through the configured command adapter.

        Args:
            control: The feature control describing the command endpoint.
            payload: The command parameters to send.

        Returns:
            The parsed command response.
        """
        response_data = await self._command_adapter.execute_command(control, payload)
        if not isinstance(response_data, dict):
            raise ViResponseError("Command response must be an object")
        response = response_data.get("data", response_data)
        if not isinstance(response, dict):
            raise ViResponseError("Command response data must be an object")
        return CommandResponse.from_api(response_data)

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

    def _group_gateway_features(
        self, response: object, requested_device_ids: set[str]
    ) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
        """Validate and group a gateway response by requested device ID."""
        if not isinstance(response, dict):
            raise ViResponseError("Gateway feature response must be an object")
        raw_response_features = response.get("data")
        if not isinstance(raw_response_features, list):
            raise ViResponseError("Gateway feature response data must be a list")

        grouped_features: dict[str, list[dict[str, Any]]] = {
            device_id: [] for device_id in requested_device_ids
        }
        seen_device_ids: set[str] = set()
        for raw_feature in raw_response_features:
            if not isinstance(raw_feature, dict):
                raise ViResponseError("Gateway feature entries must be objects")
            device_id = self._get_feature_device_id(raw_feature.get("uri"))
            if device_id in grouped_features:
                self._validate_gateway_feature(raw_feature)
                grouped_features[device_id].append(raw_feature)
                seen_device_ids.add(device_id)

        return grouped_features, seen_device_ids

    @staticmethod
    def _validate_gateway_feature(raw_feature: dict[str, Any]) -> None:
        """Validate fields required to parse an ordinary device feature."""
        feature_name = raw_feature.get("feature")
        properties = raw_feature.get("properties")
        commands = raw_feature.get("commands", {})
        if not isinstance(feature_name, str) or not feature_name:
            raise ViResponseError("Gateway device feature has no valid feature name")
        if not isinstance(properties, dict) or not isinstance(commands, dict):
            raise ViResponseError("Gateway device feature has invalid feature data")

    @staticmethod
    def _parse_gateway_device_features(
        device_id: str, raw_features: list[dict[str, Any]]
    ) -> list[Feature]:
        """Parse one device's features and expose contract failures consistently."""
        features = []
        try:
            for raw_feature in raw_features:
                features.extend(parse_feature_flat(raw_feature))
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise ViResponseError(
                f"Invalid feature data for device {device_id}"
            ) from error
        return features

    def _get_feature_device_id(self, uri: object) -> str | None:
        """Return the decoded device ID from a device feature URI."""
        if not isinstance(uri, str) or not uri:
            raise ViResponseError("Gateway feature entry has no valid URI")

        try:
            raw_path_segments = urlsplit(uri).path.split("/")
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

        device_indexes = [
            index for index, segment in enumerate(path_segments) if segment == "devices"
        ]
        if not device_indexes:
            return None
        if len(device_indexes) != 1:
            raise ViResponseError("Gateway feature URI has ambiguous device ownership")

        devices_index = device_indexes[0]
        if (
            devices_index + 1 >= len(path_segments)
            or not path_segments[devices_index + 1]
        ):
            raise ViResponseError("Gateway feature URI has no device ID")
        return path_segments[devices_index + 1]

    async def _refresh_devices_individually(
        self, devices: list[Device]
    ) -> GatewayDeviceRefreshResult:
        """Refresh devices individually and isolate known device failures."""
        updated_devices = []
        errors_by_device_id: dict[str, ViError] = {}
        device_error_types = {
            "DEVICE_COMMUNICATION_ERROR",
            "DEVICE_NOT_FOUND",
            "PACKAGE_NOT_PAID_FOR",
        }

        for device in devices:
            try:
                features = await self.get_features(device, only_enabled=True)
            except ViError as error:
                if error.error_type not in device_error_types:
                    raise
                errors_by_device_id[device.id] = error
            except (AttributeError, KeyError, TypeError, ValueError) as error:
                raise ViResponseError(
                    f"Invalid feature data for device {device.id}"
                ) from error
            else:
                updated_devices.append(replace(device, features=features))

        return GatewayDeviceRefreshResult(updated_devices, errors_by_device_id)

    def _resolve_command_payload(
        self, device: Device, ctrl: FeatureControl, target_value: Any
    ) -> dict[str, Any]:
        """Resolve all parameters required for a command.

        Includes the target value itself and any dependencies found on the device.

        Args:
            device: The device object for dependency lookup.
            ctrl: The feature control definition.
            target_value: The main value to set.

        Returns:
            Dictionary of parameters to be sent as JSON payload.
        """
        payload = {}

        for param_key in ctrl.required_params:
            # Case A: The value we want to set
            if param_key == ctrl.param_name:
                payload[param_key] = target_value
                continue

            # Case B: A dependency parameter (e.g. 'shift' when setting 'slope')
            # Look for sibling feature: parent_feature_name + "." + param_key
            sibling_name = f"{ctrl.parent_feature_name}.{param_key}"
            sibling = device.get_feature(sibling_name)

            if sibling:
                payload[param_key] = sibling.value
                _LOGGER.debug(
                    "  -> Resolved dependency '%s' with value %s",
                    param_key,
                    sibling.value,
                )
            else:
                _LOGGER.warning(
                    "  -> Dependency '%s' for command '%s' not found in "
                    "device features. Sending without it.",
                    param_key,
                    ctrl.command_name,
                )
        return payload

    def _validate_constraints(self, ctrl: FeatureControl, value: Any) -> None:
        """Validate value against all constraints using type-based dispatch.

        Args:
            ctrl: The feature control definition containing constraints.
            value: The value to check.

        Raises:
            ValueError: If value violates any constraints.
        """
        # Generic Enum Check (applies to all types)
        if ctrl.options:
            self._validate_enum_constraints(ctrl, value)

        # Type-specific Dispatch
        if isinstance(value, int | float):
            self._validate_numeric_constraints(ctrl, value)
        elif isinstance(value, str):
            self._validate_string_constraints(ctrl, value)

    def _validate_numeric_constraints(
        self, ctrl: FeatureControl, value: int | float
    ) -> None:
        """Validate numeric bounds and step."""
        if ctrl.min is not None and value < ctrl.min:
            raise ValueError(f"Value {value} < min ({ctrl.min})")
        if ctrl.max is not None and value > ctrl.max:
            raise ValueError(f"Value {value} > max ({ctrl.max})")

        if ctrl.step is not None and ctrl.step > 0:
            # Check if value aligns with step (relative to min, or 0 if min missing)
            base = ctrl.min if ctrl.min is not None else 0
            diff = value - base
            # Allow small float error (epsilon)
            remainder = diff % ctrl.step
            # remainder should be close to 0 or close to step
            is_valid = remainder < 1e-9 or abs(remainder - ctrl.step) < 1e-9

            if not is_valid:
                raise ValueError(
                    f"Value {value} does not align with step {ctrl.step} "
                    f"(starting from {base})"
                )

    def _validate_enum_constraints(self, ctrl: FeatureControl, value: Any) -> None:
        """Validate enum options."""
        if value not in ctrl.options:
            raise ValueError(f"Value {value} is not in allowed options: {ctrl.options}")

    def _validate_string_constraints(self, ctrl: FeatureControl, value: str) -> None:
        """Validate string length and pattern."""
        if ctrl.min_length is not None and len(value) < ctrl.min_length:
            raise ValueError(
                f"Value length {len(value)} < min_length ({ctrl.min_length})"
            )
        if ctrl.max_length is not None and len(value) > ctrl.max_length:
            raise ValueError(
                f"Value length {len(value)} > max_length ({ctrl.max_length})"
            )
        if ctrl.pattern and not re.match(ctrl.pattern, value):
            raise ValueError(f"Value '{value}' does not match pattern '{ctrl.pattern}'")
