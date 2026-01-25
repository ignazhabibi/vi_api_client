"""Tests/Verification for device response fixtures (Mock Data).

These tests ensure that the bundled JSON mock data (used for the MockViClient
and delivered to users) is valid and can be correctly parsed by the library.
"""

import glob
import json
import os

import pytest

from vi_api_client.parsing import parse_feature_flat

# Path to the bundled fixtures (src/vi_api_client/fixtures)
# We test these to ensure the MockClient works correctly for downstream users.
MOCK_DATA_DIR = os.path.join(
    os.path.dirname(__file__), "..", "src", "vi_api_client", "fixtures"
)


def get_mock_data_files():
    """Get list of all mock device JSON files."""
    return glob.glob(os.path.join(MOCK_DATA_DIR, "*.json"))


@pytest.mark.parametrize("file_path", get_mock_data_files(), ids=os.path.basename)
def test_mock_data_integrity(file_path):
    """Verify that each mock device file parses successfully and features extract correctly."""
    # Arrange: Load mock device JSON file and extract features array.
    file_name = os.path.basename(file_path)
    print(f"Testing mock data file: {file_name}")

    with open(file_path) as f:
        data = json.load(f)

    # Some fixtures wrap the list in {"data": [...]}, others are just [...]
    if isinstance(data, dict) and "data" in data:
        raw_features = data["data"]
    elif isinstance(data, list):
        raw_features = data
    else:
        # Fallback for single object fixture
        raw_features = [data]

    all_features = []

    # Act: Parse all raw features using flat architecture parser.
    for raw_f in raw_features:
        parsed = parse_feature_flat(raw_f)
        all_features.extend(parsed)

    # Assert: Verify features parsed successfully and have correct constraints.
    assert len(all_features) > 0, f"Mock data {file_name} resulted in 0 features"

    # Specific assertions for known patterns to ensure data quality
    writables = [feature for feature in all_features if feature.is_writable]

    # 1. Check heating curve constraints
    curves = [feature for feature in writables if "heating.curve" in feature.name]
    for curve in curves:
        if curve.control.param_name in ["slope", "shift"]:
            assert curve.control.min is not None, (
                f"{curve.name}: Missing min constraint"
            )
            assert curve.control.max is not None, (
                f"{curve.name}: Missing max constraint"
            )

    # 2. Check regex patterns
    regex_feats = [feature for feature in writables if feature.control.pattern]
    for rf in regex_feats:
        assert rf.control.pattern.startswith("^"), (
            f"Pattern {rf.control.pattern} doesn't look like regex"
        )
