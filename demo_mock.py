"""Viessmann Library Demo Application.

This script demonstrates:
1. The **Data Layers** (Raw -> Model -> Flat -> Command) for deep understanding.
2. The **Full Installation Status** fetch (Coordinator Pattern) used by Home Assistant.

Usage:
    python demo_mock.py
"""

import asyncio
import contextlib
import json
import logging
import os
import sys

# Ensure we can import the local package
sys.path.insert(0, os.path.abspath("src"))

from vi_api_client import MockViClient, OAuth
from vi_api_client.models import Device
from vi_api_client.utils import format_feature

# Configure formatted logging
logging.basicConfig(format="%(message)s", level=logging.INFO)


async def main():  # noqa: PLR0915
    """Run the mock demo."""
    print("🚀 Viessmann Library - Comprehensive Demo")
    print("=======================================")

    # 1. Initialization
    # -----------------
    auth = OAuth("client_id", "redirect_url", "tokens.json")
    client = MockViClient("Vitodens200W", auth)

    inst_id = "123"
    gw_serial = "1234567890123456"
    dev_id = "0"
    TARGET_FEATURE = "heating.circuits.0.heating.curve"

    # Create a Device object for API calls
    device = Device(
        id=dev_id,
        gateway_serial=gw_serial,
        installation_id=inst_id,
        model_id="Vitodens200W",
        device_type="heating",
        status="Online",
    )

    print("\n[PART 1] Understanding Data Layers (Single Feature)")
    print("---------------------------------------------------")
    print(f"Target: {TARGET_FEATURE}")

    # Layer 1: MODEL (get_feature now returns Feature object)
    print("\n1. MODEL Layer (get_feature)")
    print("   -> Returns a Feature object directly.")
    feature_model = await client.get_feature(device, TARGET_FEATURE)
    print(f"   Object: {feature_model.name}")
    print(f"   Properties: {list(feature_model.properties.keys())}")

    # Layer 2: FLAT / EXPANDED
    print("\n2. FLAT Layer (expand())")
    print("   -> Returns simple, scalar features (Sensors) for Home Assistant.")
    flat_features = feature_model.expand()
    for f in flat_features:
        print(f"   - Entity: {f.name:<45} | Value: {format_feature(f)}")

    # Layer 4: COMMAND
    print("\n4. COMMAND Layer (Inspection & Execution)")
    print("   -> Inspect capabilities and execute actions.")
    if feature_model.commands:
        for cmd_name, cmd in feature_model.commands.items():
            params = list(cmd.params.keys())
            print(
                f"   - {cmd_name}({', '.join(params)}) "
                f"[Executable: {cmd.is_executable}]"
            )
            if cmd_name == "setCurve":
                print(f"     Constraints: {json.dumps(cmd.params, indent=2)}")

    print("\n   [Execution Demo]")
    print("   Executing 'setCurve' with slope=1.4, shift=0...")
    try:
        result = await client.execute_command(
            feature_model, "setCurve", slope=1.4, shift=0
        )
        print(f"   Result: {result}")
    except Exception as e:
        print(f"   Error: {e}")

    print("\n\n[PART 2] Full Installation Status (Coordinator Pattern)")
    print("-------------------------------------------------------")
    print("Fetching everything in one call (Gateways -> Devices -> Features)...")

    devices = await client.get_full_installation_status(installation_id=inst_id)

    print(f"✅ Received {len(devices)} device(s).\n")

    for i, device in enumerate(devices):
        print(f"🔹 Device #{i}: {device.model_id} (ID: {device.id})")
        print(f"   • Type:   {device.device_type}")
        print(f"   • Status: {device.status}")

        # Serial from features_flat
        serial = next(
            (f.value for f in device.features_flat if f.name == "device.serial"), "N/A"
        )
        print(f"   • Serial: {serial}")

        print(
            f"   • Features: {len(device.features)} (Model Objects) -> "
            f"{len(device.features_flat)} (Flat Sensors)"
        )

        print("\n   🔎 Sample Sensors (from .features_flat):")
        interesting_keys = [
            "heating.sensors.temperature.outside",
            "heating.boiler.sensors.temperature.commonSupply",
            "heating.circuits.0.heating.curve.slope",
        ]
        for f in device.features_flat:
            if f.name in interesting_keys:
                print(f"      - {f.name:<45} : {f.value} {f.unit}")

        print("\n   🛠  Available Commands (from .features):")
        for f in device.features:
            if f.commands:
                # Print concise command list
                cmds = []
                for c_name, cmd in f.commands.items():
                    mark = "✅" if cmd.is_executable else "❌"
                    cmds.append(f"{mark} {c_name}")
                print(f"      - {f.name}: {', '.join(cmds)}")

    print("\n" + "=" * 60)
    print("NOTE: This script uses the MOCK client (Offline).")
    print("=" * 60)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
