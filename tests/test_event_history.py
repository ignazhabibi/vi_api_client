"""Tests for the installation event history read path."""

import pytest
from builders import load_fixture_device

from vi_api_client import FixtureViClient
from vi_api_client.const import API_BASE_URL, ENDPOINT_EVENT_HISTORY
from vi_api_client.exceptions import ViResponseError
from vi_api_client.models import EventHistoryPage, EventHistoryWindow, InstallationEvent
from vi_api_client.parsing import parse_installation_event

INSTALLATION_ID = "99999"
EVENTS_URL = f"{API_BASE_URL}{ENDPOINT_EVENT_HISTORY}/{INSTALLATION_ID}/events"


def _page_url(query: str) -> str:
    """Return one event history request URL with its query string."""
    return f"{EVENTS_URL}?{query}"


async def test_get_event_history_returns_first_page_by_days(vi_client, mock_responses):
    """A days window should request one page and preserve provider details."""
    # Arrange: Load the bundled page and mock the verified GET route.
    payload = load_fixture_device("event_history")
    url = _page_url("lastNDays=7&limit=50")

    mock_responses.get(url, payload=payload)

    # Act: Request the first page of a rolling week.
    page = await vi_client.get_event_history(INSTALLATION_ID, days=7, limit=50)

    # Assert: Known fields are typed, unknown fields and bodies survive.
    assert len(page.events) == 3
    first = page.events[0]
    assert first.event_type == "feature-changed"
    assert first.created_at == "2026-09-20T10:15:30.878Z"
    assert first.event_timestamp == "2026-09-20T10:15:30.000Z"
    assert first.gateway_serial == "1234567890123456"
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
    assert dict(request.query) == {"lastNDays": "7", "limit": "50"}


async def test_get_event_history_follows_cursor_without_window(
    vi_client, mock_responses
):
    """A cursor page should not repeat the lookback window."""
    # Arrange: Register the continuation URL, with the cursor as the provider
    # receives it on the wire, and a final page.
    cursor = "b3BhcXVlLWN1cnNvci10b2tlbg=="
    url = _page_url("cursor=b3BhcXVlLWN1cnNvci10b2tlbg%3D%3D")

    mock_responses.get(url, payload={"data": []})

    # Act: Request the next page through the opaque cursor.
    page = await vi_client.get_event_history(INSTALLATION_ID, cursor=cursor)

    # Assert: The final page is empty without a further cursor, and the
    # request carries the cursor instead of the lookback window.
    assert page.events == ()
    assert page.next_cursor is None
    request = next(iter(mock_responses.requests.values()))[0]
    assert dict(request.query) == {"cursor": cursor}


@pytest.mark.parametrize(
    ("days", "cursor", "limit", "message"),
    [
        pytest.param(
            None,
            None,
            None,
            "exactly one of 'days' or 'cursor'",
            id="neither-days-nor-cursor",
        ),
        pytest.param(
            7,
            "cursor-token",
            None,
            "exactly one of 'days' or 'cursor'",
            id="both-days-and-cursor",
        ),
        pytest.param(0, None, None, "positive lookback window", id="zero-days"),
        pytest.param(-3, None, None, "positive lookback window", id="negative-days"),
        pytest.param(None, "", None, "non-empty string", id="empty-cursor"),
        pytest.param(7, None, 0, "between 1 and 1000", id="limit-below-range"),
        pytest.param(7, None, 1001, "between 1 and 1000", id="limit-above-range"),
    ],
)
@pytest.mark.usefixtures("no_http_requests")
async def test_get_event_history_rejects_invalid_windows(
    vi_client,
    days: int | None,
    cursor: str | None,
    limit: int | None,
    message: str,
):
    """Invalid window arguments should fail before any request is sent."""
    with pytest.raises(ValueError, match=message):
        await vi_client.get_event_history(
            INSTALLATION_ID, days=days, cursor=cursor, limit=limit
        )


@pytest.mark.usefixtures("no_http_requests")
async def test_get_event_history_rejects_empty_installation_ids(vi_client):
    """An event history read requires a usable installation scope."""
    with pytest.raises(ValueError, match="non-empty string"):
        await vi_client.get_event_history("", days=7)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        pytest.param([], "response must be an object", id="root-not-object"),
        pytest.param({"data": {}}, "data must be a list", id="data-not-list"),
        pytest.param(
            {"data": ["not-an-object"]},
            "entries must be objects",
            id="data-entry-not-object",
        ),
        pytest.param(
            {"data": [{"eventType": "device.error.raised"}]},
            "createdAt must be a non-empty string",
            id="missing-required-fields",
        ),
        pytest.param(
            {
                "data": [
                    {
                        "eventType": "device.error.raised",
                        "createdAt": 5,
                        "eventTimestamp": "2026-09-18T08:02:10.500Z",
                    }
                ]
            },
            "createdAt must be a non-empty string",
            id="createdAt-not-string",
        ),
        pytest.param(
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
            "gatewaySerial must be a string",
            id="gatewaySerial-not-string",
        ),
        pytest.param(
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
            "audiences must be a list of strings",
            id="audiences-not-list",
        ),
        pytest.param(
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
            "audiences must be a list of strings",
            id="audiences-entry-not-string",
        ),
        pytest.param(
            {"data": [], "cursor": []},
            "cursor must be an object",
            id="cursor-not-object",
        ),
        pytest.param(
            {"data": [], "cursor": {"next": 5}},
            "cursor next must be a string",
            id="cursor-next-not-string",
        ),
    ],
)
async def test_get_event_history_rejects_malformed_responses(
    vi_client, mock_responses, payload: dict | list, message: str
):
    url = _page_url("lastNDays=7")

    mock_responses.get(url, payload=payload)

    with pytest.raises(ViResponseError, match=message):
        await vi_client.get_event_history(INSTALLATION_ID, days=7)


