"""Read-only endpoint probe for the Viessmann event-history API.

Two authenticated GET paths are plausible for the installation event
history: the 2023 announcement spelling
``/iot/v2/events-history/installations/{installationId}/events`` and the
current developer-portal OpenAPI export spelling
``/iot/v2/eventhistory/installations/{installationId}/events``. This script
requests the same read-only page from both paths with one local OAuth
credential document and prints a sanitized comparison: HTTP status, API
error type, and whether the response has the expected ``data``/``cursor``
shape. It never prints tokens, installation IDs, cursor values, API free
text, or event bodies.

Usage (from the repository root, with a completed ``vi-client login``):

    .venv/bin/python scripts/probe_event_history.py --installation-id 123456

The output is one JSON document intended for the implementation PR of the
route decision; a 401/403 on both paths is an authentication result, not
proof that either route is invalid.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp

from vi_api_client import OAuth
from vi_api_client.const import API_BASE_URL, ENDPOINT_INSTALLATIONS
from vi_api_client.credentials import CredentialDocument
from vi_api_client.exceptions import ViAuthError, ViError
from vi_api_client.utils import mask_pii

DEFAULT_REDIRECT_URI = "http://localhost:4200/"
DEFAULT_TOKEN_FILE = "tokens.json"

# Both candidate spellings, in announcement-then-portal order. The labels
# intentionally keep the two paths distinguishable in the report.
CANDIDATE_PATHS = (
    (
        "announcement",
        "/iot/v2/events-history/installations/{installation_id}/events",
    ),
    (
        "portal-export",
        "/iot/v2/eventhistory/installations/{installation_id}/events",
    ),
)


def _sanitize(text: str) -> str:
    """Return one probe text with every identifier-shaped run redacted.

    ``mask_pii`` covers its documented URL, JSON, and label contexts; the
    additional digit-run replacement closes remaining phrasings that could
    disclose an installation ID in provider free text.
    """
    return re.sub(r"\d{4,}", "****", mask_pii(text))


def _parse_args() -> argparse.Namespace:
    """Return the probe's command line configuration."""
    parser = argparse.ArgumentParser(
        description="Probe both candidate event-history GET paths read-only"
    )
    parser.add_argument(
        "--installation-id",
        type=int,
        help="Installation ID to probe (default: first installation of the account)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Rolling lookback window in days sent as lastNDays (default: 7)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Page limit sent as limit (default: 10, API maximum: 1000)",
    )
    parser.add_argument(
        "--token-file",
        default=DEFAULT_TOKEN_FILE,
        help="Path to the credential document written by vi-client login",
    )
    parser.add_argument("--client-id", help="OAuth client ID (optional if saved)")
    parser.add_argument("--redirect-uri", help="OAuth redirect URI (optional if saved)")
    return parser.parse_args()


def _client_config(args: argparse.Namespace) -> tuple[str, str]:
    """Return the OAuth client ID and redirect URI for the probe.

    Raises:
        ViAuthError: If the credential document is unreadable or malformed.
        ValueError: If no usable client ID is configured.
    """
    config = CredentialDocument(Path(args.token_file)).read()

    client_id = args.client_id or os.getenv("VIESSMANN_CLIENT_ID")
    if not client_id and isinstance(config.get("client_id"), str):
        client_id = config["client_id"]
    if not client_id:
        raise ValueError(
            "No client ID found; pass --client-id or run vi-client login first"
        )

    redirect_uri = args.redirect_uri or os.getenv("VIESSMANN_REDIRECT_URI")
    if not redirect_uri and isinstance(config.get("redirect_uri"), str):
        redirect_uri = config["redirect_uri"]
    return client_id, redirect_uri or DEFAULT_REDIRECT_URI


async def _resolve_installation_id(
    auth: OAuth, requested_id: int | None
) -> tuple[str, str]:
    """Return the installation ID to probe and how it was determined."""
    if requested_id is not None:
        return str(requested_id), "requested"
    url = f"{API_BASE_URL}{ENDPOINT_INSTALLATIONS}"
    async with await auth.request("GET", url) as response:
        body = await _read_json(response)
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise ValueError("No installations found for this account")
    first_id = data[0].get("id")
    if isinstance(first_id, bool) or not isinstance(first_id, (str, int)):
        raise ValueError("First installation has no usable ID")
    return str(first_id), "first-installation"


async def _read_json(response: aiohttp.ClientResponse) -> Any:
    """Return the response body as parsed JSON, or None when unreadable."""
    try:
        return await response.json()
    except aiohttp.ClientError, ValueError:
        return None


