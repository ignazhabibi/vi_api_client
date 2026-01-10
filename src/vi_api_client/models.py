"""Data models for Viessmann API objects."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

@dataclass
class Feature:
    """Representation of a Viessmann feature."""
    
    name: str
    properties: Dict[str, Any]
    is_enabled: bool
    is_ready: bool
    
    @property
    def value(self) -> Union[str, int, float, None]:
        """Tries to extract a single representative value for the feature.
        
        This mimics the logic used in the CLI to show a human-readable value.
        """
        if not self.properties:
            return None
            
        # Standard pattern: single 'value' or 'status'
        if "value" in self.properties:
            val_obj = self.properties["value"]
            if isinstance(val_obj, dict) and "value" in val_obj:
                return val_obj["value"]
                
        if "status" in self.properties:
            val_obj = self.properties["status"]
            if isinstance(val_obj, dict) and "value" in val_obj:
                return val_obj["value"]
                
        # For complex/nested features, we might return the whole dict or None?
        # For simple sensors, the above covers 90%
        return None

    @property
    def unit(self) -> Optional[str]:
        """Extract unit if available."""
        if "value" in self.properties:
            val_obj = self.properties["value"]
            if isinstance(val_obj, dict):
                return val_obj.get("unit")
        return None

    @property
    def formatted_value(self) -> str:
        """Return value with unit string representation."""
        val = self.value
        if val is None:
            # Fallback for complex types: list key-value pairs
            found_vals = []
            for key, val_item in self.properties.items():
                 if isinstance(val_item, dict) and "value" in val_item:
                    v = val_item.get("value")
                    u = val_item.get("unit", "")
                    if key == "value" or key == "status":
                        found_vals.append(f"{v} {u}".strip())
                    else:
                        found_vals.append(f"{key}: {v} {u}".strip())
            return ", ".join(found_vals)

        u = self.unit
        return f"{val} {u}".strip() if u else str(val)

    @classmethod
    def from_api(cls, data: Dict[str, Any]) -> "Feature":
        """Factory method to create feature from API response."""
        return cls(
            name=data.get("feature", ""),
            properties=data.get("properties", {}),
            is_enabled=data.get("isEnabled", False),
            is_ready=data.get("isReady", False)
        )


@dataclass
class Device:
    """Representation of a Viessmann device."""
    
    id: str
    gateway_serial: str
    installation_id: int
    model_id: str
    device_type: str
    status: str
    features: List[Feature] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: Dict[str, Any], gateway_serial: str, installation_id: int) -> "Device":
        """Factory method to create device from API response."""
        return cls(
            id=data.get("id", ""),
            gateway_serial=gateway_serial,
            installation_id=installation_id,
            model_id=data.get("modelId", ""),
            device_type=data.get("deviceType", ""),
            status=data.get("status", "")
        )
