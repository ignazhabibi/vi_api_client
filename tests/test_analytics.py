"""Unit tests for Analytics logic."""

import pytest

from vi_api_client.analytics import parse_consumption_response, resolve_properties


def test_resolve_properties():
    """Test metric mapping."""
    # Arrange: Prepare test data and fixtures.
    # N/A

    # Act: Resolve properties for summary metric.
    props = resolve_properties("summary")

    # Assert: Summary should include 3 properties.
    assert len(props) == 3
    assert "heating.power.consumption.total" in props

    # Act: Resolve properties for specific DHW metric.
    props = resolve_properties("dhw")

    # Assert: DHW metric should resolve to single property.
    assert len(props) == 1
    assert props[0] == "heating.power.consumption.dhw"

    # Act: Attempt to resolve invalid metric (should raise ValueError).
    with pytest.raises(ValueError):
        resolve_properties("invalid_metric")


def test_parse_consumption_response(load_fixture_json):
    """Test parsing of raw API response."""
    # Arrange: Prepare test data and fixtures.
    raw_data = load_fixture_json("analytics/consumption_raw.json")
    props = ["heating.power.consumption.total"]

    # Act: Parse consumption response for total metric.
    features = parse_consumption_response(raw_data, props)

    # Assert: Total consumption value should be correctly parsed.
    assert len(features) == 1
    assert features[0].name == "analytics.heating.power.consumption.total"
    assert features[0].value == 12.5
    assert features[0].unit == "kilowattHour"

    # Act: Parse response for missing property (should default to 0.0).
    # Should get 0.0 default
    props_missing = ["heating.power.consumption.heating"]
    features_missing = parse_consumption_response(raw_data, props_missing)

    # Assert: Missing property should default to 0.0.
    assert len(features_missing) == 1
    assert features_missing[0].value == 0.0
