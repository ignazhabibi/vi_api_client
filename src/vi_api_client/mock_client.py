import json
from pathlib import Path
from typing import Any, cast

from ._adapter import _CommandAdapter, _DiscoveryAdapter
from .api import ViClient
from .models import (
    Device,
    FeatureControl,
)


class _FixtureDiscoveryAdapter:
    """Return deterministic client envelopes without authentication or HTTP."""

    def __init__(self, device_name: str) -> None:
        """Initialize the adapter for a selected fixture device."""
        self._device_name = device_name
        fixture_path = Path(__file__).parent / "fixtures" / "discovery.json"
        with fixture_path.open(encoding="utf-8") as file:
            self._discovery_data = cast(dict[str, dict[str, Any]], json.load(file))
        self._feature_data: dict[str, Any] | None = None

    async def get_installations(self) -> dict[str, Any]:
        """Return the mock installation envelope."""
        return {
            "data": [
                {
                    **self._discovery_data["installations"]["data"][0],
                    "description": self._discovery_data["installations"]["data"][0][
                        "description"
                    ].format(device_name=self._device_name),
                }
            ]
        }

    async def get_gateways(self) -> dict[str, Any]:
        """Return the mock gateway envelope."""
        return self._discovery_data["gateways"]

    async def get_devices(
        self, installation_id: str, gateway_serial: str
    ) -> dict[str, Any]:
        """Return the selected fixture as one deterministic device."""
        return {
            "data": [
                {
                    "id": "0",
                    "modelId": self._device_name,
                    "deviceType": DEVICE_TYPE_MAP.get(self._device_name, "heating"),
                    "status": "connected",
                }
            ]
        }

    async def get_features(
        self, device: Device, payload: dict[str, bool | list[str]]
    ) -> dict[str, Any]:
        """Return the selected fixture's raw feature envelope."""
        return self._load_feature_data()

    async def get_gateway_features(
        self, devices: list[Device], payload: dict[str, bool]
    ) -> dict[str, Any]:
        """Return the selected fixture's raw gateway-scoped feature envelope."""
        return self._load_feature_data()

    def _load_feature_data(self) -> dict[str, Any]:
        """Load the selected fixture feature envelope once."""
        if self._feature_data is None:
            fixture_path = (
                Path(__file__).parent / "fixtures" / f"{self._device_name}.json"
            )
            with fixture_path.open(encoding="utf-8") as file:
                self._feature_data = cast(dict[str, Any], json.load(file))
        return self._feature_data


class _FixtureCommandAdapter:
    """Return deterministic command responses without modifying fixtures."""

    async def execute_command(
        self, control: FeatureControl, parameters: dict[str, Any]
    ) -> dict[str, Any]:
        """Return a successful fixture command response."""
        print(
            f"[MOCK] Executing command '{control.command_name}' for feature "
            f"'{control.parent_feature_name}' (param: {control.param_name}) "
            f"with params: {parameters}"
        )
        return {"data": {"success": True, "reason": "Mock Execution Success"}}


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

    def __init__(self, device_name: str) -> None:
        """Initialize the mock client.

        Args:
            device_name: The name of the mock device (e.g. "Vitodens200W").
                Must correspond to a file in the fixtures directory.
        """
        self.device_name = device_name
        self._discovery_adapter: _DiscoveryAdapter = _FixtureDiscoveryAdapter(
            device_name
        )
        self._command_adapter: _CommandAdapter = _FixtureCommandAdapter()

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
