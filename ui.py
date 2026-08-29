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
    parser = argparse.ArgumentParser(
        description="Launch the Zoho to Google Workspace Migration Agent Local Web UI"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Local port for Web UI server (default: 8080)"
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not automatically open default browser on start"
    )
    args = parser.parse_args()

    run_ui_server(port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    main()
