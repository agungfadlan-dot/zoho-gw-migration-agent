#!/usr/bin/env python3
"""
Zoho to Google Workspace Migration Agent - Web UI Direct Launcher.

Usage:
  python3 ui.py [--port 8080] [--no-browser]
"""

import sys
import os
import argparse

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from ui.server import run_ui_server


def main():
    default_port = int(os.environ.get("PORT", 8080))
    default_host = os.environ.get("HOST", "127.0.0.1")

    parser = argparse.ArgumentParser(
        description="Launch the Zoho to Google Workspace Migration Agent Local/Cloud Web UI"
    )
    parser.add_argument(
        "--host",
        type=str,
        default=default_host,
        help=f"Host address to bind (default: {default_host})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=default_port,
        help=f"Port for Web UI server (default: {default_port})"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        default=bool(os.environ.get("PORT")), # Auto-disable browser in container/cloud environments
        help="Do not automatically open default browser on start"
    )
    args = parser.parse_args()

    run_ui_server(host=args.host, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
