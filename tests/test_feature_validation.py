"""Public request-flow tests for feature response validation."""

import aiohttp
import pytest
from aioresponses import aioresponses
from builders import build_gateway_device

from vi_api_client.client import ViClient
from vi_api_client.const import API_BASE_URL, ENDPOINT_FEATURES
from vi_api_client.exceptions import ViResponseError


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("feature", "message"),
    [
        ({"properties": {"value": 1}}, "Feature name must be a non-empty string"),
        (
            {"feature": "heating.status", "properties": []},
            "Feature properties must be an object",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "isEnabled": "true",
            },
            "Feature enabled and ready fields must be booleans",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": []}},
            },
            "params must be an object",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1, "unit": 5},
            },
            "unit must be a string",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": [],
            },
            "commands must be an object",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": []},
            },
            "named objects",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"uri": 5}},
            },
            "uri must be a string",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"isExecutable": "yes"}},
            },
            "isExecutable must be a boolean",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": 5}}},
            },
            "params must contain named objects",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"required": "yes"}}}},
            },
            "required must be a boolean",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"type": 5}}}},
            },
            "type must be a string",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"enum": "auto"}}}},
            },
            "enum must be a list",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"enum": [float("nan")]}}}},
            },
            "Feature constraint enum",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"constraints": "no"}}}},
            },
            "constraints must be an object",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"min": "low"}}}},
            },
            "Feature constraint min must be a number",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": {"min": "low"}},
            },
            "Feature constraint min must be a number",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": "no"}},
            },
            "Feature property constraints must be an object",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"min": "low"}}},
            },
            "Feature constraint min must be a number",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"minLength": "two"}}},
            },
            "minLength must be an integer",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"pattern": 5}}},
            },
            "pattern must be a string",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"regEx": 5}}},
            },
            "regEx must be a string",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"enum": "auto"}}},
            },
            "Feature constraint enum must be a list",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": {"constraints": {"min": float("inf")}}},
            },
            "must be a finite number",
        ),
        (
            {
                "feature": "heating.status",
                "properties": {"value": 1},
                "commands": {"set": {"params": {"slope": {"min": float("inf")}}}},
            },
            "Feature constraint min must be a finite number",
        ),
    ],
    ids=[
        "missing-feature-name",
        "properties-not-object",
        "enabled-not-boolean",
        "command-params-not-object",
        "property-unit-not-string",
        "commands-not-object",
        "command-not-object",
        "command-uri-not-string",
        "command-executable-not-boolean",
        "command-param-not-object",
        "command-param-required-not-boolean",
        "command-param-type-not-string",
        "command-param-enum-not-list",
        "command-param-enum-non-finite",
        "command-param-constraints-not-object",
        "command-param-min-not-number",
        "property-min-not-number",
        "property-constraints-not-object",
        "property-constraint-min-not-number",
        "property-constraint-min-length-not-integer",
        "property-constraint-pattern-not-string",
        "property-constraint-regex-not-string",
        "property-constraint-enum-not-list",
        "property-constraint-min-non-finite",
        "command-param-min-non-finite",
    ],
)
async def test_live_feature_read_rejects_malformed_known_fields(
    static_token_auth, feature, message
):
    """Live feature reads reject malformed known fields before flattening."""
    # Arrange: Return an invalid feature through the public HTTP request flow.
    device = build_gateway_device("device-1")
    endpoint = (
        f"{API_BASE_URL}{ENDPOINT_FEATURES}/installation-1/gateways/gateway-1/"
        "devices/device-1/features/filter"
    )
    with aioresponses() as mock_responses:
        mock_responses.post(endpoint, status=200, payload={"data": [feature]})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The public read rejects known contract violations.
            with pytest.raises(ViResponseError, match=message):
                await client.get_features(device)
