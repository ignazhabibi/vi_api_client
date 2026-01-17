"""CLI for Viessmann Client."""

import argparse
import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional, Dict, Any, Union, AsyncGenerator

import aiohttp
from vi_api_client import (
    ViClient, 
    MockViClient, 
    OAuth,
    ViValidationError,
    ViNotFoundError,
    ViAuthError,
    ViRateLimitError
)

# Default file to store tokens and config
TOKEN_FILE = "tokens.json"

logging.basicConfig(level=logging.INFO, format='%(message)s')
_LOGGER = logging.getLogger(__name__)

@dataclass
class CLIContext:
    session: aiohttp.ClientSession
    client: Union[ViClient, MockViClient]
    # Found IDs (either from args or auto-discovery)
    inst_id: str
    gw_serial: str
    dev_id: str

def load_config(token_file: str) -> Dict[str, Any]:
    """Load configuration from token file."""
    try:
        with open(token_file, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


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

def get_client_config_safe(args) -> tuple[str, str]:
    """Get config but return empty strings if missing (for mock mode)."""
    # For mock mode, we don't strictly need client_id, so we can be lenient.
    if args.mock_device:
        return "mock_id", "mock_uri"
    return get_client_config(args)

@asynccontextmanager
async def setup_client_context(args, discover: bool = True) -> AsyncGenerator[CLIContext, None]:
    """
    Creates Session, Auth, Client AND performs Auto-Discovery if needed.
    """
    client_id, redirect_uri = get_client_config_safe(args)
    
    async with await create_session(args) as session:
        auth = OAuth(client_id, redirect_uri, args.token_file, session)
        
        # Initialize Client
        if args.mock_device:
            client = MockViClient(args.mock_device, auth)
            # Default mock IDs
            inst_id = getattr(args, "installation_id", None) or "99999"
            gw_serial = getattr(args, "gateway_serial", None) or "MOCK_GATEWAY"
            dev_id = getattr(args, "device_id", None) or "0"
            print(f"Using Mock Device: {args.mock_device}")
        else:
            client = ViClient(auth)
            inst_id = getattr(args, "installation_id", None)
            gw_serial = getattr(args, "gateway_serial", None)
            dev_id = getattr(args, "device_id", None)

            # Perform Auto-Discovery if IDs are missing
            if discover and not (inst_id and gw_serial and dev_id):
                gateways = await client.get_gateways()
                if not gateways:
                    print("No gateways found.")
                    raise ValueError("No gateways found.")
                
                if not gw_serial:
                    gw_serial = gateways[0].serial
                if not inst_id:
                    inst_id = gateways[0].installation_id
                
                if not dev_id:
                    devices = await client.get_devices(inst_id, gw_serial)
                    if not devices:
                        raise ValueError("No devices found.")
                    
                    # Prefer Device "0" (Heating System)
                    # Prefer Device "0" (Heating System)
                    target_dev = next((d for d in devices if d.id == "0"), devices[0])
                    dev_id = target_dev.id
                    print(f"Auto-selected Context: Inst={inst_id}, GW={gw_serial}, Dev={dev_id}")

        yield CLIContext(session, client, inst_id, gw_serial, dev_id)

async def cmd_list_devices(args):
    """List installations and devices."""
    # Does not use full context discovery, just client
    async with setup_client_context(args, discover=False) as ctx:
        try:
            installations = await ctx.client.get_installations()
            print(f"Found {len(installations)} installations:")
            for inst in installations:
                print(f"- ID: {inst.id}, Description: {inst.description}, Alias: {inst.alias}")
                
            gateways = await ctx.client.get_gateways()
            print(f"\nFound {len(gateways)} gateways:")
            for gw in gateways:
                print(f"- Serial: {gw.serial} (Inst: {gw.installation_id}), Version: {gw.version}, Status: {gw.status}")
                
                devices = await ctx.client.get_devices(gw.installation_id, gw.serial)
                print(f"Found {len(devices)} devices:")
                for device in devices:
                    print(f"- ID: {device.id}, Model: {device.model_id}, Type: {device.device_type}, Status: {device.status}")
                    
        except Exception as e:
            _LOGGER.error("Error listing devices: %s", e)

async def cmd_list_features(args):
    """List all features for a device."""
    try:
        async with setup_client_context(args) as ctx:
            # Transient Device for API call
            from vi_api_client.models import Device
            device = Device(
                id=ctx.dev_id, 
                gateway_serial=ctx.gw_serial, 
                installation_id=ctx.inst_id, 
                model_id="transient", 
                device_type="unknown", 
                status="online"
            )

            if args.values:
                features_models = await ctx.client.get_features(
                    device,
                    only_enabled=args.enabled
                )
                
                # Expand features
                flat_list = []
                for f in features_models:
                    flat_list.extend(f.expand())
                
                if args.json:
                     # Output clean JSON list of objects
                     from vi_api_client.utils import format_feature
                     out_data = [{"name": item.name, "value": item.value, "unit": item.unit, "formatted": format_feature(item)} for item in flat_list]
                     print(json.dumps(out_data))
                else:
                    print(f"Found {len(features_models)} Raw Features for device {ctx.dev_id} (expanded to {len(flat_list)}):")
                    from vi_api_client.utils import format_feature
                    for item in flat_list:
                        val = format_feature(item)
                        if len(val) > 80:
                            val = val[:77] + "..."
                        print(f"- {item.name:<75}: {val}")
            
            else:
                # Simple Listing
                only_enabled = args.enabled
                # Transient Device reuse if possible or new
                if 'device' not in locals():
                     from vi_api_client.models import Device
                     device = Device(
                        id=ctx.dev_id, gateway_serial=ctx.gw_serial, installation_id=ctx.inst_id, 
                        model_id="transient", device_type="unknown", status="online"
                     )
                     
                features = await ctx.client.get_features(
                     device, only_enabled=only_enabled
                )
                
                if args.json:
                    # Depending on structure, but simple list of names for piping
                    print(json.dumps([f.name for f in features]))
                else: 
                     print(f"Found {len(features)} Features for device {ctx.dev_id}:")
                     for f in features:
                        print(f"- {f.name}")
                        
    except Exception as e:
        _LOGGER.error("Error listing features: %s", e)


async def cmd_get_feature(args):
    """Get a specific feature."""
    try:
        async with setup_client_context(args) as ctx:
            from vi_api_client.models import Device
            device = Device(
                id=ctx.dev_id, gateway_serial=ctx.gw_serial, installation_id=ctx.inst_id, 
                model_id="transient", device_type="unknown", status="online"
            )
            feature = await ctx.client.get_feature(device, args.feature_name)
            
            if args.raw:
                # We need to access underlying properties/structure if user wants raw
                # But feature is now an object. 
                # We can't return raw JSON easily unless we keep a raw dict or implement to_dict
                # However, args.raw is rarely used except for debugging.
                # Let's check api.py implementation. It just does Feature.from_api(data).
                # The data is lost unless stored.
                # Actually, Feature has properties field. We can reconstruct basic structure.
                print(json.dumps({"feature": feature.name, "properties": feature.properties, "isEnabled": feature.is_enabled}, indent=2))
            else:
                expanded = feature.expand()
                
                if not expanded:
                     print(f"Feature '{args.feature_name}' exists but has no scalar values (Structural).")
                     print("Use --raw to see underlying structure.")
                
                for item in expanded:
                    from vi_api_client.utils import format_feature
                    print(f"- {item.name}: {format_feature(item)}")
    except ViNotFoundError:
        print(f"Feature '{args.feature_name}' not found.")
    except Exception as e:
        _LOGGER.error("Error fetching feature: %s", e)

async def cmd_get_consumption(args):
    """Get consumption data."""
    try:
        async with setup_client_context(args) as ctx:
            print(f"Fetching consumption (Metric: {args.metric})...")
            from vi_api_client.models import Device
            from datetime import datetime
             
            device = Device(
                id=ctx.dev_id, gateway_serial=ctx.gw_serial, installation_id=ctx.inst_id, 
                model_id="transient", device_type="unknown", status="online"
            )
            
            # Helper for CLI "today"
            now = datetime.now()
            start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            end_dt = now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

            result = await ctx.client.get_consumption(
                device, start_dt, end_dt, metric=args.metric
            )
            if isinstance(result, list):
                 print(f"Feature expanded to {len(result)} items:")
                 from vi_api_client.utils import format_feature
                 for f in result:
                     print(f"- {f.name}: {format_feature(f)}")
            else:
                 from vi_api_client.utils import format_feature
                 print(f"Feature: {result.name}")
                 print(f"Value: {format_feature(result)}")
    except Exception as e:
        _LOGGER.error("Error fetching consumption: %s", e)

async def cmd_exec(args):
    """Execute a command."""
    try:
        from vi_api_client.utils import parse_cli_params
        params = parse_cli_params(args.params)
    except ValueError as e:
        print(f"Error parsing parameters: {e}")
        return

    try:
        async with setup_client_context(args) as ctx:
            print(f"Fetching feature '{args.feature_name}'...")
            from vi_api_client.models import Device
            from vi_api_client.utils import format_feature, parse_cli_params

            if args.params:
                try:
                    params = parse_cli_params(args.params)
                except ValueError as e:
                    print(f"Error parsing parameters: {e}")
                    return
            else:
                params = {}

            device = Device(
                id=ctx.dev_id, gateway_serial=ctx.gw_serial, installation_id=ctx.inst_id, 
                model_id="transient", device_type="unknown", status="online"
            )
            feature = await ctx.client.get_feature(device, args.feature_name)
            
            print(f"Executing '{args.command_name}' with {params}...")
            result = await ctx.client.execute_command(feature, args.command_name, params)
            
            if result.success:
                print("✅ Success!")
            else:
                print("❌ Failed!")
            
            if result.message:
                print(f"Message: {result.message}")
            if result.reason:
                print(f"Reason: {result.reason}")
    except ViValidationError as e:
        print(f"Validation failed: {e}")
    except ViNotFoundError as e:
        print(f"Not found: {e}")
    except Exception as e:
        _LOGGER.error("Error executing command: %s", e)

async def cmd_list_commands(args):
    """List all available commands for a device."""
    try:
        async with setup_client_context(args) as ctx:
            # Fetch all features to introspect commands
            from vi_api_client.models import Device
            device = Device(
                id=ctx.dev_id, gateway_serial=ctx.gw_serial, installation_id=ctx.inst_id, 
                model_id="transient", device_type="unknown", status="online"
            )
            features = await ctx.client.get_features(device)
            
            commandable_features = [f for f in features if f.commands]
            
            print(f"\nFound {len(commandable_features)} features with commands:\n")
            
            for f in commandable_features:
                print(f"- {f.name}")
                for cmd_name, cmd in f.commands.items():
                    is_exec = "✅" if cmd.is_executable else "❌"
                    print(f"    Command: {cmd_name} {is_exec}")
                    
                    params = cmd.params
                    if params:
                        for p_name, p_def in params.items():
                            req_str = "*" if p_def.get("required") else ""
                            type_str = p_def.get("type", "unknown")
                            
                            c_def = p_def.get("constraints", {})
                            constraints = []
                            if "min" in c_def: constraints.append(f"min: {c_def['min']}")
                            if "max" in c_def: constraints.append(f"max: {c_def['max']}")
                            if "stepping" in c_def: constraints.append(f"step: {c_def['stepping']}")
                            if "enum" in c_def: constraints.append(f"enum: {c_def['enum']}")
                            if "regEx" in c_def: constraints.append(f"regex: {c_def['regEx']}")
                            if "minLength" in c_def: constraints.append(f"minLength: {c_def['minLength']}")
                            if "maxLength" in c_def: constraints.append(f"maxLength: {c_def['maxLength']}")
                            
                            print(f"      - {p_name}{req_str} ({type_str})")
                            if constraints:
                                print(f"          Constraints: {', '.join(constraints)}")
                    else:
                        print("      (No parameters)")
                print("")
    except Exception as e:
        _LOGGER.error("Error listing commands: %s", e)

def cmd_list_mock_devices(args):
    """List available mock devices."""
    devices = MockViClient.get_available_mock_devices()
    print("Available Mock Devices:")
    for d in devices:
        print(f"- {d}")

def main():
    """Main CLI entrypoint."""
    # Parent parser for common arguments
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--client-id", help="OAuth Client ID (optional if saved)")
    common_parser.add_argument("--redirect-uri", default="http://localhost:4200/", help="OAuth Redirect URI")
    common_parser.add_argument("--token-file", default=TOKEN_FILE, help="Path to save/load tokens")
    common_parser.add_argument("--insecure", action="store_true", help="Disable SSL verification")
    common_parser.add_argument("--mock-device", help="Use a mock device (e.g. Vitodens200W)")

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
    parser_features.add_argument("--json", action="store_true", help="Output JSON (for lists)")
    
    # Get Feature
    parser_feature = subparsers.add_parser("get-feature", help="Get a specific feature", parents=[common_parser])
    parser_feature.add_argument("feature_name", help="Feature Name (e.g. heating.circuits.0)")
    parser_feature.add_argument("--installation-id", type=int, help="Installation ID (optional)")
    parser_feature.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    parser_feature.add_argument("--device-id", help="Device ID (optional)")
    parser_feature.add_argument("--raw", action="store_true", help="Show raw JSON response")
    
    # Get Consumption
    parser_consumption = subparsers.add_parser("get-consumption", help="Get energy consumption for today", parents=[common_parser])
    parser_consumption.add_argument("--metric", default="summary", choices=["summary", "total", "heating", "dhw"], help="Metric to fetch")
    parser_consumption.add_argument("--installation-id", type=int, help="Installation ID (optional)")
    parser_consumption.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    parser_consumption.add_argument("--device-id", help="Device ID (optional)")
    
    # List available mock devices
    subparsers.add_parser("list-mock-devices", help="List available mock devices", parents=[common_parser])
    
    # List Commands
    parser_cmds = subparsers.add_parser("list-commands", help="List all available commands for a device", parents=[common_parser])
    parser_cmds.add_argument("--installation-id", type=int, help="Installation ID (optional)")
    parser_cmds.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    parser_cmds.add_argument("--device-id", help="Device ID (optional)")


    # Exec Command
    parser_exec = subparsers.add_parser("exec", help="Execute a command on a feature (e.g. set curve)", parents=[common_parser])
    parser_exec.add_argument("feature_name", help="Feature Name (e.g. heating.circuits.0.heating.curve)")
    parser_exec.add_argument("command_name", help="Command Name (e.g. setCurve)")
    parser_exec.add_argument("params", nargs="*", help="Parameters (key=value OR single JSON string)")
    parser_exec.add_argument("--installation-id", type=int, help="Installation ID (optional)")
    parser_exec.add_argument("--gateway-serial", help="Gateway Serial (optional)")
    parser_exec.add_argument("--device-id", help="Device ID (optional)")


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
        elif args.command == "get-consumption":
            asyncio.run(cmd_get_consumption(args))
        elif args.command == "list-mock-devices":
            cmd_list_mock_devices(args)
        elif args.command == "list-commands":
            asyncio.run(cmd_list_commands(args))
        elif args.command == "exec":
            asyncio.run(cmd_exec(args))
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
