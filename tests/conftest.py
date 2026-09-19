"""Shared fixtures and test doubles for the vi_api_client test suite."""

import json
from pathlib import Path

import pytest

from vi_api_client.auth import AbstractAuth

TESTS_DIR = Path(__file__).resolve().parent


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
def device_responses_dir() -> Path:
    """Return the path to the bundled device fixture directory."""
    # The bundled product fixtures double as the offline test catalog.
    return TESTS_DIR.parent / "src" / "vi_api_client" / "fixtures"


@pytest.fixture
def available_fixture_devices(device_responses_dir: Path) -> list[str]:
    """Return the sorted names of the bundled fixture devices."""
    return sorted(path.stem for path in device_responses_dir.glob("*.json"))


@pytest.fixture
def load_fixture_device(device_responses_dir: Path):
    """Factory to load a bundled fixture device JSON by name."""

    def _load(name: str):
        with (device_responses_dir / f"{name}.json").open(encoding="utf-8") as file:
            return json.load(file)

    return _load


@pytest.fixture
def load_fixture_json():
    """Load a JSON fixture file from the tests/fixtures directory."""

    def _load(path: str):
        full_path = TESTS_DIR / "fixtures" / path
        with full_path.open(encoding="utf-8") as file:
            return json.load(file)

    return _load
