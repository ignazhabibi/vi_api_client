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
