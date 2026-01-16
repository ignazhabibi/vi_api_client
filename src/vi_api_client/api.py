"""Viessmann API Client."""

from typing import Any, Dict, List, Union


from .auth import AbstractAuth
from .connection import ViConnector
from .const import ENDPOINT_INSTALLATIONS, ENDPOINT_GATEWAYS, ENDPOINT_ANALYTICS_THERMAL, API_BASE_URL
from .exceptions import ViConnectionError, ViNotFoundError
from .models import Device, Feature, Installation, Gateway


class Client:
    """Client for Viessmann Climate Solutions API."""

    def __init__(self, auth: AbstractAuth) -> None:
        """Initialize the client."""
        self.connector = ViConnector(auth)

    async def get_installations(self) -> List[Installation]:
        """Get list of installations."""
        data = await self.connector.get(ENDPOINT_INSTALLATIONS)
        return [Installation.from_api(i) for i in data.get("data", [])]

    async def get_gateways(self) -> List[Gateway]:
        """Get list of gateways."""
        data = await self.connector.get(ENDPOINT_GATEWAYS)
        return [Gateway.from_api(g) for g in data.get("data", [])]


    async def get_features(
        self, 
        installation_id: int, 
        gateway_serial: str, 
        device_id: str,
        only_enabled: bool = False,
        feature_names: List[str] = None
    ) -> List[Feature]:
        """
        Get features for a device as typed objects.
        Uses the efficient POST filter endpoint if filters are applied.
        """
        raw_features = []
        
        # Optimized Path: Server-side filtering
        if only_enabled or feature_names:
            url = f"/iot/v2/features/installations/{installation_id}/gateways/{gateway_serial}/devices/{device_id}/features/filter"
            payload = {
                "skipDisabled": only_enabled,
                "skipNotReady": only_enabled
            }
            if feature_names:
                payload["filter"] = feature_names
                
            data = await self.connector.post(url, payload)
            raw_features = data.get("data", [])
        else:
            # Legacy Path: Full Dump via GET
            url = f"/iot/v2/features/installations/{installation_id}/gateways/{gateway_serial}/devices/{device_id}/features"
            data = await self.connector.get(url)
            raw_features = data.get("data", [])
            
        return [Feature.from_api(f) for f in raw_features]
    
    async def get_feature(
        self, installation_id: int, gateway_serial: str, device_id: str, feature_name: str
    ) -> Dict[str, Any]:
        """Get a specific feature."""
        url = f"/iot/v2/features/installations/{installation_id}/gateways/{gateway_serial}/devices/{device_id}/features/{feature_name}"
        data = await self.connector.get(url)
        return data.get("data", {})

    async def get_devices(self, installation_id: int, gateway_serial: str) -> List[Device]:
        """Get devices as typed objects."""
        url = f"{ENDPOINT_INSTALLATIONS}/{installation_id}/gateways/{gateway_serial}/devices"
        data = await self.connector.get(url)
        raw_devices = data.get("data", [])
        return [
            Device.from_api(d, gateway_serial, installation_id) 
            for d in raw_devices
        ]

    async def get_full_installation_status(
        self, installation_id: int, only_enabled: bool = True
    ) -> List[Device]:
        """Fetch full status of an installation (Gateways -> Devices -> Features)."""
        gateways = await self.get_gateways()
        all_devices = []

        for gw in gateways:
            gw_serial = gw.serial
            devices = await self.get_devices(installation_id, gw_serial)

            for device in devices:
                # Optimized: Only fetch enabled features for full status
                features = await self.get_features(installation_id, gw_serial, device.id, only_enabled=only_enabled)
                # Create a new Device instance with populated features (Device is immutable)
                from dataclasses import replace
                device = replace(device, features=features)
                all_devices.append(device)

        return all_devices
    
    async def update_device(self, device: Device, only_enabled: bool = True) -> Device:
        """
        Refresh the features of an existing device.
        This is the most efficient way to poll for updates, as it reuses
        the known Gateway/Installation IDs and only fetches changed data.
        """
        # Fetch fresh features
        features = await self.get_features(
            device.installation_id, 
            device.gateway_serial, 
            device.id, 
            only_enabled=only_enabled
        )
        
        # Return new device instance (immutable update)
        from dataclasses import replace
        return replace(device, features=features)
    
    async def execute_command(
        self,
        feature: Feature,
        command_name: str,
        params: Dict[str, Any] = {},
        **kwargs
    ) -> Dict[str, Any]:
        """Execute a command on a feature."""
        final_params = params.copy()
        final_params.update(kwargs)

        if command_name not in feature.commands:
            raise ValueError(
                f"Command '{command_name}' not found in feature '{feature.name}'. "
                f"Available: {list(feature.commands.keys())}"
            )
            
        cmd = feature.commands[command_name]
        
        if not cmd.is_executable:
             raise ValueError(f"Command '{command_name}' is currently not executable (isExecutable=False).")

        if not cmd.uri:
             raise ValueError(f"Command '{command_name}' has no URI definition.")

        cmd.validate(final_params)

        return await self.connector.post(cmd.uri, final_params)

    async def get_today_consumption(
        self,
        gateway_serial: str,
        device_id: str,
        metric: str = "summary"
    ) -> Union[Feature, List[Feature]]:
        """Fetch energy consumption for the current day."""
        from .analytics import get_today_timerange, resolve_properties, parse_consumption_response
        
        start_dt, end_dt = get_today_timerange()
        properties = resolve_properties(metric)
        
        raw_data = await self.get_aggregated_consumption(
            gateway_serial, device_id, start_dt, end_dt, properties, resolution="1d"
        )
        
        features = parse_consumption_response(raw_data, properties)
            
        if metric != "summary" and len(features) == 1:
            return features[0]
            
        return features

    async def get_aggregated_consumption(
        self,
        gateway_serial: str,
        device_id: str,
        start_dt: str,
        end_dt: str,
        properties: List[str],
        resolution: str = "1d"
    ) -> Dict[str, Any]:
        """Fetch aggregated energy data from the Analytics API."""
        payload = {
            "gateway_id": gateway_serial,
            "device_id": str(device_id),
            "start_datetime": start_dt,
            "end_datetime": end_dt,
            "properties": properties,
            "resolution": resolution
        }
        
        return await self.connector.post(ENDPOINT_ANALYTICS_THERMAL, payload)
