"""Tests for feature response validation."""

import pytest
from builders import build_device

from vi_api_client.const import API_BASE_URL, ENDPOINT_FEATURES
from vi_api_client.exceptions import ViResponseError
from vi_api_client.parsing import api_feature_to_flat_features


@pytest.mark.parametrize(
    ("feature", "message"),
    [
        pytest.param(
            {"properties": {"value": 1}},
            "Feature name must be a non-empty string",
            id="missing-feature-name",
        ),
        pytest.param(
            {"feature": "heating.status", "properties": []},
            "Feature properties must be an object",
            id="properties-not-object",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "isEnabled": "true",
            },
            "Feature enabled and ready fields must be booleans",
            id="enabled-not-boolean",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": []}},
            },
            "params must be an object",
            id="command-params-not-object",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1, "unit": 5},
            },
            "unit must be a string",
            id="property-unit-not-string",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": [],
            },
            "commands must be an object",
            id="commands-not-object",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": []},
            },
            "named objects",
            id="command-not-object",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"uri": 5}},
            },
            "uri must be a string",
            id="command-uri-not-string",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"isExecutable": "yes"}},
            },
            "isExecutable must be a boolean",
            id="command-executable-not-boolean",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": 5}}},
            },
            "params must contain named objects",
            id="command-param-not-object",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"required": "yes"}}}},
            },
            "required must be a boolean",
            id="command-param-required-not-boolean",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"type": 5}}}},
            },
            "type must be a string",
            id="command-param-type-not-string",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"enum": "auto"}}}},
            },
            "enum must be a list",
            id="command-param-enum-not-list",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"enum": [float("nan")]}}}},
            },
            "Feature constraint enum",
            id="command-param-enum-non-finite",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"constraints": "no"}}}},
            },
            "constraints must be an object",
            id="command-param-constraints-not-object",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"min": "low"}}}},
            },
            "Feature constraint min must be a number",
            id="command-param-min-not-number",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": {"min": "low"}},
            },
            "Feature constraint min must be a number",
            id="property-min-not-number",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": "no"}},
            },
            "Feature property constraints must be an object",
            id="property-constraints-not-object",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"min": "low"}}},
            },
            "Feature constraint min must be a number",
            id="property-constraint-min-not-number",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"minLength": "two"}}},
            },
            "minLength must be an integer",
            id="property-constraint-min-length-not-integer",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"pattern": 5}}},
            },
            "pattern must be a string",
            id="property-constraint-pattern-not-string",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"regEx": 5}}},
            },
            "regEx must be a string",
            id="property-constraint-regex-not-string",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"enum": "auto"}}},
            },
            "Feature constraint enum must be a list",
            id="property-constraint-enum-not-list",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"min": float("inf")}}},
            },
            "must be a finite number",
            id="property-constraint-min-non-finite",
        ),
        pytest.param(
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"min": float("inf")}}}},
            },
            "Feature constraint min must be a finite number",
            id="command-param-min-non-finite",
        ),
    ],
)
def test_feature_parsing_rejects_malformed_known_fields(feature, message):
    """Malformed known fields fail before a feature is flattened."""
    with pytest.raises(ViResponseError, match=message):
        api_feature_to_flat_features(feature)


async def test_live_feature_read_rejects_malformed_features(vi_client, mock_responses):
    """Live feature reads apply the feature validation to the API response."""
    # Arrange: Return a feature without a name through the HTTP request flow.
    device = build_device("device-1")
    endpoint = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/gateway-1/"
        "devices/device-1/features/filter"
    )
    mock_responses.post(endpoint, payload={"data": [{"properties": {"value": 1}}]})

    # Act and assert: The public read surfaces the validation error.
    with pytest.raises(ViResponseError, match="Feature name must be a non-empty"):
        await vi_client.get_features(device)
