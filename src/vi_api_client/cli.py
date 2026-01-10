"""CLI for Viessmann Client."""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Optional, Dict, Any

import aiohttp
from vi_api_client import Client, OAuth

# Default file to store tokens and config
TOKEN_FILE = "tokens.json"

logging.basicConfig(level=logging.INFO, format='%(message)s')
_LOGGER = logging.getLogger(__name__)

def load_config(token_file: str) -> Dict[str, Any]:
    """Load configuration from token file."""
    try:
        with open(token_file, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_config(token_file: str, config: Dict[str, Any]) -> None:
    """Save configuration to token file."""
    with open(token_file, "w") as f:
        json.dump(config, f, indent=2)

async def create_session(args) -> aiohttp.ClientSession:
    """Create aiohttp session with optional insecure SSL."""
    if args.insecure:
        print("WARNING: SSL verification disabled via --insecure")
        connector = aiohttp.TCPConnector(ssl=False)
        return aiohttp.ClientSession(connector=connector)
    return aiohttp.ClientSession()

async def cmd_login(args):
    """Handle login command."""
    client_id = args.client_id
    redirect_uri = args.redirect_uri
    
    
    auth = OAuth(client_id, redirect_uri, args.token_file)
    url = auth.get_authorization_url()
    
    print(f"Please visit the following URL to log in:\n\n{url}\n")
    print(f"After verifying, you will be redirected to {redirect_uri}?code=...")
    code = input("Paste the 'code' parameter from the URL here: ").strip()
    
    async with await create_session(args) as session:
        auth.websession = session
        await auth.async_fetch_details_from_code(code)
        
    print(f"Successfully authenticated! Tokens and config saved to {args.token_file}")

def get_client_config(args) -> tuple[str, str]:
    """Get client_id and redirect_uri from args or file."""
    config = load_config(args.token_file)
    
    client_id = args.client_id or os.getenv("VIESSMANN_CLIENT_ID") or config.get("client_id")
    redirect_uri = args.redirect_uri or os.getenv("VIESSMANN_REDIRECT_URI") or config.get("redirect_uri")
    
    if not client_id:
        print(f"Error: Client ID not found. Provide via --client-id or VIESSMANN_CLIENT_ID env var.")
        print("Please run 'login' first or provide --client-id.")
        sys.exit(1)
        
    return client_id, redirect_uri

async def cmd_list_devices(args):
    """List installations and devices."""
    client_id, redirect_uri = get_client_config(args)

    async with await create_session(args) as session:
        auth = OAuth(client_id, redirect_uri, args.token_file, session)
        client = Client(auth)

        try:
            installations = await client.get_installations()
            print(f"Found {len(installations)} Installations:")
            for inst in installations:
                inst_id = inst.get("id")
                print(f"- Installation ID: {inst_id}")
                
            gateways = await client.get_gateways()
            print(f"\nFound {len(gateways)} Gateways:")
            for gw in gateways:
                gw_serial = gw.get("serial")
                inst_id = gw.get("installationId")
                print(f"- Gateway: {gw_serial} (Inst: {inst_id})")
                
                devices = await client.get_devices(inst_id, gw_serial)
                print(f"  Devices ({len(devices)}):")
                for dev in devices:
                    dev_id = dev.get("id")
                    dev_type = dev.get("deviceType")
                    model = dev.get("modelId")
                    print(f"  - Device: {dev_id} (Type: {dev_type}, Model: {model})")
                    
        except Exception as e:
            _LOGGER.error("Error listing devices: %s", e)

async def cmd_list_features(args):
    """List all features for a device."""
    client_id, redirect_uri = get_client_config(args)

    async with await create_session(args) as session:
        auth = OAuth(client_id, redirect_uri, args.token_file, session)
        client = Client(auth)
        
        try:
            # Auto-discovery if IDs are missing
            inst_id = args.installation_id
            gw_serial = args.gateway_serial
            dev_id = args.device_id
            
            if not (inst_id and gw_serial and dev_id):
                gateways = await client.get_gateways()
                if not gateways:
                    print("No gateways found.")
                    return
                gw = gateways[0]
                gw_serial = gw.get("serial")
                inst_id = gw.get("installationId")
                
                devices = await client.get_devices(inst_id, gw_serial)
                if not devices:
                    print(f"No devices found on gateway {gw_serial}")
                    return
                # Default to device 0 if available, else first one
                target_dev = next((d for d in devices if d.get("id") == "0"), devices[0])
                dev_id = target_dev.get("id")
                print(f"Using Device: {dev_id} (Gateway: {gw_serial}, Inst: {inst_id})")

            
            if args.values:
                # Use model-based fetching to show flat/expanded features
                # accessible to the end user (simulating HA behavior)
                features_models = await client.get_features_models(inst_id, gw_serial, dev_id)
                # We create a dummy device to leverage the property (or just call expand manually)
                # But to fully verify, let's just iterate and expand
                
                print(f"Found {len(features_models)} Raw Features for device {dev_id}:")
                
                flat_list = []
                for f in features_models:
                    if args.enabled and not f.is_enabled:
                        continue
                    flat_list.extend(f.expand())
                    
                print(f"Expanded to {len(flat_list)} Flat Features:")
                for item in flat_list:
                     print(f"- {item.name}: {item.formatted_value}")
            
            else:
                # Standard listing (names only)
                if args.enabled:
                    features = await client.get_enabled_features(inst_id, gw_serial, dev_id)
                    print(f"Found {len(features)} Enabled Features for device {dev_id}:")
                else:
                    features = await client.get_features(inst_id, gw_serial, dev_id)
                    print(f"Found {len(features)} Features for device {dev_id}:")
                
                for f in features:
                    print(f"- {f.get('feature')}")
        except Exception as e:
            _LOGGER.error("Error listing features: %s", e)


async def cmd_get_feature(args):
    """Get a specific feature."""
    client_id, redirect_uri = get_client_config(args)

    async with await create_session(args) as session:
        auth = OAuth(client_id, redirect_uri, args.token_file, session)
        client = Client(auth)
        
        try:
            # Auto-discovery if IDs are missing
            inst_id = args.installation_id
            gw_serial = args.gateway_serial
            dev_id = args.device_id
            
            if not (inst_id and gw_serial and dev_id):
                # We need to discover context
                # To capture stdout/logging if needed, but for simple CLI we just call methods
                gateways = await client.get_gateways()
                if not gateways:
                    print("No gateways found.")
                    return
                
                # Pick first gateway
                gw = gateways[0]
                gw_serial = gw.get("serial")
                inst_id = gw.get("installationId")
                
                # Pick priority device (0) over default first found
                devices = await client.get_devices(inst_id, gw_serial)
                if not devices:
                    print(f"No devices found on gateway {gw_serial}")
                    return
                
                target_dev = next((d for d in devices if d.get("id") == "0"), devices[0])
                dev_id = target_dev.get("id")

            feature = await client.get_feature(inst_id, gw_serial, dev_id, args.feature_name)
            print(json.dumps(feature, indent=2))
        except Exception as e:
            _LOGGER.error("Error fetching feature: %s", e)

def main():
    """Main CLI entrypoint."""
    # Parent parser for common arguments
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--client-id", help="OAuth Client ID (optional if saved)")
    common_parser.add_argument("--redirect-uri", default="http://localhost:4200/", help="OAuth Redirect URI")
    common_parser.add_argument("--token-file", default=TOKEN_FILE, help="Path to save/load tokens")
    common_parser.add_argument("--insecure", action="store_true", help="Disable SSL verification")

    parser = argparse.ArgumentParser(description="Viessmann API CLI")
    subparsers = parser.add_subparsers(dest="command")

    
    # Login
    parser_login = subparsers.add_parser("login", help="Authenticate with Viessmann", parents=[common_parser])
    
    # List Devices
    parser_list = subparsers.add_parser("list-devices", help="List installations and devices", parents=[common_parser])

    # List Features
    parser_features = subparsers.add_parser("list-features", help="List all features for a device", parents=[common_parser])
    parser_features.add_argument("--installation-id", type=int, help="Installation ID (optional)")
    parser_features.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    parser_features.add_argument("--device-id", help="Device ID (optional)")
    parser_features.add_argument("--enabled", action="store_true", help="List only enabled features")
    parser_features.add_argument("--values", action="store_true", help="Show feature values")
    
    # Get Feature
    parser_feature = subparsers.add_parser("get-feature", help="Get a specific feature", parents=[common_parser])
    parser_feature.add_argument("feature_name", help="Feature Name (e.g. heating.circuits.0)")
    parser_feature.add_argument("--installation-id", type=int, help="Installation ID (optional)")
    parser_feature.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    parser_feature.add_argument("--device-id", help="Device ID (optional)")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return

    try:
        if args.command == "login":
            if not args.client_id and not os.getenv("VIESSMANN_CLIENT_ID"):
                # Check config one last time before failing
                config = load_config(args.token_file)
                if not config.get("client_id"):
                    print("Error: --client-id is required for initial login (or use VIESSMANN_CLIENT_ID env var).")
                    sys.exit(1)
            asyncio.run(cmd_login(args))
        elif args.command == "list-devices":
            asyncio.run(cmd_list_devices(args))
        elif args.command == "list-features":
            asyncio.run(cmd_list_features(args))
        elif args.command == "get-feature":
            asyncio.run(cmd_get_feature(args))
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
