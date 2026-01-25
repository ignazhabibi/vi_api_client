"""Analytics logic helper for Viessmann API."""

from typing import Any

from .models import Feature

# Metric to API property mapping
METRIC_MAPPING = {
    "dhw": "heating.power.consumption.dhw",
    "heating": "heating.power.consumption.heating",
    "total": "heating.power.consumption.total",
}


def resolve_properties(metric: str) -> list[str]:
    """Resolve the requested metric to a list of API property strings.

    Args:
        metric: High-level metric name ('summary', 'total', 'heating', 'dhw').

    Returns:
        List of internal API property names.

    Raises:
        ValueError: If metric is unknown.
    """
    if metric == "summary":
        return list(METRIC_MAPPING.values())
    elif metric in METRIC_MAPPING:
        return [METRIC_MAPPING[metric]]
    else:
        raise ValueError(
            f"Invalid metric: {metric}. "
            "Must be 'summary', 'total', 'heating', or 'dhw'."
        )


def parse_consumption_response(
    raw_data: dict[str, Any], properties: list[str]
) -> list[Feature]:
    """Parse the raw analytics API response into Feature objects.

    Args:
        raw_data: The JSON response from the API.
        properties: List of properties that were requested.

    Returns:
        List of Feature objects containing the consumption data.
    """
    # Parse response based on structure:
    # { "data": { "data": { "summary": { "prop": value, ... } } } }
    features = []

    data_block = raw_data.get("data", {}).get("data", {})
    summary = data_block.get("summary", {})

    for prop_name in properties:
        # Extract value directly from summary dict
        value = summary.get(prop_name, 0.0)

        feature = Feature(
            name=f"analytics.{prop_name}",
            value=value,
            unit="kilowattHour",
            is_enabled=True,
            is_ready=True,
        )
        features.append(feature)

    return features
