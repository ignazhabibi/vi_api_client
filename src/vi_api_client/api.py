"""Viessmann API Client."""

import logging
import re
from dataclasses import replace
from datetime import datetime
from typing import Any

from .analytics import parse_consumption_response, resolve_properties
from .auth import AbstractAuth
from .connection import ViConnector
from .const import (
    ENDPOINT_ANALYTICS_THERMAL,
    ENDPOINT_FEATURES,
    ENDPOINT_GATEWAYS,
    ENDPOINT_INSTALLATIONS,
)
from .models import (
    CommandResponse,
    Device,
    Feature,
    FeatureControl,
    Gateway,
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

    async def get_installations(self) -> list[Installation]:
        """Get list of installations.

        Returns:
            List of Installation objects available to the user.
        """
        _LOGGER.debug("Fetching installations...")
        installations_data = await self.connector.get(ENDPOINT_INSTALLATIONS)
        installations = [
            Installation.from_api(installation_data)
            for installation_data in installations_data.get("data", [])
        ]
        _LOGGER.debug("Found %s installations", len(installations))
        return installations

    async def get_gateways(self) -> list[Gateway]:
        """Get list of gateways.

        Returns:
            List of Gateway objects found (across all installations).
        """
        _LOGGER.debug("Fetching gateways...")
        gateways_data = await self.connector.get(ENDPOINT_GATEWAYS)
        gateways = [
            Gateway.from_api(gateway_data)
            for gateway_data in gateways_data.get("data", [])
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
        url = self._build_devices_url(installation_id, gateway_serial)
        devices_data = await self.connector.get(url)
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
        url = self._build_features_url(device)
        payload = {"skipDisabled": only_enabled, "skipNotReady": only_enabled}
        if feature_names:
            payload["filter"] = feature_names

        _LOGGER.debug(
            "Fetching features for device %s (enabled=%s)...",
            device.id,
            only_enabled,
        )
        response = await self.connector.post(url, payload)
        raw_features = response.get("data", [])

        flat_features = []
        for raw_feature in raw_features:
            flat_features.extend(parse_feature_flat(raw_feature))

        _LOGGER.debug(
            "Fetched %s raw objects -> %s flat features",
            len(raw_features),
            len(flat_features),
        )
        return flat_features

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

    async def set_feature(
        self, device: Device, feature: Feature, target_value: Any
    ) -> CommandResponse:
        """Set a value for a feature.

        Automatically resolves dependencies (other required parameters for the command)
        by looking them up in the device's feature list.

        Args:
            device: The device allowing context lookup for dependencies.
            feature: The feature to set.
            target_value: The value to write.

        Returns:
            CommandResponse indicating success or failure.

        Raises:
            ValueError: If feature is read-only or value is out of bounds.
        """
        if not feature.control:
            raise ValueError(f"Feature '{feature.name}' is read-only.")

        control = feature.control
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
        response_data = await self.connector.post(control.uri, payload)
        return CommandResponse.from_api(response_data)

    async def get_consumption(
        self,
        device: Device,
        start_dt: datetime | str,
        end_dt: datetime | str,
        metric: str = "summary",
        resolution: str = "1d",
    ) -> list[Feature]:
        """Fetch aggregated energy consumption.

        Args:
            device: The device to fetch data for.
            start_dt: Start time (datetime or ISO string).
            end_dt: End time (datetime or ISO string).
            metric: The data metric to fetch (e.g. 'summary', 'dhw').
            resolution: Data resolution (default: '1d').

        Returns:
            List of features representing the consumption data.
        """
        # Ensure string format
        if isinstance(start_dt, datetime):
            start_dt = start_dt.isoformat()
        if isinstance(end_dt, datetime):
            end_dt = end_dt.isoformat()

        properties = resolve_properties(metric)

        payload = {
            "gateway_id": device.gateway_serial,
            "device_id": str(device.id),
            "start_datetime": start_dt,
            "end_datetime": end_dt,
            "properties": properties,
            "resolution": resolution,
        }

        data = await self.connector.post(ENDPOINT_ANALYTICS_THERMAL, payload)

        return parse_consumption_response(data, properties)

    # ------------------------------------------------------------------
    # Private Helper Methods
    # ------------------------------------------------------------------

    def _build_devices_url(self, installation_id: str, gateway_serial: str) -> str:
        return (
            f"{ENDPOINT_INSTALLATIONS}/{installation_id}/gateways/"
            f"{gateway_serial}/devices"
        )

    def _build_features_url(
        self, device: Device, feature_name: str | None = None
    ) -> str:
        base = (
            f"{ENDPOINT_FEATURES}/{device.installation_id}/gateways/"
            f"{device.gateway_serial}/devices/{device.id}/features"
        )
        if feature_name:
            return f"{base}/{feature_name}"
        return f"{base}/filter"

    def _resolve_command_payload(
        self, device: Device, ctrl: "FeatureControl", target_value: Any
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

    def _validate_constraints(self, ctrl: "FeatureControl", value: Any) -> None:
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
        validators = {
            int: self._validate_numeric_constraints,
            float: self._validate_numeric_constraints,
            str: self._validate_string_constraints,
        }

        validator = validators.get(type(value))
        if validator:
            validator(ctrl, value)  # type: ignore

    def _validate_numeric_constraints(
        self, ctrl: "FeatureControl", value: int | float
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

    def _validate_enum_constraints(self, ctrl: "FeatureControl", value: Any) -> None:
        """Validate enum options."""
        if value not in ctrl.options:
            raise ValueError(f"Value {value} is not in allowed options: {ctrl.options}")

    def _validate_string_constraints(self, ctrl: "FeatureControl", value: str) -> None:
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
