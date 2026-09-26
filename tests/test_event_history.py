"""Tests for the installation event history read path."""

import aiohttp
import pytest
from aioresponses import aioresponses

from vi_api_client.client import ViClient
from vi_api_client.const import API_BASE_URL, ENDPOINT_EVENT_HISTORY
from vi_api_client.exceptions import ViResponseError
from vi_api_client.models import EventHistoryPage, InstallationEvent

INSTALLATION_ID = "99999"
EVENTS_URL = f"{API_BASE_URL}{ENDPOINT_EVENT_HISTORY}/{INSTALLATION_ID}/events"


def _page_url(query: str) -> str:
    """Return one event history request URL with its query string."""
    return f"{EVENTS_URL}?{query}"


@pytest.mark.asyncio
async def test_get_event_history_returns_first_page_by_days(
    load_fixture_device, static_token_auth
):
    """A days window should request one page and preserve provider details."""
    # Arrange: Load the bundled page and mock the verified GET route.
    payload = load_fixture_device("event_history")
    url = _page_url("lastNDays=7&limit=50")

    with aioresponses() as mock_responses:
        mock_responses.get(url, payload=payload)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Request the first page of a rolling week.
            page = await client.get_event_history(INSTALLATION_ID, days=7, limit=50)

    # Assert: Known fields are typed, unknown fields and bodies survive.
    assert len(page.events) == 3
    first = page.events[0]
    assert first.event_type == "feature-changed"
    assert first.created_at == "2026-09-20T10:15:30.878Z"
    assert first.event_timestamp == "2026-09-20T10:15:30.000Z"
    assert first.gateway_serial == "7630175843100101"
    assert first.body == {
        "featureName": "heating.dhw.temperature.main",
        "commandName": "setTargetTemperature",
        "commandBody": {"temperature": 55},
    }
    assert first.fields["editedBy"] == "3f2b1c0d-1111-4a5b-8c9d-0e1f2a3b4c5d"
    assert first.fields["audiences"] is None
    third = page.events[2]
    assert third.fields["unknownFutureField"] == {"nested": ["kept", "as-is"]}
    assert page.next_cursor == "b3BhcXVlLWN1cnNvci10b2tlbg=="

    request_entries = list(mock_responses.requests.items())
    assert len(request_entries) == 1
    (method, request_url), _requests = request_entries[0]
    assert method == "GET"
    assert str(request_url) == url
    request = _requests[0]
    assert request.kwargs["params"] == {"lastNDays": 7, "limit": 50}


@pytest.mark.asyncio
async def test_get_event_history_follows_cursor_without_window(static_token_auth):
    """A cursor page should not repeat the lookback window."""
    # Arrange: Register the continuation URL and a final page. The mock
    # re-encodes the transport-encoded query once more, so the registered
    # URL uses the mock's double-encoded form; the asserted request
    # contract below is the unchanged params mapping, which the provider
    # receives single-encoded.
    cursor = "b3BhcXVlLWN1cnNvci10b2tlbg=="
    url = _page_url("cursor=b3BhcXVlLWN1cnNvci10b2tlbg%253D%253D")

    with aioresponses() as mock_responses:
        mock_responses.get(url, payload={"data": []})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Request the next page through the opaque cursor.
            page = await client.get_event_history(INSTALLATION_ID, cursor=cursor)

    # Assert: The final page is empty without a further cursor, and the
    # request carries the cursor instead of the lookback window.
    assert page.events == ()
    assert page.next_cursor is None
    request = next(iter(mock_responses.requests.values()))[0]
    assert request.kwargs["params"] == {"cursor": cursor}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("days", "cursor", "limit", "message"),
    [
        (None, None, None, "exactly one of 'days' or 'cursor'"),
        (7, "cursor-token", None, "exactly one of 'days' or 'cursor'"),
        (0, None, None, "positive lookback window"),
        (-3, None, None, "positive lookback window"),
        (None, "", None, "non-empty string"),
        (7, None, 0, "between 1 and 1000"),
        (7, None, 1001, "between 1 and 1000"),
    ],
)
async def test_get_event_history_rejects_invalid_windows(
    days: int | None,
    cursor: str | None,
    limit: int | None,
    message: str,
    static_token_auth,
):
    """Invalid window arguments should fail before any request is sent."""
    # Arrange: Create a client without registering any HTTP response.
    async with aiohttp.ClientSession() as session:
        client = ViClient(static_token_auth(session))

        # Act and assert: Local contract violations raise before network I/O.
        with pytest.raises(ValueError, match=message):
            await client.get_event_history(
                INSTALLATION_ID, days=days, cursor=cursor, limit=limit
            )


