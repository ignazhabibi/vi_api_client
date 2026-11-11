# Viessmann API Client

This context defines the language used to describe Viessmann installations and
their feature data within the client library.

## Language

**Gateway-scoped device refresh**:
A coordinated refresh of the available features of known devices
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

**Available feature**:
A device feature that is enabled and ready, reported by `Feature.is_available`.
Reads default to available features; callers request disabled or not-ready
features explicitly.
_Avoid_: Active feature

**Settable feature**:
A writable, available feature, reported by `Feature.is_settable`: `set_feature`
would accept it in this snapshot.
_Avoid_: Enabled-for-write

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

**API feature**:
One named feature object in a Viessmann API response, such as
`heating.circuits.0.heating.curve`. Parsing turns it into one or more features,
such as `heating.circuits.0.heating.curve.slope` and `...shift`; when it only
reports a `value`, its single feature keeps the API feature name.
_Avoid_: Raw feature name, base name

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
A feature that reports `is_writable=True` because the parser attached an
internal write binding recording how its command writes the value: a parameter
command, or an on/off command pair for a boolean without a parameter setter.
The binding travels with the feature, so copies stay writable and hand-built
features are read-only.
_Avoid_: Control block, command metadata object

**On/off command pair**:
Two commands without required parameters that switch one boolean property on
and off, such as `enable` and `disable` for `heating.dhw.hygiene.enabled`.
The library's explicit pair table decides which pairs make a feature writable;
reported executability does not.
_Avoid_: Toggle command, switch command

**Feature constraints**:
The `FeatureConstraints` rules the API reports for writing one feature value
(ranges, step, options, lengths, pattern, value type, schedule rules) on
`Feature.constraints`.
_Avoid_: Control metadata, command constraints bundle

**Device command list**:
The per-API-feature record of every reported command and its parameter
metadata a device snapshot carries; `Device.get_command_names` reads it. It
stores neither executability nor deprecation.
_Avoid_: Command registry

**Setter command**:
A command whose name starts with `set`. Only a setter binds a value to a
feature; a command such as `schedule` or `triggerDaily` does more than store a
value, leaves its features read-only, and is sent with `run_command`, for
example `run_command(device, "heating.operating.programs.holiday",
"schedule", {"start": "2026-12-24", "end": "2026-12-31"})`.
_Avoid_: Write command, value command

**Feature command**:
The API operation that sends command parameters for a writable feature. How
the library sends it is internal command knowledge; it is not the executed
command.

**Command rejection**:
The API's refusal of a sent command, reported by `ViCommandRejectedError` with
a `message` and a `reason`. A successful write raises nothing, so success
always means that no exception was raised.

**Device communication error**:
An error response the API classifies as `DEVICE_COMMUNICATION_ERROR`,
reported by `ViDeviceCommunicationError` whatever its HTTP status: an
unreachable device or gateway is not a mistake in the request.

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
