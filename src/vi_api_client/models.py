"""Data models for Viessmann API objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from .validation import validate_command_params

# Priority keys as a Set for faster lookup (O(1)) in checks
VALUE_PRIORITY_KEYS = [
    "value", "status", "active", "enabled", "strength", "entries",
    "day", "week", "month", "year"
]
# For set operations (subset checks)
VALUE_PRIORITY_SET = set(VALUE_PRIORITY_KEYS)

@dataclass(frozen=True)
class Command:
    """Representation of a generic command on a feature."""
    name: str
    uri: str
    is_executable: bool
    params: Dict[str, Any] = field(default_factory=dict)
    
    def validate(self, params: Dict[str, Any]) -> None:
        """Validate parameters."""
        validate_command_params(self.name, self.params, params)

    @classmethod
    def from_api(cls, name: str, data: Dict[str, Any]) -> Command:
        return cls(
            name=name,
            uri=data.get("uri", ""),
            is_executable=data.get("isExecutable", True),
            params=data.get("params", {})
        )

@dataclass(frozen=True)
class Installation:
    """Representation of an installation."""
    id: str
    description: str
    alias: str
    address: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> Installation:
        return cls(
            id=str(data.get("id", "")),
            description=data.get("description", ""),
            alias=data.get("alias", ""),
            address=data.get("address", {})
        )


@dataclass(frozen=True)
class Feature:
    """Representation of a Viessmann feature."""
    
    name: str
    properties: Dict[str, Any]
    is_enabled: bool
    is_ready: bool
    commands: Dict[str, Command] = field(default_factory=dict)
    
    @property
    def value(self) -> Union[str, int, float, bool, list, None]:
        """Extract the main value."""
        data = self._primary_data
        
        if data is None:
            return None
            
        # Standard Case: {"value": 10}
        if isinstance(data, dict) and "value" in data:
            return data["value"]
            
        # Edge Case: Raw value or List (History)
        return data

    @property
    def unit(self) -> Optional[str]:
        """Extract unit."""
        # 1. Try to get unit from the primary data object (nested case)
        # e.g. properties={'status': {'value': 'ok', 'unit': 'stat'}}
        data = self._primary_data
        if isinstance(data, dict):
            return data.get("unit")
            
        # 2. Fallback: Check if unit exists as a direct property (flat case)
        # e.g. properties={'value': 10, 'unit': 'celsius'}
        if "unit" in self.properties:
            return self.properties["unit"]
            
        return None

    def expand(self) -> List["Feature"]:
        """Expand complex features into a list of simple scalar features."""
        ignore_keys = {"unit", "type", "displayValue", "links"}
        
        # Get actual data keys
        data_keys = {k for k in self.properties.keys() if k not in ignore_keys}
        
        if not data_keys:
            return []

        # Efficiently check if feature is scalar using set operations.
        # If yes: Do not expand (we use .value).
        if data_keys.issubset(VALUE_PRIORITY_SET):
            return [self]

        # Otherwise: It is a complex object (e.g. curve has slope & shift) -> Expand
        flattened = []
        # Sort iteration to ensure deterministic behavior
        for key in sorted(data_keys):
            flattened.append(self._create_sub_feature(key, self.properties[key]))
            
        return flattened

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> Feature:
        return cls(
            name=data.get("feature", ""),
            properties=data.get("properties", {}),
            is_enabled=data.get("isEnabled", False),
            is_ready=data.get("isReady", False),
            commands={
                name: Command.from_api(name, cmd_data) 
                for name, cmd_data in data.get("commands", {}).items()
            }
        )

    # --- Private / Internal Helpers ---

    @property
    def _primary_data(self) -> Union[Dict[str, Any], Any, None]:
        """
        Internal Helper: Finds the 'main' data object based on priority.
        Used by both .value and .unit to avoid double-looping.
        """
        if not self.properties:
            return None
        
        # Search for primary value key in properties.
        for key in VALUE_PRIORITY_KEYS:
            if key in self.properties:
                return self.properties[key]
        return None

    def _create_sub_feature(self, suffix: str, val_obj: Any) -> Feature:
        """Helper to create a virtual sub-feature."""
        # Normalize val_obj to be a dict with a 'value' key
        # Handle cases where val_obj is a dict but doesn't have 'value' (e.g. raw dict)
        if isinstance(val_obj, dict) and "value" in val_obj:
            new_props = val_obj
        else:
            new_props = {"value": val_obj}
        
        # Preserve unit if it was lost in normalization (e.g. raw dict without value key but with unit)
        if isinstance(val_obj, dict) and "unit" in val_obj and "unit" not in new_props:
            new_props["unit"] = val_obj["unit"]

        # Clean name generation
        new_name = self.name if self.name.endswith(f".{suffix}") else f"{self.name}.{suffix}"
        
        return Feature(
            name=new_name,
            properties=new_props, # Sub-Feature has normalized structure
            is_enabled=self.is_enabled,
            is_ready=self.is_ready,
            commands={} 
        )

@dataclass(frozen=True)
class Device:
    """Representation of a Viessmann device."""
    id: str
    gateway_serial: str
    installation_id: str
    model_id: str
    device_type: str
    status: str
    features: List[Feature] = field(default_factory=list)

    @property
    def features_flat(self) -> List[Feature]:
        """Return a flattened list of all features."""
        # List comprehension is slightly faster than loop + extend
        return [sub_f for f in self.features for sub_f in f.expand()]
    
    # Helper for fast access
    def get_feature(self, name: str) -> Optional[Feature]:
        """O(n) lookup helper."""
        for f in self.features:
            if f.name == name:
                return f
        return None

    @classmethod
    def from_api(cls, data: Dict[str, Any], gateway_serial: str, installation_id: str) -> Device:
        return cls(
            id=data.get("id", ""),
            gateway_serial=gateway_serial,
            installation_id=installation_id,
            model_id=data.get("modelId", ""),
            device_type=data.get("deviceType", ""),
            status=data.get("status", "")
        )

@dataclass(frozen=True)
class CommandResponse:
    """Response from a command execution."""
    success: bool
    message: Optional[str] = None
    reason: Optional[str] = None

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> CommandResponse:
        """Create from API response."""
        # API response is strictly {"data": {"success": ..., "message": ..., "reason": ...}}
        # but sometimes might be distinct. We handle the inner 'data' block or root.
        root = data.get("data", data)
        # Handle string "True"/"False" or boolean
        success_raw = root.get("success")
        if isinstance(success_raw, str):
            success = success_raw.lower() == "true"
        else:
            success = bool(success_raw)
            
        return cls(
            success=success,
            message=root.get("message"),
            reason=root.get("reason")
        )

@dataclass(frozen=True)
class Gateway:
    """Representation of a gateway."""
    serial: str
    version: str
    status: str
    installation_id: str

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> Gateway:
        return cls(
            serial=data.get("serial", ""),
            version=data.get("version", ""),
            status=data.get("status", ""),
            installation_id=str(data.get("installationId", "")) 
        )
