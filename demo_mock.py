"""Viessmann Library Demo Application (Mock).

Demonstrates the Flat Architecture with offline mock data.
"""

import asyncio
import contextlib
import logging
import os
import sys

sys.path.insert(0, os.path.abspath("src"))

from vi_api_client import MockViClient
from vi_api_client.models import Device
from vi_api_client.utils import format_feature

logging.basicConfig(format="%(message)s", level=logging.INFO)


def print_features(features, title, limit=10):
    """Helper to print features list."""
    print(f"\n{title}")
    print("-" * len(title))
    for i, feature in enumerate(features[:limit]):
        if i >= limit:
            break
        val = format_feature(feature)
        if len(val) > 50:
            val = val[:47] + "..."
        marker = " ✏️" if feature.is_writable else ""
        print(f"      {feature.name:<50} : {val}{marker}")

    if len(features) > limit:
        print(f"   ... and {len(features) - limit} more.")


def print_writable_details(features, limit=5):
    """Helper to print writable feature details."""
    print("\n🛠  Writable Features")
    print("-------------------")
    writable = [f for f in features if f.is_writable]
    print(f"Found {len(writable)} writable features:\n")

    for i, feature in enumerate(writable[:limit]):
        ctrl = feature.control
        print(f"   {i + 1}. {feature.name}")
        print(f"      Command: {ctrl.command_name}, Param: {ctrl.param_name}")

        # Collect all constraints in a data-driven way
        constraint_attrs = [
            ("min", ctrl.min),
            ("max", ctrl.max),
            ("step", ctrl.step),
            ("options", ctrl.options),
            ("pattern", ctrl.pattern),
            ("min_length", ctrl.min_length),
            ("max_length", ctrl.max_length),
        ]

        constraints = [
            f"{name}={value}" for name, value in constraint_attrs if value is not None
        ]
        if constraints:
            print(f"      Constraints: {', '.join(constraints)}")

    if len(writable) > limit:
        print(f"\n   ... and {len(writable) - limit} more.")


async def main():
    """Run the mock demo."""
    print("🚀 Viessmann Mock Demo (Flat Architecture)")
    print("=" * 50)

    client = MockViClient("Vitodens200W")
    device = Device(
        id="0",
        gateway_serial="MOCK_GW",
        installation_id="123",
        model_id="Vitodens200W",
        device_type="heating",
        status="Online",
    )

    # Fetch features
    features = await client.get_features(device, only_enabled=True)
    print(f"\n✅ Fetched {len(features)} enabled features from mock data.")

    # Display samples
    print_features(features, "📋 Sample Features (first 10)", limit=10)

    # Display writable features
    print_writable_details(features, limit=5)

    # Get specific feature
    print("\n\n📍 Get Specific Feature")
    print("----------------------")
    temp_features = await client.get_features(
        device, feature_names=["heating.sensors.temperature.outside"]
    )
    if temp_features:
        f = temp_features[0]
        print(f"   {f.name}: {format_feature(f)}")

    print("\n" + "=" * 50)
    print("Mock data from: fixtures/Vitodens200W.json")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
