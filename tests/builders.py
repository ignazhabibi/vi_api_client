"""Model builders shared by several test modules."""

from vi_api_client.models import Device, Gateway, Installation


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


def build_installation(installation_id: str) -> Installation:
    """Build one installation as account discovery returns it."""
    return Installation(
        id=installation_id, description="Home", alias="home", address={}
    )


def build_gateway(serial: str, installation_id: str) -> Gateway:
    """Build one gateway as account discovery returns it."""
    return Gateway(
        serial=serial, version="1.0", status="ok", installation_id=installation_id
    )


def build_device(device_id: str, installation_id: str, gateway_serial: str) -> Device:
    """Build one unhydrated device as gateway discovery returns it."""
    return Device(
        id=device_id,
        gateway_serial=gateway_serial,
        installation_id=installation_id,
        model_id=f"model-{device_id}",
        device_type="heating",
        status="ok",
    )
