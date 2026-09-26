# Viessmann API Client

This context defines the language used to describe Viessmann installations and
their feature data within the client library.

## Language

**Gateway-scoped device refresh**:
A coordinated refresh of the enabled and ready features of known devices
belonging to one gateway.
_Avoid_: Gateway service, gateway feature snapshot

**Gateway device refresh result**:
The outcome of a gateway-scoped device refresh, separating successfully
refreshed devices from device-specific failures.
_Avoid_: Gateway device snapshot, mixed device list

**Bulk feature fetch**:
The retrieval of device features through a gateway-scoped API operation rather
than through one request per device.
_Avoid_: Gateway feature fetch

**Enabled and ready feature**:
A device feature that is both enabled by the device configuration and ready for
interaction at the time it is retrieved.
_Avoid_: Active feature

**Device snapshot**:
The immutable representation of a device and the features known when that
snapshot was created. An empty feature collection does not reveal whether no
feature response has been applied or whether the response contained no
features.
_Avoid_: Hydrated device

**Device feature hydration**:
The operation of producing a new device snapshot from a feature response. It
is an operation, not an observable device state.
_Avoid_: Device hydration state

**Refreshed device snapshot**:
A device snapshot whose feature values came from an API read response.

**Command-updated device snapshot**:
A device snapshot updated locally after a successful feature command response,
without a subsequent API read-back.
_Avoid_: Optimistic update

**Feature**:
One flat, addressable device property with its reported value and capabilities.

**Feature value**:
The `FeatureValue` data reported for a feature. Its concrete shape follows the
recursive `JsonValue` contract: scalar, no value, list, or string-keyed object.
_Avoid_: Raw value, arbitrary value

**JSON value contract**:
The public recursive `JsonValue` type describing every dynamically shaped value
the library accepts or exposes: null, booleans, finite numbers, strings, lists,
and string-keyed objects. See ADR 0003.
_Avoid_: Arbitrary JSON, untyped payload

**Validation boundary**:
The explicit trust boundary where live responses, OAuth responses, credential
documents, and bundled fixture data are validated field by field before use.
Known malformed fields fail loudly; unknown additional fields stay allowed.
_Avoid_: Schema enforcement, payload parsing

**Validation detail**:
A `ValidationDetail` entry describing one field-level API validation problem as
string-keyed JSON data, carried by `ViValidationError` as `validation_errors`.
_Avoid_: Validation error payload

**Writable feature**:
A feature that reports `is_writable=True` and has `FeatureControl` metadata
describing how a feature command can target it.

**Feature command**:
The API operation that sends command parameters for a writable feature.
`FeatureControl` is the existing Python class name for command metadata; it is
not the executed command.

**Live client**:
`ViClient` configured with authentication that reads from the Viessmann API.

**Fixture-backed client**:
`FixtureViClient`, which runs the public client workflows against bundled fixture
responses without authentication or network requests.

**Credential document**:
The JSON file shared by OAuth token persistence and CLI authentication
configuration. A missing document is empty; a corrupted document raises an
error and is neither treated as empty nor silently overwritten.

**Installation event**:
One entry of an installation's event history. Known fields are validated at
the trust boundary while the complete event mapping, including unknown fields
and the event body, stays available to callers.
_Avoid_: History record, log entry

**Event history page**:
One page of an installation's event history: the reported events and the
opaque continuation cursor for the next page, when the provider reported one.
_Avoid_: Event list snapshot

**Event history window**:
The complete traversal result for one bounded lookback request: every event
collected while following the continuation cursor up to the page safety
limit, plus the remaining cursor when the traversal stopped there. A completed
window traversal reports exhausted pagination, not proven provider retention
of the requested lookback.
_Avoid_: Full history, complete archive
