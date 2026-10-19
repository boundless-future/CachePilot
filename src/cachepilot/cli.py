"""Lightweight entry point; GPU integrations load only in the server command."""
import argparse
from importlib import metadata, util
import json
from pathlib import Path
import sys

from cachepilot.config import SharedPrefixConfig, WholeStepConfig
from cachepilot.native import configure_native_path
from cachepilot.provenance import source_manifest


def doctor():
    directory = configure_native_path()
    versions = {}
    for package in ("torch", "vllm", "lmcache", "msgspec"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return dict(python=sys.version, packages=versions,
                native_directory=str(directory) if directory else None,
                native_available=util.find_spec("cachepilot_reservation_native") is not None,
                scope="Dependency inventory only; not CUDA or pinned-source admission")


def run_server(events, upstream_args, observe=True):
    from cachepilot.integration.lmcache.service import install_service
    from lmcache.cli.main import main as upstream_main
    service = install_service(str(events))
    if observe:
        from cachepilot.telemetry.service import install_observer
        install_observer(service)
    previous = sys.argv
    sys.argv = ["lmcache", "server", *upstream_args]
    try:
        return upstream_main()
    finally:
        sys.argv = previous


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cachepilot")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Inventory dependencies without importing GPU libraries")
    config = commands.add_parser("config", help="Print explicit shared-prefix KV transfer JSON")
    config.add_argument("--max-chunks", type=int, choices=(0, 1, 2), default=0)
    config.add_argument("--whole-step", action="store_true", help="Use the gated whole-step pilot connector")
    config.add_argument("--port", type=int, default=5555)
    snapshot = commands.add_parser("manifest", help="Hash the runtime and research source tree")
    snapshot.add_argument("root", type=Path)
    server = commands.add_parser("server", help="Install owned service and run LMCache MP server")
    server.add_argument("--events", required=True, type=Path)
    server.add_argument("upstream_args", nargs=argparse.REMAINDER,
                        help="Pass LMCache server options after --")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        print(json.dumps(doctor(), indent=2))
    elif args.command == "config":
        config_type = WholeStepConfig if args.whole_step else SharedPrefixConfig
        print(json.dumps(config_type(max_chunks=args.max_chunks, port=args.port).kv_transfer_config(), indent=2))
    elif args.command == "manifest":
        print(json.dumps(source_manifest(args.root), indent=2))
    else:
        extra = args.upstream_args
        return run_server(args.events, extra[1:] if extra[:1] == ["--"] else extra)
    return 0