@pytest.mark.asyncio
async def test_get_event_history_rejects_empty_installation_ids(static_token_auth):
    """An event history read requires a usable installation scope."""
    # Arrange: Create a client without registering any HTTP response.
    async with aiohttp.ClientSession() as session:
        client = ViClient(static_token_auth(session))

        # Act and assert: The empty scope is rejected before network I/O.
        with pytest.raises(ValueError, match="non-empty string"):
            await client.get_event_history("", days=7)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"data": {}},
        {"data": ["not-an-object"]},
        {"data": [{"eventType": "device.error.raised"}]},
        {
            "data": [
                {
                    "eventType": "device.error.raised",
                    "createdAt": 5,
                    "eventTimestamp": "2026-09-18T08:02:10.500Z",
                }
            ]
        },
        {
            "data": [
                {
                    "eventType": "device.error.raised",
                    "createdAt": "2026-09-18T08:02:11.000Z",
                    "eventTimestamp": "2026-09-18T08:02:10.500Z",
                    "gatewaySerial": 5,
                }
            ]
        },
        {
            "data": [
                {
                    "eventType": "device.error.raised",
                    "createdAt": "2026-09-18T08:02:11.000Z",
                    "eventTimestamp": "2026-09-18T08:02:10.500Z",
                    "audiences": "OWNER",
                }
            ]
        },
        {
            "data": [
                {
                    "eventType": "device.error.raised",
                    "createdAt": "2026-09-18T08:02:11.000Z",
                    "eventTimestamp": "2026-09-18T08:02:10.500Z",
                    "audiences": [5],
                }
            ]
        },
        {"data": [], "cursor": []},
        {"data": [], "cursor": {"next": 5}},
    ],
    ids=[
        "data-not-list",
        "data-entry-not-object",
        "missing-required-fields",
        "createdAt-not-string",
        "gatewaySerial-not-string",
        "audiences-not-list",
        "audiences-entry-not-string",
        "cursor-not-object",
        "cursor-next-not-string",
    ],
)
async def test_get_event_history_rejects_malformed_envelopes(
    payload: dict, static_token_auth
):
    """Contract violations in successful responses should raise publicly."""
    # Arrange: Return one malformed successful response from the route.
    url = _page_url("lastNDays=7")

    with aioresponses() as mock_responses:
        mock_responses.get(url, payload=payload)
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The public response error communicates the failure.
            with pytest.raises(ViResponseError):
                await client.get_event_history(INSTALLATION_ID, days=7)


@pytest.mark.asyncio
async def test_get_event_history_accepts_empty_cursor_next_as_final_page(
    static_token_auth,
):
    """The provider reports the final page with an empty next cursor."""
    # Arrange: The live API sends cursor.next as "" when no pages follow.
    url = _page_url("lastNDays=7")

    with aioresponses() as mock_responses:
        mock_responses.get(url, payload={"data": [], "cursor": {"next": ""}})
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act: Request the final page of the window.
            page = await client.get_event_history(INSTALLATION_ID, days=7)

    # Assert: The empty string means no continuation cursor.
    assert page.events == ()
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_get_event_history_rejects_non_object_envelopes(static_token_auth):
    """A successful event history response must be a JSON object."""
    # Arrange: Return a JSON array instead of the page envelope.
    url = _page_url("lastNDays=7")

    with aioresponses() as mock_responses:
        mock_responses.get(url, payload=[])
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The envelope check raises the public error.
            with pytest.raises(ViResponseError, match="must be an object"):
                await client.get_event_history(INSTALLATION_ID, days=7)


@pytest.mark.asyncio
async def test_get_event_history_rejects_successful_non_json_responses(
    static_token_auth,
):
    """A successful event history response must be JSON."""
    # Arrange: Return plain text content from the route.
    url = _page_url("lastNDays=7")

    with aioresponses() as mock_responses:
        mock_responses.get(url, body="not JSON", content_type="text/plain")
        async with aiohttp.ClientSession() as session:
            client = ViClient(static_token_auth(session))

            # Act and assert: The transport boundary raises the public error.
            with pytest.raises(ViResponseError, match="not valid JSON"):
                await client.get_event_history(INSTALLATION_ID, days=7)


def test_installation_event_rejects_non_json_nested_values():
    """Event data outside the JSON value contract should reject publicly."""
    # Act and assert: A non-JSON nested value cannot be preserved.
    with pytest.raises(ViResponseError, match="non-JSON nested value"):
        InstallationEvent.from_api(
            {
                "eventType": "device.error.raised",
                "createdAt": "2026-09-18T08:02:11.000Z",
                "eventTimestamp": "2026-09-18T08:02:10.500Z",
                "unknownField": object(),
            }
        )


def test_event_history_page_stores_immutable_snapshots():
    """Pages and events should not alias caller-owned collections."""
    # Arrange: Build a page from caller-owned collections.
    raw_event = {
        "eventType": "device.error.raised",
        "createdAt": "2026-09-18T08:02:11.000Z",
        "eventTimestamp": "2026-09-18T08:02:10.500Z",
        "body": {"errorCode": "F.9000"},
    }
    event = InstallationEvent.from_api(raw_event)
    page = EventHistoryPage(events=[event], next_cursor="cursor-token")

    # Act: Mutate the caller-owned collections after construction.
    raw_event["body"] = {"errorCode": "CHANGED"}
    events = list(page.events)

    # Assert: Snapshots are immutable and independent of caller mutations.
    assert isinstance(page.events, tuple)
    assert events[0].body == {"errorCode": "F.9000"}
    assert events[0].fields["eventType"] == "device.error.raised"
    assert page.next_cursor == "cursor-token"
