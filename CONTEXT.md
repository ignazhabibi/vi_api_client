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
