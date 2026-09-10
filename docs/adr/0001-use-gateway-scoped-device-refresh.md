---
status: accepted
---

# Use an explicit gateway-scoped device refresh with partial results

`vi_api_client` will expose `update_gateway_devices(devices)` for refreshing the
enabled and ready features of known devices through the gateway-scoped bulk
endpoint. The method accepts devices from exactly one installation and gateway
because the bulk response does not provide enough metadata to reconstruct a
`Device`, and it returns a `GatewayDeviceRefreshResult` that separates updated
devices from device-specific errors so that one device cannot fail the whole
batch.

The existing list-returning discovery and refresh methods retain their current
behavior. The new operation uses the API's POST filter form, performs targeted
per-device fallback for missing devices and device-specific communication
errors, exposes Viessmann's `errorType` on library exceptions, and does not add
a library cache; consumers remain responsible for retaining stale state and
interpreting partial availability.
