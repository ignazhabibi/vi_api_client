"""Model builders shared by several test modules."""

from vi_api_client.models import Device


def build_gateway_device(device_id: str) -> Device:
    """Build an unhydrated device on the shared test installation and gateway."""
    return Device(
        id=device_id,
        gateway_serial="gateway-1",
        installation_id="installation-1",
        model_id=f"model-{device_id}",
        device_type="heating",
        status="connected",
    )
