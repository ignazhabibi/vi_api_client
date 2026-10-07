"""Shared fixtures and test doubles for the vi_api_client test suite."""

import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import aiohttp
import pytest
from aiointercept import aiointercept
from builders import StaticTokenAuth

from vi_api_client.cli import async_main
from vi_api_client.client import ViClient


@pytest.fixture
async def mock_responses() -> AsyncIterator[aiointercept]:
    """Intercept every aiohttp request; unregistered requests fail."""
    async with aiointercept(mock_external_urls=True) as responses:
        yield responses


@pytest.fixture
async def static_token_auth(
    mock_responses: aiointercept,
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
def no_http_requests(mock_responses: aiointercept) -> Iterator[aiointercept]:
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
