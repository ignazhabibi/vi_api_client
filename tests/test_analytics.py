"""Unit tests for Analytics logic."""

import pytest

from vi_api_client.analytics import parse_consumption_response, resolve_properties


def test_resolve_properties():
    """Test metric mapping."""
    # --- ARRANGE ---
    # N/A

    # --- ACT 1: Summary ---
    props = resolve_properties("summary")

    # --- ASSERT 1 ---
    assert len(props) == 3
    assert "heating.power.consumption.total" in props

    # --- ACT 2: Specific ---
    props = resolve_properties("dhw")

    # --- ASSERT 2 ---
    assert len(props) == 1
    assert props[0] == "heating.power.consumption.dhw"

    # --- ACT 3: Invalid ---
    with pytest.raises(ValueError):
        resolve_properties("invalid_metric")


def test_parse_consumption_response(load_fixture_json):
    """Test parsing of raw API response."""
    # --- ARRANGE ---
    raw_data = load_fixture_json("analytics/consumption_raw.json")
    props = ["heating.power.consumption.total"]

    # --- ACT 1: Parse total ---
    features = parse_consumption_response(raw_data, props)

    # --- ASSERT 1 ---
    assert len(features) == 1
    assert features[0].name == "analytics.heating.power.consumption.total"
    assert features[0].value == 12.5
    assert features[0].unit == "kilowattHour"

    # --- ACT 2: Parse missing property ---
    # Should get 0.0 default
    props_missing = ["heating.power.consumption.heating"]
    features_missing = parse_consumption_response(raw_data, props_missing)

    # --- ASSERT 2 ---
    assert len(features_missing) == 1
    assert features_missing[0].value == 0.0