async def test_get_event_history_accepts_empty_cursor_next_as_final_page(
    vi_client, mock_responses
):
    """The provider reports the final page with an empty next cursor."""
    # Arrange: The live API sends cursor.next as "" when no pages follow.
    url = _page_url("lastNDays=7")

    mock_responses.get(url, payload={"data": [], "cursor": {"next": ""}})

    # Act: Request the final page of the window.
    page = await vi_client.get_event_history(INSTALLATION_ID, days=7)

    # Assert: The empty string means no continuation cursor.
    assert page.events == ()
    assert page.next_cursor is None


async def test_get_event_history_rejects_successful_non_json_responses(
    vi_client, mock_responses
):
    url = _page_url("lastNDays=7")

    mock_responses.get(url, body="not JSON", content_type="text/plain")

    with pytest.raises(ViResponseError, match="not valid JSON"):
        await vi_client.get_event_history(INSTALLATION_ID, days=7)


def test_installation_event_rejects_non_json_nested_values():
    """Event data outside the JSON value contract should reject publicly."""
    # Act and assert: A non-JSON nested value cannot be preserved.
    with pytest.raises(ViResponseError, match="non-JSON nested value"):
        parse_installation_event(
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
    body = {"errorCode": "F.9000"}
    raw_event = {
        "eventType": "device.error.raised",
        "createdAt": "2026-09-18T08:02:11.000Z",
        "eventTimestamp": "2026-09-18T08:02:10.500Z",
        "body": body,
    }
    event = parse_installation_event(raw_event)
    caller_events = [event]
    page = EventHistoryPage(events=caller_events, next_cursor="cursor-token")

    # Act: Mutate the caller-owned collections in place after construction.
    body["errorCode"] = "CHANGED"
    caller_events.append(event)

    # Assert: The page and its event keep their original snapshots.
    assert page.events == (event,)
    assert page.events[0].body == {"errorCode": "F.9000"}
    assert page.events[0].fields["body"] == {"errorCode": "F.9000"}
    assert page.next_cursor == "cursor-token"


def test_installation_event_rejects_non_object_events():
    """An event must be a JSON object."""
    with pytest.raises(ViResponseError, match="Event must be an object"):
        parse_installation_event(["not", "an", "event"])  # type: ignore[arg-type]


def _event(timestamp: str) -> dict[str, object]:
    """Build one API event that occurred at the given time."""
    return {
        "eventType": "feature-changed",
        "createdAt": timestamp,
        "eventTimestamp": timestamp,
        "gatewayId": "1234567890123456",
        "body": {"featureName": "heating.dhw.temperature.main"},
    }


def _page(timestamps: list[str], cursor: str | None = None) -> dict[str, object]:
    """Build one API event history page with an optional continuation cursor."""
    page: dict[str, object] = {"data": [_event(timestamp) for timestamp in timestamps]}
    if cursor is not None:
        page["cursor"] = {"next": cursor}
    return page


async def test_get_event_history_window_follows_the_cursor_to_the_last_page(
    vi_client, mock_responses
):
    # Arrange: Three pages; only the first request carries the lookback.
    first_url = _page_url("lastNDays=7&limit=2")
    second_url = _page_url("cursor=page-2&limit=2")
    third_url = _page_url("cursor=page-3&limit=2")
    mock_responses.get(
        first_url,
        payload=_page(["2026-09-20T10:00:00Z", "2026-09-19T10:00:00Z"], "page-2"),
    )
    mock_responses.get(
        second_url,
        payload=_page(["2026-09-18T10:00:00Z", "2026-09-17T10:00:00Z"], "page-3"),
    )
    mock_responses.get(third_url, payload=_page(["2026-09-16T10:00:00Z"]))

    # Act: Collect the whole window.
    window = await vi_client.get_event_history_window(INSTALLATION_ID, 7, limit=2)

    # Assert: Every event is collected in page order and the window is
    # complete; each page was requested exactly once.
    assert isinstance(window, EventHistoryWindow)
    assert [event.event_timestamp for event in window.events] == [
        "2026-09-20T10:00:00Z",
        "2026-09-19T10:00:00Z",
        "2026-09-18T10:00:00Z",
        "2026-09-17T10:00:00Z",
        "2026-09-16T10:00:00Z",
    ]
    assert window.next_cursor is None
    assert window.pages_fetched == 3
    assert window.earliest_timestamp == "2026-09-16T10:00:00Z"
    assert window.latest_timestamp == "2026-09-20T10:00:00Z"
    assert [
        (str(url), len(requests))
        for (_, url), requests in mock_responses.requests.items()
    ] == [(first_url, 1), (second_url, 1), (third_url, 1)]


async def test_get_event_history_window_returns_an_empty_window(
    vi_client, mock_responses
):
    # Arrange: The provider has no events and uses its default page size.
    mock_responses.get(_page_url("lastNDays=365"), payload=_page([]))

    # Act: Collect the window without a page size.
    window = await vi_client.get_event_history_window(INSTALLATION_ID, 365)

    # Assert: One page was read and the window is complete and empty.
    assert window.events == ()
    assert window.next_cursor is None
    assert window.pages_fetched == 1
    assert window.earliest_timestamp is None
    assert window.latest_timestamp is None


async def test_get_event_history_window_stops_at_the_page_safety_limit(
    vi_client, mock_responses
):
    # Arrange: The provider keeps reporting a next page.
    mock_responses.get(
        _page_url("lastNDays=7"), payload=_page(["2026-09-20T10:00:00Z"], "page-2")
    )
    mock_responses.get(
        _page_url("cursor=page-2"), payload=_page(["2026-09-19T10:00:00Z"], "page-3")
    )

    # Act: Collect at most two pages.
    window = await vi_client.get_event_history_window(INSTALLATION_ID, 7, max_pages=2)

    # Assert: The window is incomplete and keeps the cursor to continue with;
    # the third page was never requested.
    assert len(window.events) == 2
    assert window.next_cursor == "page-3"
    assert window.pages_fetched == 2
    assert len(mock_responses.requests) == 2


@pytest.mark.usefixtures("no_http_requests")
@pytest.mark.parametrize(
    ("arguments", "options"),
    [
        pytest.param(("", 7), {}, id="empty-installation-id"),
        pytest.param((INSTALLATION_ID, 0), {}, id="zero-days"),
        pytest.param((INSTALLATION_ID, -1), {}, id="negative-days"),
        pytest.param((INSTALLATION_ID, 7), {"limit": 0}, id="zero-limit"),
        pytest.param((INSTALLATION_ID, 7), {"limit": 1001}, id="limit-above-maximum"),
        pytest.param((INSTALLATION_ID, 7), {"max_pages": 0}, id="zero-page-limit"),
        pytest.param((INSTALLATION_ID, 7), {"max_pages": -3}, id="negative-page-limit"),
    ],
)
async def test_get_event_history_window_rejects_invalid_arguments(
    vi_client, arguments, options
):
    # Act and assert: Invalid arguments fail before any request.
    with pytest.raises(ValueError):
        await vi_client.get_event_history_window(*arguments, **options)


@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_event_history_window_completes_after_two_pages():
    # Arrange: The bundled fixtures model a first page and an empty final page.
    client = FixtureViClient("Vitocal250A")

    # Act: Collect the fixture window.
    window = await client.get_event_history_window("99999", 7, limit=50)

    # Assert: The window is complete after both pages.
    assert len(window.events) == 3
    assert window.next_cursor is None
    assert window.pages_fetched == 2
    assert window.earliest_timestamp == "2026-09-18T08:02:10.500Z"
    assert window.latest_timestamp == "2026-09-20T10:15:30.000Z"


@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_event_history_window_stops_at_the_page_safety_limit():
    # Arrange: Use the bundled two-page window.
    client = FixtureViClient("Vitocal250A")

    # Act: Collect at most one page.
    window = await client.get_event_history_window("99999", 7, max_pages=1)

    # Assert: The window is incomplete and keeps the fixture cursor.
    assert len(window.events) == 3
    assert window.next_cursor is not None
    assert window.pages_fetched == 1


@pytest.mark.usefixtures("no_http_requests")
async def test_fixture_event_history_window_can_be_empty(monkeypatch):
    # Arrange: Serve the bundled empty final page for every request, as the
    # provider does for a window without events.
    client = FixtureViClient("Vitocal250A")
    empty_page = load_fixture_device("event_history_final_page")

    async def serve_empty_page(installation_id, params):
        return empty_page

    monkeypatch.setattr(
        client._discovery_adapter, "get_event_history", serve_empty_page
    )

    # Act: Collect the window.
    window = await client.get_event_history_window("99999", 7)

    # Assert: The window is complete and empty after one page.
    assert window.events == ()
    assert window.next_cursor is None
    assert window.pages_fetched == 1


def test_event_history_window_stores_an_immutable_event_snapshot():
    # Arrange: Build a window from a caller-owned list.
    events = [
        InstallationEvent(
            event_type="feature-changed",
            created_at="2026-09-20T10:00:00Z",
            event_timestamp="2026-09-20T10:00:00Z",
            gateway_serial=None,
            body=None,
        )
    ]
    window = EventHistoryWindow(events=events, next_cursor="page-2", pages_fetched=1)

    # Act: Change the caller's list afterwards.
    events.clear()

    # Assert: The window keeps its events and is frozen.
    assert len(window.events) == 1
    with pytest.raises(AttributeError):
        window.pages_fetched = 2  # type: ignore[misc]
