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
    def value(self) -> Union[str, int, float, bool, list, None]:
        """Tries to extract the main value for the feature.
        
        Prioritizes: 'value' > 'status' > 'active' > 'enabled'.
        For lists (e.g. consumption history), returns the raw list.
        """
        if not self.properties:
            return None
            
        # 1. Standard 'value'
        if "value" in self.properties:
            val_obj = self.properties["value"]
            if isinstance(val_obj, dict) and "value" in val_obj:
                return val_obj["value"]
            # Fallback if 'value' property is not a dict structure but direct value (rare but possible)
            if not isinstance(val_obj, dict):
                return val_obj

        # 2. 'status'
        if "status" in self.properties:
            val_obj = self.properties["status"]
            if isinstance(val_obj, dict) and "value" in val_obj:
                return val_obj["value"]

        # 3. 'active' (Boolean)
        if "active" in self.properties:
            val_obj = self.properties["active"]
            if isinstance(val_obj, dict) and "value" in val_obj:
                return val_obj["value"]
                
        # 5. 'strength' (Wifi)
        if "strength" in self.properties:
            val_obj = self.properties["strength"]
            if isinstance(val_obj, dict) and "value" in val_obj:
                return val_obj["value"]

        # 6. 'entries' (Error/Status Messages)
        if "entries" in self.properties:
            val_obj = self.properties["entries"]
            if isinstance(val_obj, dict) and "value" in val_obj:
                return val_obj["value"]
            # Sometimes entries is a direct list
            if isinstance(val_obj, list):
                return val_obj

        # 7. History Lists (day/week/month/year)
        # If we didn't find a scalar 'value', check for common history keys
        for key in ["day", "week", "month", "year"]:
             if key in self.properties:
                val_obj = self.properties[key]
                if isinstance(val_obj, dict) and "value" in val_obj:
                    return val_obj["value"]

        return None

    @property
    def unit(self) -> Optional[str]:
        """Extract unit if available from the primary value property."""
        # Check definitions in order
        for key in ["value", "status", "day", "week", "month"]:
             if key in self.properties:
                val_obj = self.properties[key]
                if isinstance(val_obj, dict):
                    return val_obj.get("unit")
        return None

    @property
    def formatted_value(self) -> str:
        """Return value with unit string representation."""
        val = self.value
        u = self.unit
        
        if val is None:
            # Fallback: dump properties cleanly
            parts = []
            for k, v in self.properties.items():
                if isinstance(v, dict) and "value" in v:
                    # Extract value/unit from sub-property
                    sub_val = v["value"]
                    sub_unit = v.get("unit", "")
                    parts.append(f"{k}: {sub_val} {sub_unit}".strip())
                else:
                    parts.append(f"{k}: {v}")
            return ", ".join(parts)
            
        if isinstance(val, list):
            # For short lists, show the content for better debugging
            if len(val) <= 10:
                # Convert items to string to ensure join works
                return str(val) + (f" {u}".strip() if u else "")
            return f"List[{len(val)} items] {u or ''}".strip()
            
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


    def expand(self) -> List["Feature"]:
        """Expand complex features into a list of simple scalar features.
        
        Automatically handles:
        - Lists (summaries): ...summary.dhw -> [...currentDay, ...lastMonth]
        - Composites: ...curve -> [...slope, ...shift]
        - Metrics: ...statistics -> [...starts, ...hours]
        - Limits: ...levels -> [...min, ...max]
        
        Preserves simple features like 'temperature' (value) or 'wifi' (strength) as single items.
        """
        flattened = []
        
        # Metadata keys to ignore during analysis
        ignore_keys = {"unit", "type", "displayValue", "links"}
        
        # Keys that indicate this is a "Primary Value" feature (do not expand if this is the only key)
        # Note: 'day'/'week' etc are lists, but we treat them as the feature's value.
        primary_keys = {
            "value", "status", "active", "enabled", 
            "strength", "entries", 
            "day", "week", "month", "year"
        }

        # Filter properties to significant data keys
        data_keys = [k for k in self.properties.keys() if k not in ignore_keys]
        
        # Decision Logic:
        # 1. If multiple data keys -> It's a composite (e.g. starts + hours, or min + max). Expand ALL.
        # 2. If single data key BUT it's not a primary key (e.g. only 'slope'?) -> Expand it to be safe/explicit.
        #    (Example: 'heating.curve' might rarely strictly just have 'slope').
        # 3. If single data key AND it IS a primary key -> Simple feature. Keep self.
        
        should_expand = False
        
        # Rule 1: If 'value' is present, it's a standard feature. Do not expand.
        if "value" in data_keys:
            should_expand = False
            
        # Rule 2: If ALL keys are 'primary keys' (e.g. value + status), do not expand.
        elif all(k in primary_keys for k in data_keys):
            should_expand = False
            
        # Rule 3: Otherwise (mixed keys, or non-primary keys like slope/starts), expand.
        else:
            should_expand = True
                
        if should_expand:
            for key in data_keys:
                if key in self.properties:
                    flattened.append(self._create_sub_feature(key, self.properties[key]))
            return flattened
            
        return [self]

    def _create_sub_feature(self, suffix: str, val_obj: Any) -> "Feature":
        """Helper to create a virtual sub-feature."""
        # Ensure the value object is wrapped in standard structure {"value": ...} for the new feature
        # If val_obj is already {"value": 11, "unit": "kWh"}, we can use it as the "value" property of the new feature.
        
        # We construct a new property dict where "value" is the primary key
        # This ensures feature.value works on the result.
        new_props = {"value": val_obj}
        
        return Feature(
            name=f"{self.name}.{suffix}",
            properties=new_props,
            is_enabled=self.is_enabled,
            is_ready=self.is_ready
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

    @property
    def features_flat(self) -> List[Feature]:
        """Return a flattened list of all features.
        
        Complex features are broken down into simple scalars.
        """
        all_flat = []
        for f in self.features:
            all_flat.extend(f.expand())
        return all_flat

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
