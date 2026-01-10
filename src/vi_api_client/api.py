"""Viessmann API Client."""

from typing import Any, Dict, List

from .auth import AbstractAuth
from .const import ENDPOINT_INSTALLATIONS, ENDPOINT_GATEWAYS, ENDPOINT_ANALYTICS_THERMAL, API_BASE_URL
from .exceptions import VitoConnectionError
from .models import Device, Feature

class Client:
    """Client for Viessmann API."""

    def __init__(self, auth: AbstractAuth) -> None:
        """Initialize the client."""
        self.auth = auth

    async def get_installations(self) -> List[Dict[str, Any]]:
        """Get list of installations."""
        url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
        async with await self.auth.request("GET", url) as resp:
            if resp.status != 200:
                raise VitoConnectionError(f"Error fetching installations: {resp.status}")
            data = await resp.json()
            return data.get("data", [])

    async def get_gateways(self) -> List[Dict[str, Any]]:
        """Get list of gateways."""
        url = f"{API_BASE_URL}{ENDPOINT_GATEWAYS}"
        async with await self.auth.request("GET", url) as resp:
            if resp.status != 200:
                raise VitoConnectionError(f"Error fetching gateways: {resp.status}")
            data = await resp.json()
            return data.get("data", [])

    async def get_devices(self, installation_id: int, gateway_serial: str) -> List[Dict[str, Any]]:
        """Get devices for a specific gateway."""
        url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}/{installation_id}/gateways/{gateway_serial}/devices"
        async with await self.auth.request("GET", url) as resp:
            if resp.status != 200:
                # 404 might mean no devices or wrong IDs
                text = await resp.text()
                raise VitoConnectionError(f"Error fetching devices: {resp.status} - {text}")
            data = await resp.json()
            return data.get("data", [])

    async def get_features(self, installation_id: int, gateway_serial: str, device_id: str) -> List[Dict[str, Any]]:
        """Get all features for a device."""
        url = f"{API_BASE_URL}/iot/v2/features/installations/{installation_id}/gateways/{gateway_serial}/devices/{device_id}/features"
        async with await self.auth.request("GET", url) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise VitoConnectionError(f"Error fetching features: {resp.status} - {text}")
            data = await resp.json()
            # The API response structure for features usually wraps them in 'data' list
            return data.get("data", [])
    
    async def get_feature(
        self, installation_id: int, gateway_serial: str, device_id: str, feature_name: str
    ) -> Dict[str, Any]:
        """Get a specific feature."""
        # Note: Sending a request for a specific feature by name is usually done by appending it to the URL
        # e.g. .../features/heating.circuits.0
        url = f"{API_BASE_URL}/iot/v2/features/installations/{installation_id}/gateways/{gateway_serial}/devices/{device_id}/features/{feature_name}"
        async with await self.auth.request("GET", url) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise VitoConnectionError(f"Error fetching feature {feature_name}: {resp.status} - {text}")
            data = await resp.json()
            return data.get("data", {})

    async def get_enabled_features(self, installation_id: int, gateway_serial: str, device_id: str) -> List[Dict[str, Any]]:
        """Get only enabled features for a device.
        
        This includes the full feature object with properties and values.
        """
        features = await self.get_features(installation_id, gateway_serial, device_id)
        return [f for f in features if f.get("isEnabled")]

    async def get_features_with_values(
        self, installation_id: int, gateway_serial: str, device_id: str, only_enabled: bool = True
    ) -> List[Dict[str, str]]:
        """Get features with their formatted values nicely extracted.
        
        Returns a list of dicts with 'name' and 'value' keys.
        """
        if only_enabled:
            features = await self.get_enabled_features(installation_id, gateway_serial, device_id)
        else:
            features = await self.get_features(installation_id, gateway_serial, device_id)
            
        results = []
        for f in features:
            name = f.get("feature")
            props = f.get("properties", {})
            found_vals = []
            
            for key, val_item in props.items():
                if isinstance(val_item, dict) and "value" in val_item:
                    v = val_item.get("value")
                    u = val_item.get("unit", "")
                    if key == "value" or key == "status":
                        found_vals.append(f"{v} {u}".strip())
                    else:
                        found_vals.append(f"{key}: {v} {u}".strip())
                        
            val_str = ", ".join(found_vals)
            results.append({"name": name, "value": val_str})
            
        return results

    async def get_features_models(
        self, installation_id: int, gateway_serial: str, device_id: str
    ) -> List[Feature]:
        """Get all features as typed objects."""
        features = await self.get_features(installation_id, gateway_serial, device_id)
        return [Feature.from_api(f) for f in features]
        
    async def get_devices_models(
        self, installation_id: int, gateway_serial: str
    ) -> List[Device]:
        """Get devices as typed objects."""
        devices = await self.get_devices(installation_id, gateway_serial)
        return [
            Device.from_api(d, gateway_serial, installation_id) 
            for d in devices
        ]

    async def get_full_installation_status(
        self, installation_id: int
    ) -> List[Device]:
        """Fetch full status of an installation (Gateways -> Devices -> Features).
        
        This is designed for the UpdateCoordinator pattern to fetch everything in one go.
        """
        gateways = await self.get_gateways()
        all_devices = []
        
        for gw in gateways:
            gw_serial = gw["serial"]
            # Get devices for this gateway
            devices = await self.get_devices_models(installation_id, gw_serial)
            
            for device in devices:
                # Fetch features for each device
                features = await self.get_features_models(installation_id, gw_serial, device.id)
                device.features = features
                all_devices.append(device)
                
        return all_devices
    
    async def get_aggregated_consumption(
        self,
        gateway_serial: str,
        device_id: str,
        start_dt: str,
        end_dt: str,
        properties: List[str],
        resolution: str = "1d"
    ) -> Dict[str, Any]:
        """Fetch aggregated energy data from the Analytics API.
        
        This endpoint provides historical and potentially more accurate consumption/production data 
        than the standard feature properties.
        
        :param gateway_serial: The serial number of the gateway.
        :param device_id: The ID of the device (e.g. "0").
        :param start_dt: Start datetime in ISO 8601 format (e.g. "2023-01-01T00:00:00").
        :param end_dt: End datetime in ISO 8601 format.
        :param properties: List of feature names to fetch (e.g. ["heating.power.consumption.total"]).
        :param resolution: Data resolution, default "1d".
        :return: JSON response containing the data lake result.
        """
        url = f"{API_BASE_URL}{ENDPOINT_ANALYTICS_THERMAL}"
        
        payload = {
            "gateway_id": gateway_serial,
            "device_id": str(device_id),
            "start_datetime": start_dt,
            "end_datetime": end_dt,
            "properties": properties,
            "resolution": resolution
        }
        
        async with await self.auth.request("POST", url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise VitoConnectionError(f"Error fetching analytics: {resp.status} - {text}")
            return await resp.json()
