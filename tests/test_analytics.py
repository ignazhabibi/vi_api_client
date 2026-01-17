"""Unit tests for Analytics logic."""

import pytest

from vi_api_client.analytics import parse_consumption_response, resolve_properties


def test_resolve_properties():
    """Test metric mapping."""
    # Summary -> All 3
    props = resolve_properties("summary")
    assert len(props) == 3
    assert "heating.power.consumption.total" in props

    # Specific
    props = resolve_properties("dhw")
    assert len(props) == 1
    assert props[0] == "heating.power.consumption.dhw"

    # Invalid
    with pytest.raises(ValueError):
        resolve_properties("invalid_metric")


def test_parse_consumption_response():
    """Test parsing of raw API response."""
    raw_data = {
        "data": {
            "data": {
                "summary": {
                    "heating.power.consumption.total": 12.5,
                    "heating.power.consumption.dhw": 4.0,
                }
            }
        }
    }

    # Parse total
    props = ["heating.power.consumption.total"]
    features = parse_consumption_response(raw_data, props)
    assert len(features) == 1
    assert features[0].name == "analytics.heating.power.consumption.total"
    assert features[0].value == 12.5
    assert features[0].unit == "kilowattHour"

    # Parse missing property (should be 0.0)
    props = ["heating.power.consumption.heating"]
    features = parse_consumption_response(raw_data, props)
    assert len(features) == 1
    assert features[0].value == 0.0
