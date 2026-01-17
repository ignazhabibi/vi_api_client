"""Viessmann API Client."""

import logging
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
from .models import CommandResponse, Device, Feature, Gateway, Installation

_LOGGER = logging.getLogger(__name__)


class ViClient:
    """Client for Viessmann Climate Solutions API."""

    def __init__(self, auth: AbstractAuth) -> None:
        """Initialize the client."""
        self.connector = ViConnector(auth)

    async def get_installations(self) -> list[Installation]:
        """Get list of installations."""
        _LOGGER.debug("Fetching installations...")
        installations_data = await self.connector.get(ENDPOINT_INSTALLATIONS)
        installations = [
            Installation.from_api(i) for i in installations_data.get("data", [])
        ]
        _LOGGER.debug(f"Found {len(installations)} installations")
        return installations

    async def get_gateways(self) -> list[Gateway]:
        """Get list of gateways."""
        _LOGGER.debug("Fetching gateways...")
        gateways_data = await self.connector.get(ENDPOINT_GATEWAYS)
        gateways = [Gateway.from_api(g) for g in gateways_data.get("data", [])]
        _LOGGER.debug(f"Found {len(gateways)} gateways")
        return gateways

    async def get_devices(
        self, installation_id: str, gateway_serial: str
    ) -> list[Device]:
        """Get devices as typed objects."""
        url = (
            f"{ENDPOINT_INSTALLATIONS}/{installation_id}/gateways/{gateway_serial}/devices"
        )
        devices_data = await self.connector.get(url)
        return [
            Device.from_api(d, gateway_serial, installation_id)
            for d in devices_data.get("data", [])
        ]

    async def get_features(
        self,
        device: Device,
        only_enabled: bool = False,
        feature_names: list[str] | None = None,
    ) -> list[Feature]:
        """Get features for a device as typed objects."""
        url = (
            f"{ENDPOINT_FEATURES}/{device.installation_id}/gateways/"
            f"{device.gateway_serial}/devices/{device.id}/features/filter"
        )
        payload = {"skipDisabled": only_enabled, "skipNotReady": only_enabled}
        if feature_names:
            payload["filter"] = feature_names

        _LOGGER.debug(
            f"Fetching features for device {device.id} (enabled={only_enabled})..."
        )
        features_data = await self.connector.post(url, payload)
        features = [Feature.from_api(f) for f in features_data.get("data", [])]
        _LOGGER.debug(f"Fetched {len(features)} features for device {device.id}")
        return features

    async def get_feature(self, device: Device, feature_name: str) -> Feature:
        """Get a specific feature as typed object."""
        url = (
            f"{ENDPOINT_FEATURES}/{device.installation_id}/gateways/"
            f"{device.gateway_serial}/devices/{device.id}/features/{feature_name}"
        )
        feature_data = await self.connector.get(url)  # Uses explicit GET
        return Feature.from_api(feature_data.get("data", {}))

    async def get_full_installation_status(
        self, installation_id: str, only_enabled: bool = True
    ) -> list[Device]:
        """Fetch full status of an installation (Gateways -> Devices -> Features)."""
        gateways = await self.get_gateways()
        all_devices = []

        for gw in gateways:
            gw_serial = gw.serial
            devices = await self.get_devices(installation_id, gw_serial)

            for device in devices:
                # Only fetch enabled features for full status
                features = await self.get_features(device, only_enabled=only_enabled)
                # Create new Device instance (Device is immutable)
                device_with_features = replace(device, features=features)
                all_devices.append(device_with_features)

        return all_devices

    async def update_device(self, device: Device, only_enabled: bool = True) -> Device:
        """Refresh the features of an existing device."""
        features = await self.get_features(device, only_enabled=only_enabled)

        # Return new device instance (immutable update)
        return replace(device, features=features)

    async def execute_command(
        self,
        feature: Feature,
        command_name: str,
        params: dict[str, Any] | None = None,
        **kwargs,
    ) -> CommandResponse:
        """Execute a command on a feature."""
        _LOGGER.debug(
            f"Executing command '{command_name}' on '{feature.name}' "
            f"with params: {params}"
        )
        if params is None:
            params = {}
        final_params = params.copy()
        final_params.update(kwargs)

        if command_name not in feature.commands:
            raise ValueError(
                f"Command '{command_name}' not found in feature '{feature.name}'. "
                f"Available: {list(feature.commands.keys())}"
            )

        cmd = feature.commands[command_name]

        if not cmd.is_executable:
            raise ValueError(
                f"Command '{command_name}' is currently not executable "
                "(isExecutable=False)."
            )

        if not cmd.uri:
            raise ValueError(f"Command '{command_name}' has no URI definition.")

        cmd.validate(final_params)

        response_data = await self.connector.post(cmd.uri, final_params)
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
            device: The target Device.
            start_dt: Start time (datetime or ISO string).
            end_dt: End time (datetime or ISO string).
            metric: 'summary', 'total', 'heating', or 'dhw'.
            resolution: Data resolution (default: '1d').

        Returns:
            List of analytics Features.
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
