import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from ._discovery import _DiscoveryAdapter
from .api import ViClient
from .auth import AbstractAuth
from .models import (
    CommandResponse,
    Device,
    Feature,
    FeatureControl,
    GatewayDeviceRefreshResult,
)
from .parsing import parse_feature_flat


class _FixtureDiscoveryAdapter:
    """Return deterministic discovery envelopes without authentication or HTTP."""

    def __init__(self, device_name: str) -> None:
        """Initialize the adapter for a selected fixture device."""
        self._device_name = device_name
        fixture_path = Path(__file__).parent / "fixtures" / "discovery.json"
        with fixture_path.open(encoding="utf-8") as file:
            self._data = cast(dict[str, dict[str, Any]], json.load(file))

    async def get_installations(self) -> dict[str, Any]:
        """Return the mock installation envelope."""
        envelope = self._data["installations"]
        installation = envelope["data"][0]
        installation["description"] = installation["description"].format(
            device_name=self._device_name
        )
        return envelope

    async def get_gateways(self) -> dict[str, Any]:
        """Return the mock gateway envelope."""
        return self._data["gateways"]


# Mapping of fixture names to device types
# This provides consistent device_type values for mock devices
DEVICE_TYPE_MAP: dict[str, str] = {
    "Vitocal151A": "heating",
    "Vitocal200": "heating",
    "Vitocal250A": "heating",
    "Vitocal252": "heating",
    "Vitocal300G": "heating",
    "Vitodens050W": "heating",
    "Vitodens200W": "heating",
    "Vitodens300W": "heating",
    "VitolaUniferral": "heating",
    "Vitopure350": "ventilation",
}


class MockViClient(ViClient):
    """A mock client that returns static responses from JSON files.

    Useful for testing, CLI usage without credentials, and development.
    """

    def __init__(self, device_name: str, auth: AbstractAuth | None = None) -> None:
        """Initialize the mock client.

        Args:
            device_name: The name of the mock device (e.g. "Vitodens200W").
                Must correspond to a file in the fixtures directory.
            auth: Ignored compatibility argument; mock clients do not authenticate.
        """
        self.device_name = device_name
        self._discovery_adapter: _DiscoveryAdapter = _FixtureDiscoveryAdapter(
            device_name
        )
        self._data_cache = None

    @staticmethod
    def get_available_mock_devices() -> list[str]:
        """Return a list of available mock device names."""
        fixtures_dir = Path(__file__).parent / "fixtures"
        if not fixtures_dir.exists():
            return []

        return sorted(
            file.stem
            for file in fixtures_dir.glob("*.json")
            if file.stem != "discovery"
        )

    def _load_data(self) -> dict[str, Any]:
        """Load the JSON data for the selected device.

        Returns:
            The parsed JSON data as a dictionary.

        Raises:
            FileNotFoundError: If the fixture file does not exist.
        """
        if self._data_cache:
            return self._data_cache

        fixtures_dir = Path(__file__).parent / "fixtures"
        file_path = fixtures_dir / f"{self.device_name}.json"

        if not file_path.exists():
            raise FileNotFoundError(
                f"Mock device file not found: {self.device_name}.json. "
                f"Available: {self.get_available_mock_devices()}"
            )

        with file_path.open(encoding="utf-8") as file:
            self._data_cache = json.load(file)

        return self._data_cache

    async def get_devices(
        self,
        installation_id: str,
        gateway_serial: str,
        include_features: bool = False,
        only_active_features: bool = False,
    ) -> list[Device]:
        """Return the mocked device as a typed model."""
        device = Device(
            id="0",
            gateway_serial=gateway_serial,
            installation_id=installation_id,
            model_id=self.device_name,
            device_type=DEVICE_TYPE_MAP.get(self.device_name, "heating"),
            status="connected",
        )

        if include_features:
            features = await self.get_features(
                device, only_enabled=only_active_features
            )
            device = replace(device, features=features)

        return [device]

    async def get_features(
        self,
        device: Device,
        only_enabled: bool = False,
        feature_names: list[str] | None = None,
    ) -> list[Feature]:
        """Return the list of features from the loaded JSON file.

        Args:
            device: The device object (context).
            only_enabled: If True, only return enabled features.
            feature_names: Optional whitelist of feature names.

        Returns:
            List of flattened Feature objects.
        """
        data = self._load_data()
        raw_features = data.get("data", [])

        # Parse ALL features to flat list
        all_features = []
        for raw_feature in raw_features:
            all_features.extend(parse_feature_flat(raw_feature))

        # Filter
        filtered = []
        for feature in all_features:
            if only_enabled and not feature.is_enabled:
                continue

            # Strict name matching (assuming feature_names are flat names)
            if feature_names and feature.name not in feature_names:
                continue

            filtered.append(feature)

        return filtered

    async def update_gateway_devices(
        self, devices: list[Device]
    ) -> GatewayDeviceRefreshResult:
        """Refresh gateway devices from fixtures without network access.

        Args:
            devices: Existing devices belonging to one installation and gateway.

        Returns:
            Refreshed devices in input order with no device-specific errors.

        Raises:
            ValueError: If devices span multiple scopes or contain duplicate IDs.
        """
        if not devices:
            return GatewayDeviceRefreshResult([], {})

        self._validate_gateway_devices(devices)
        updated_devices = []
        for device in devices:
            features = await self.get_features(device, only_enabled=True)
            enabled_and_ready_features = [
                feature for feature in features if feature.is_ready
            ]
            updated_devices.append(replace(device, features=enabled_and_ready_features))
        return GatewayDeviceRefreshResult(updated_devices, {})

    async def _execute_command(
        self,
        control: FeatureControl,
        payload: dict[str, Any],
    ) -> CommandResponse:
        """Mock execution of a command (Success).

        Args:
            control: The feature control block being executed.
            payload: Validated parameters for the command.

        Returns:
            A CommandResponse indicating success.
        """
        print(
            f"[MOCK] Executing command '{control.command_name}' for feature "
            f"'{control.parent_feature_name}' (param: {control.param_name}) "
            f"with params: {payload}"
        )
        return CommandResponse(success=True, reason="Mock Execution Success")
