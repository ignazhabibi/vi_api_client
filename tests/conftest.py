"""Shared fixtures and test doubles for the vi_api_client test suite."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from aioresponses import aioresponses

from vi_api_client.auth import AbstractAuth

TESTS_DIR = Path(__file__).resolve().parent

# The discovery catalog and the event history responses share the fixture
# directory but are not device fixtures.
DISCOVERY_CATALOG_NAME = "discovery"
NON_DEVICE_FIXTURE_NAMES = {
    DISCOVERY_CATALOG_NAME,
    "event_history",
    "event_history_final_page",
}


class StaticTokenAuth(AbstractAuth):
    """Provide a static token for live client request-flow tests."""

    async def async_get_access_token(self) -> str:
        """Return the access token used by mocked HTTP requests."""
        return "access-token"


@pytest.fixture
def static_token_auth() -> type[StaticTokenAuth]:
    """Return the shared static-token auth double class."""
    return StaticTokenAuth


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    """Return the path of a not yet created credential document."""
    return tmp_path / "tokens.json"


@pytest.fixture
def device_responses_dir() -> Path:
    """Return the path to the bundled device fixture directory."""
    # The bundled product fixtures double as the offline test catalog.
    return TESTS_DIR.parent / "src" / "vi_api_client" / "fixtures"


@pytest.fixture
def available_fixture_devices(device_responses_dir: Path) -> list[str]:
    """Return the sorted names of the bundled device fixture files."""
    # Skip the catalog and event history files that share the directory.
    return sorted(
        path.stem
        for path in device_responses_dir.glob("*.json")
        if path.stem not in NON_DEVICE_FIXTURE_NAMES
    )


@pytest.fixture
def load_fixture_device(device_responses_dir: Path):
    """Factory to load a bundled fixture device JSON by name."""

    def _load(name: str):
        with (device_responses_dir / f"{name}.json").open(encoding="utf-8") as file:
            return json.load(file)

    return _load


@pytest.fixture
def fixture_discovery_catalog(device_responses_dir: Path):
    """Return the bundled discovery catalog that lists the fixture devices."""
    catalog_file = device_responses_dir / f"{DISCOVERY_CATALOG_NAME}.json"
    return json.loads(catalog_file.read_text(encoding="utf-8"))


@pytest.fixture
def load_fixture_json():
    """Load a JSON fixture file from the tests/fixtures directory."""

    def _load(path: str):
        full_path = TESTS_DIR / "fixtures" / path
        with full_path.open(encoding="utf-8") as file:
            return json.load(file)

    return _load


@pytest.fixture
def no_http_requests() -> Iterator[aioresponses]:
    """Fail the test if code under test attempts any aiohttp request."""
    with aioresponses() as mock_responses:
        # No response is registered, so any request raises a connection error.
        yield mock_responses
    assert mock_responses.requests == {}
