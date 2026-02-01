import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .analytics import parse_consumption_response, resolve_properties
from .api import ViClient
from .auth import AbstractAuth
from .models import (
    CommandResponse,
    Device,
    Feature,
    FeatureControl,
    Gateway,
    Installation,
)
from .parsing import parse_feature_flat


class MockAuth(AbstractAuth):
    """Mock authentication provider."""

    async def async_get_access_token(self) -> str:
        """Return a mock access token."""
        return "mock_token"

    def __init__(self, websession: Any = None) -> None:
        """Initialize mock auth."""
        # Standard AbstractAuth expects a websession, but we don't use it in Mock
        super().__init__(websession)


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
            auth: Optional auth provider (not used for logic,
                but kept for interface compatibility).
        """
        # Pass dummy auth if none provided, to satisfy superclass
        super().__init__(auth or MockAuth())
        self.device_name = device_name
        self._data_cache = None

    @staticmethod
    def get_available_mock_devices() -> list[str]:
        """Return a list of available mock device names."""
        fixtures_dir = Path(__file__).parent / "fixtures"
        if not fixtures_dir.exists():
            return []

        files = [file.name for file in fixtures_dir.glob("*.json")]
        # Return sorted names without extension
        return sorted([Path(file).stem for file in files])

    def _load_analytics_data(self) -> dict[str, Any] | None:
        """Load optional analytics fixture if available.

        Returns:
            Analytics data dict if fixture exists, None otherwise.
        """
        fixtures_dir = Path(__file__).parent / "fixtures"
        file_path = fixtures_dir / f"{self.device_name}_analytics.json"

        if not file_path.exists():
            return None

        with file_path.open(encoding="utf-8") as file:
            return json.load(file)

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

    async def get_installations(self) -> list[Installation]:
        """Return a mock installation."""
        return [
            Installation(
                id="99999",
                description=f"Mock Installation ({self.device_name})",
                alias="Mock Home",
                address={"city": "Mock City"},
            )
        ]

    async def get_gateways(self) -> list[Gateway]:
        """Return a mock gateway."""
        return [
            Gateway(
                serial="MOCK_GATEWAY_SERIAL",
                version="1.0.0",
                status="connected",
                installation_id="99999",
            )
        ]

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

    async def _execute_command(
        self,
        ctrl: "FeatureControl",
        params: dict[str, Any],
    ) -> CommandResponse:
        """Mock execution of a command (Success).

        Args:
            ctrl: The feature control block being executed.
            params: Validated parameters for the command.

        Returns:
            A CommandResponse indicating success.
        """
        print(
            f"[MOCK] Executing command '{ctrl.command_name}' for feature "
            f"'{ctrl.parent_feature_name}' (param: {ctrl.param_name}) "
            f"with params: {params}"
        )
        return CommandResponse(success=True, reason="Mock Execution Success")

    # get_today_consumption depends on Analytics API.
    async def get_consumption(
        self,
        device: Device,
        start_dt: datetime | str,
        end_dt: datetime | str,
        metric: str = "summary",
        resolution: str = "1d",
    ) -> list[Feature]:
        """Get consumption data from analytics fixture if available.

        Args:
            device: The device object (context).
            start_dt: Start time (not used in mock).
            end_dt: End time (not used in mock).
            metric: The data metric to fetch (e.g. 'summary', 'dhw').
            resolution: Data resolution (not used in mock).

        Returns:
            List of analytics features if fixture exists, empty list otherwise.
        """
        analytics_data = self._load_analytics_data()
        if analytics_data:
            properties = resolve_properties(metric)
            return parse_consumption_response(analytics_data, properties)
        return []
