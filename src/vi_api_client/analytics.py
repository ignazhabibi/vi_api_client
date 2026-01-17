"""Analytics logic helper for Viessmann API."""

from datetime import datetime
from typing import Any, Dict, List, Tuple, Union
from .models import Feature

# Metric to API property mapping
METRIC_MAPPING = {
    "total": "heating.power.consumption.total",
    "heating": "heating.power.consumption.heating",
    "dhw": "heating.power.consumption.dhw"
}


def resolve_properties(metric: str) -> List[str]:
    """Resolve the requested metric to a list of API property strings."""
    if metric == "summary":
        return list(METRIC_MAPPING.values())
    elif metric in METRIC_MAPPING:
        return [METRIC_MAPPING[metric]]
    else:
        raise ValueError(f"Invalid metric: {metric}. Must be 'summary', 'total', 'heating', or 'dhw'.")

def parse_consumption_response(raw_data: Dict[str, Any], properties: List[str]) -> List[Feature]:
    """Parse the raw analytics API response into Feature objects."""
    # Parse response based on structure:
    # { "data": { "data": { "summary": { "prop": value, ... } } } }
    features = []
    
    data_block = raw_data.get("data", {}).get("data", {})
    summary = data_block.get("summary", {})
    
    for prop_name in properties:
        # Extract value directly from summary dict
        val = summary.get(prop_name, 0.0)
        
        f = Feature(
            name=f"analytics.{prop_name}",
            properties={"value": {"value": val, "unit": "kilowattHour"}}, 
            is_enabled=True,
            is_ready=True
        )
        features.append(f)
        
    return features
