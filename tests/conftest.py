"""Shared fixtures and test doubles for the vi_api_client test suite."""

import json
import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client.auth import AbstractAuth
from vi_api_client.cli import async_main
from vi_api_client.client import ViClient

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
def mock_responses() -> Iterator[aioresponses]:
    """Intercept every aiohttp request; unregistered requests fail."""
    with aioresponses() as responses:
        yield responses


@pytest.fixture
async def static_token_auth(
    mock_responses: aioresponses,
) -> AsyncIterator[StaticTokenAuth]:
    """Return a static-token auth over a session whose requests are mocked."""
    async with aiohttp.ClientSession() as session:
        yield StaticTokenAuth(session)


@pytest.fixture
def vi_client(static_token_auth: StaticTokenAuth) -> ViClient:
    """Return a live client whose HTTP requests go to ``mock_responses``."""
    return ViClient(static_token_auth)


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
def no_http_requests(mock_responses: aioresponses) -> Iterator[aioresponses]:
    """Fail the test if code under test attempts any aiohttp request."""
    # No response is registered, so any request raises a connection error.
    yield mock_responses
    assert mock_responses.requests == {}


@pytest.fixture
def run_cli(monkeypatch, capsys, caplog):
    """Run one CLI invocation in-process and return its status and outputs.

    The returned error text joins stderr and the captured log records.
    """
    caplog.set_level(logging.INFO)
    # async_main configures root logging for real processes; in-process runs
    # must not leave handlers behind for later tests.
    monkeypatch.setattr(logging, "basicConfig", lambda **_: None)

    async def _run(*arguments: str) -> tuple[int, str, str]:
        capsys.readouterr()
        caplog.clear()
        exit_status = await async_main(list(arguments))
        captured = capsys.readouterr()
        return exit_status, captured.out, captured.err + caplog.text

    return _run