async def _probe_path(
    auth: OAuth, path_template: str, installation_id: str, days: int, limit: int
) -> dict[str, Any]:
    """Return one sanitized probe result for a single candidate path."""
    query = urlencode({"lastNDays": days, "limit": limit})
    url = (
        f"{API_BASE_URL}{path_template.format(installation_id=installation_id)}?{query}"
    )
    result: dict[str, Any] = {
        "path": path_template.format(installation_id="<installationId>"),
        "httpStatus": None,
        "apiErrorType": None,
        "hasDataList": False,
        "hasCursorObject": False,
        "hasCursorNext": False,
        "eventCount": None,
        "firstEventFields": None,
        "requestError": None,
    }
    try:
        async with await auth.request("GET", url) as response:
            result["httpStatus"] = response.status
            body = await _read_json(response)
    except ViError as error:
        result["requestError"] = _sanitize(str(error))
        return result

    if isinstance(body, dict):
        # Provider free text is not part of the recorded evidence and stays
        # unread; the structured error type is sufficient for the report.
        error_type = body.get("errorType")
        result["apiErrorType"] = error_type if isinstance(error_type, str) else None

        data = body.get("data")
        result["hasDataList"] = isinstance(data, list)
        if isinstance(data, list):
            result["eventCount"] = len(data)
            first_event = next(
                (event for event in data if isinstance(event, dict)), None
            )
            if first_event is not None:
                # Field names only; values may contain unredacted event data.
                result["firstEventFields"] = sorted(first_event)

        cursor = body.get("cursor")
        result["hasCursorObject"] = isinstance(cursor, dict)
        if isinstance(cursor, dict):
            # Presence only; cursor values are opaque and stay unread.
            result["hasCursorNext"] = isinstance(cursor.get("next"), str)
    return result


def _recommendation(results: list[dict[str, Any]]) -> str:
    """Return a sanitized route decision suggestion from the probe results.

    A path is usable only when the public client would accept its response:
    a 2xx status, a ``data`` list, and either no cursor object or a cursor
    whose ``next`` is a string, matching ``EventHistoryPage`` validation.
    """
    usable = [
        result
        for result in results
        if isinstance(result["httpStatus"], int)
        and 200 <= result["httpStatus"] < 300
        and result["hasDataList"]
        and (not result["hasCursorObject"] or result["hasCursorNext"])
    ]
    statuses = {result["httpStatus"] for result in results}
    if not usable and statuses and statuses <= {401, 403}:
        return (
            "Both paths returned an authentication failure; this is an "
            "authentication result, not proof that either route is invalid. "
            "Fix credentials and rerun before deciding."
        )
    portal_first = sorted(
        usable,
        key=lambda result: 0 if "eventhistory" in result["path"] else 1,
    )
    if portal_first:
        preferred = portal_first[0]["path"]
        note = (
            "Both paths are usable; the portal-export spelling is preferred."
            if len(usable) > 1
            else "Only this path returned a usable event page."
        )
        return f"Route on: {preferred}. {note}"
    return (
        "No path returned a successful event page; keep the production route "
        "undecided and attach this output to the PR for discussion."
    )


async def async_main() -> int:
    """Run both probes once and print one sanitized JSON report."""
    args = _parse_args()
    try:
        client_id, redirect_uri = _client_config(args)
    except ViAuthError as error:
        print(f"Error: {_sanitize(str(error))}", file=sys.stderr)
        return 1
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    async with aiohttp.ClientSession() as session:
        auth = OAuth(
            client_id,
            redirect_uri,
            Path(args.token_file),
            websession=session,
        )
        try:
            installation_id, source = await _resolve_installation_id(
                auth, args.installation_id
            )
        except ViError as error:
            print(
                f"Error resolving installation: {_sanitize(str(error))}",
                file=sys.stderr,
            )
            return 1
        except ValueError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1

        results: list[dict[str, Any]] = []
        for label, path_template in CANDIDATE_PATHS:
            result = await _probe_path(
                auth, path_template, installation_id, args.days, args.limit
            )
            result["candidate"] = label
            results.append(result)

    report = {
        "query": {"lastNDays": args.days, "limit": args.limit},
        "installationIdSource": source,
        "results": results,
        "recommendation": _recommendation(results),
    }
    print(json.dumps(report, indent=2))
    return 0


def main() -> None:
    """Entry point for the probe script."""
    raise SystemExit(asyncio.run(async_main()))


if __name__ == "__main__":
    main()
