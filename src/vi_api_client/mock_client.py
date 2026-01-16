
import json
import os
from typing import Any, Dict, List, Optional

from .api import Client
from .auth import AbstractAuth
from .exceptions import ViNotFoundError
from .models import Feature, Device, Installation, Gateway


class MockAuth(AbstractAuth):
    """Mock authentication provider."""
    
    async def async_get_access_token(self) -> str:
        return "mock_token"

    def __init__(self, websession: Any = None) -> None:
        # Standard AbstractAuth expects a websession, but we don't use it in Mock
        super().__init__(websession)

class MockViClient(Client):
    """
    A mock client that returns static responses from JSON files.
    Useful for testing, CLI usage without credentials, and development.
    """

    def __init__(self, device_name: str, auth: Optional[AbstractAuth] = None) -> None:
        """
        Initialize the mock client.
        
        :param device_name: The name of the mock device (e.g. "Vitodens200W").
                            Must correspond to a file in the fixtures directory.
        :param auth: Optional auth provider (not used for logic, but kept for interface compatibility).
        """
        # Pass dummy auth if none provided, to satisfy superclass
        super().__init__(auth or MockAuth())
        self.device_name = device_name
        self._data_cache = None

    @staticmethod
    def get_available_mock_devices() -> List[str]:
        """Return a list of available mock device names."""
        fixtures_dir = os.path.join(os.path.dirname(__file__), "fixtures")
        if not os.path.exists(fixtures_dir):
            return []
        
        files = [f for f in os.listdir(fixtures_dir) if f.endswith(".json")]
        # Return sorted names without extension
        return sorted([os.path.splitext(f)[0] for f in files])

    def _load_data(self) -> Dict[str, Any]:
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

        with open(file_path, "r", encoding="utf-8") as f:
            self._data_cache = json.load(f)
        
        return self._data_cache

    async def get_installations(self) -> List[Installation]:
        """Return a mock installation."""
        return [Installation(
            id=99999,
            description=f"Mock Installation ({self.device_name})",
            alias="Mock Home",
            address={"city": "Mock City"}
        )]

    async def get_gateways(self) -> List[Gateway]:
        """Return a mock gateway."""
        return [Gateway(
            serial="MOCK_GATEWAY_SERIAL",
            version="1.0.0",
            status="connected",
            installation_id=99999
        )]

    async def get_devices(self, installation_id: int, gateway_serial: str) -> List[Device]:
        """Return the mocked device as a typed model."""
        # In the real API, get_devices returns a list of device summaries.
        # Here we just wrap our single mock device data into a Device model
        
        # We need the base JSON to be loaded, but Device.from_api expects the 'entities' payload usually?
        # Actually in api.py: Device.from_api(d, ...) where d is from /devices endpoint.
        # But MockClient loads 'data' which is usually the FEATURES list or full response?
        # A typical 'devices' response is different from 'features' response.
        # But for MOCK simplicity, we construct a fake Device object using the known name/id.
        
        # NOTE: self._load_data() gives us FEATURES in the current mock setup (it loads vitodens...json).
        # We don't have a specific "device list" mock file yet.
        # So we fabricate a Device object based on the filename we loaded.
        
        return [Device(
            id="0", # Using a fixed mock device ID
            gateway_serial=gateway_serial,
            installation_id=installation_id,
            model_id=self.device_name, # Use the model name passed to constructor
            device_type="heating" if "heating" in self.device_name.lower() or "vitodens" in self.device_name.lower() else "unknown",
            status="connected"
        )]

    async def get_features(
        self, 
        installation_id: int, 
        gateway_serial: str, 
        device_id: str,
        only_enabled: bool = False,
        feature_names: List[str] = None
    ) -> List[Feature]:
        """Return the list of features from the loaded JSON file."""
        data = self._load_data()
        raw_features = data.get("data", [])
        
        # Apply filters to mimic server behavior
        if only_enabled:
            raw_features = [f for f in raw_features if f.get("isEnabled")]
            
        if feature_names:
            raw_features = [f for f in raw_features if f.get("feature") in feature_names]
            
        return [Feature.from_api(f) for f in raw_features]
    
    # We can rely on the superclass implementation for:
    # - get_feature (it might be inefficient as it calls get_features, but fine for mock)
    # - get_features_with_values
    # - get_features_models (inherited as now deleted or just uses new get_features?)
    # Wait, api.py deleted get_features_models. So we are good.    #
    # However, get_feature in the base class does a specific request. 
    # We should override it to query our local list.
    
    async def get_feature(
        self, installation_id: int, gateway_serial: str, device_id: str, feature_name: str
    ) -> Dict[str, Any]:
        """Get a specific feature from the local list (Return RAW Dict)."""
        data = self._load_data().get("data", [])
        for f in data:
            if f.get("feature") == feature_name:
                return f
        
        raise ViNotFoundError(f"Feature '{feature_name}' not found in mock device.")

    # get_today_consumption depends on Analytics API.
    # We don't have analytics samples yet, so we can return empty or mock data.
    # For now, let's log a warning or return empty.
    async def get_today_consumption(
        self, gateway_serial: str, device_id: str, metric: str = "summary"
    ) -> Any:
        # Mock analytics support could be added later.
        # Returning empty list or None is safer than crashing.
        return []

    async def execute_command(
        self,
        feature: Any, # Use Any to avoid circular import or redefine Type
        command_name: str,
        params: Dict[str, Any] = {},
        **kwargs
    ) -> Dict[str, Any]:
        """Mock execution of a command."""
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
             raise ValueError(f"Command '{command_name}' is currently not executable (isExecutable=False).")

        if not cmd.uri:
             raise ValueError(f"Command '{command_name}' has no URI definition.")

        # Validate parameters using the command definition.
        cmd.validate(final_params)

        print(f"[MOCK] Executing command '{command_name}' on feature '{feature.name}' with params: {final_params}")
        # In a real mock, we could update the local JSON/cache to reflect the change.
        # For now, just return success.
        return {"success": True, "reason": "Mock Execution"}
