"""Tests/Verification against collected JSON fixtures."""

import glob
import json
import os

import pytest

from vi_api_client.parsing import parse_feature_flat

# Path matches where the user said fixtures are: src/vi_api_client/fixtures
FIXTURES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "src", "vi_api_client", "fixtures"
)


def get_fixture_files():
    """Get list of all fixture files."""
    return glob.glob(os.path.join(FIXTURES_DIR, "*.json"))


@pytest.mark.parametrize("file_path", get_fixture_files())
def test_parse_fixture(file_path):
    """Verify that each fixture parse successfully and extracts features."""
    file_name = os.path.basename(file_path)
    print(f"Testing fixture: {file_name}")

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
    for raw_f in raw_features:
        parsed = parse_feature_flat(raw_f)
        all_features.extend(parsed)

    assert len(all_features) > 0, f"Fixture {file_name} resulted in 0 features"

    # Specific assertions for known patterns
    writables = [f for f in all_features if f.is_writable]

    # 1. Check heating curve constraints extraction
    curves = [f for f in writables if "heating.curve" in f.name]
    for c in curves:
        # If it's a writable curve param (slope/shift), it generally has constraints
        if c.control.param_name in ["slope", "shift"]:
            assert c.control.min is not None, f"{c.name}: Missing min constraint"
            assert c.control.max is not None, f"{c.name}: Missing max constraint"

    # 2. Check regex extraction (if applicable)
    # Most fixtures have holiday programs with regex dates
    regex_feats = [f for f in writables if f.control.pattern]
    # Not asserting len > 0 globally because some small fixtures might not have them,
    # but if they appear, they should be valid.
    for rf in regex_feats:
        assert rf.control.pattern.startswith("^"), (
            f"Pattern {rf.control.pattern} doesn't look like regex"
        )
