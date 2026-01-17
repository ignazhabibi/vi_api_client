import json
import os

import pytest


@pytest.fixture
def device_responses_dir():
    """Return the path to the device responses directory."""
    # We can use the ones in src/vi_api_client/fixtures to test the bundled files
    return os.path.join(
        os.path.dirname(__file__), "..", "src", "vi_api_client", "fixtures"
    )


@pytest.fixture
def available_mock_devices(device_responses_dir):
    """Return a list of available mock device filenames (without extension)."""
    files = [f for f in os.listdir(device_responses_dir) if f.endswith(".json")]
    return sorted([os.path.splitext(f)[0] for f in files])


@pytest.fixture
def load_mock_device(device_responses_dir):
    """Factory to load a mock device JSON by name."""

    def _load(name):
        with open(os.path.join(device_responses_dir, f"{name}.json")) as f:
            return json.load(f)

    return _load
