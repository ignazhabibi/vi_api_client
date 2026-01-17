import json
import os
from datetime import datetime
from typing import Any

from .api import ViClient
from .auth import AbstractAuth
from .exceptions import ViNotFoundError
from .models import CommandResponse, Device, Feature, Gateway, Installation


class MockAuth(AbstractAuth):
    """Mock authentication provider."""

    async def async_get_access_token(self) -> str:
        """Return a mock access token."""
        return "mock_token"

    def __init__(self, websession: Any = None) -> None:
        """Initialize mock auth."""
        # Standard AbstractAuth expects a websession, but we don't use it in Mock
        super().__init__(websession)


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
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        if not os.path.exists(fixtures_dir):
            return []

        files = [f for f in os.listdir(fixtures_dir) if f.endswith(".json")]
        # Return sorted names without extension
        return sorted([os.path.splitext(f)[0] for f in files])

    def _load_data(self) -> dict[str, Any]:
        """Load the JSON data for the selected device."""
        if self._data_cache:
            return self._data_cache

        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        file_path = os.path.join(fixtures_dir, f"{self.device_name}.json")

        if not os.path.exists(file_path):
            raise FileNotFoundError(
                f"Mock device file not found: {self.device_name}.json. "
                f"Available: {self.get_available_mock_devices()}"
            )

        with open(file_path, encoding="utf-8") as f:
            self._data_cache = json.load(f)

        return self._data_cache

    async def get_installations(self) -> list[Installation]:
        """Return a mock installation."""
        return [
            Installation(
                id=99999,
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
        self, installation_id: str, gateway_serial: str
    ) -> list[Device]:
        """Return the mocked device as a typed model."""
        # In the real API, get_devices returns a list of device summaries.
        # Since our mock data source (fixtures) only contains feature snapshots,
        # we synthesize a Device object using the known mock file name and defaults.
        return [
            Device(
                id="0",  # Using a fixed mock device ID
                gateway_serial=gateway_serial,
                installation_id=installation_id,
                model_id=self.device_name,  # Use the model name passed to constructor
                device_type="heating"
                if "heating" in self.device_name.lower()
                or "vitodens" in self.device_name.lower()
                else "unknown",
                status="connected",
            )
        ]

    async def get_features(
        self,
        device: Device,
        only_enabled: bool = False,
        feature_names: list[str] | None = None,
    ) -> list[Feature]:
        """Return the list of features from the loaded JSON file."""
        data = self._load_data()
        raw_features = data.get("data", [])

        # Apply filters to mimic server behavior
        if only_enabled:
            raw_features = [f for f in raw_features if f.get("isEnabled")]

        if feature_names:
            raw_features = [
                f for f in raw_features if f.get("feature") in feature_names
            ]

        return [Feature.from_api(f) for f in raw_features]


    async def get_feature(self, device: Device, feature_name: str) -> Feature:
        """Get a specific feature from the local list (Return Feature Model)."""
        data = self._load_data().get("data", [])
        for f in data:
            if f.get("feature") == feature_name:
                return Feature.from_api(f)

        raise ViNotFoundError(f"Feature '{feature_name}' not found in mock device.")

    # get_today_consumption depends on Analytics API.
    # Analytics data is not currently mocked; returns empty list.
    async def get_consumption(
        self,
        device: Device,
        start_dt: datetime | str,
        end_dt: datetime | str,
        metric: str = "summary",
        resolution: str = "1d",
    ) -> list[Feature]:
        """Get consumption data (Mock)."""
        # Mock analytics support could be added later.
        # Returning empty list or None is safer than crashing.
        return []

    async def execute_command(
        self,
        feature: Any,  # Use Any to avoid circular import or redefine Type
        command_name: str,
        params: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """Mock execution of a command."""
        if params is None:
            params = {}
        final_params = params.copy()
        final_params.update(kwargs)
        # Validate command exists
        if command_name not in feature.commands:
            raise ValueError(
                f"Command '{command_name}' not found in feature '{feature.name}'. "
                f"Available: {list(feature.commands.keys())}"
            )

        cmd = feature.commands[command_name]

        # Check if command is executable
        if not cmd.is_executable:
            raise ValueError(
                f"Command '{command_name}' is currently not executable (isExecutable=False)."  # noqa: E501
            )

        if not cmd.uri:
            raise ValueError(f"Command '{command_name}' has no URI definition.")

        # Validate parameters using the command definition.
        cmd.validate(final_params)

        print(
            f"[MOCK] Executing command '{command_name}' on feature '{feature.name}' with params: {final_params}"  # noqa: E501
        )
        # Stateless mock execution; does not update internal state.
        return CommandResponse(success=True, reason="Mock Execution")
