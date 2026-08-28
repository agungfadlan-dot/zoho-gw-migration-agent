"""
Terminal UI & Formatted Console Outputs.

Provides clean ANSI colored banners, tables, progress bars, and status badges.
"""

import sys
import time
from typing import Dict, Any, List
from engine.pipeline import MigrationProgressCallback


class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'


def banner():
    art = f"""{Colors.CYAN}{Colors.BOLD}
================================================================================
   ZOHO -> GOOGLE WORKSPACE ENTERPRISE MIGRATION AGENT
   [Passwordless Admin-to-Admin Architecture & Hardened Security Enclave]
================================================================================{Colors.RESET}
"""
    print(art)


def print_stage_header(stage_number: int, stage_name: str, desc: str):
    print(f"\n{Colors.BOLD}{Colors.HEADER}[STAGE {stage_number}] {stage_name.upper()}{Colors.RESET}")
    print(f"{Colors.DIM}{desc}{Colors.RESET}")
    print("-" * 80)


def print_status_badge(status: str) -> str:
    s = status.upper()
    if s in ("OK", "CONNECTED", "PROVISIONED", "CREATED", "SYNCED", "SUCCESS"):
        return f"{Colors.GREEN}[ {s} ]{Colors.RESET}"
    elif s in ("EXISTS", "WARNING", "SKIPPED", "SIMULATED"):
        return f"{Colors.YELLOW}[ {s} ]{Colors.RESET}"
    else:
        return f"{Colors.RED}[ {s} ]{Colors.RESET}"


def print_table(headers: List[str], rows: List[List[str]]):
    """Simple terminal table renderer."""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, val in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(str(val)))

    header_line = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_line = "-+-".join("-" * col_widths[i] for i in range(len(headers)))

    print(f"{Colors.BOLD}{header_line}{Colors.RESET}")
    print(sep_line)
    for row in rows:
        line = " | ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row))
        print(line)
    print()


class TerminalProgressCallback(MigrationProgressCallback):
    """Interactive progress updates in console."""

    def on_stage_start(self, stage_name: str, total_items: int) -> None:
        print(f"\n{Colors.BOLD}>>> Initiating {stage_name} ({total_items} items)...{Colors.RESET}")

    def on_item_progress(self, stage_name: str, current_item: str, index: int, total: int, status: str) -> None:
        badge = print_status_badge(status)
        pct = int((index / max(1, total)) * 100)
        print(f"  [{index}/{total} - {pct}%] {current_item.ljust(35)} {badge}")

    def on_stage_complete(self, stage_name: str, summary: Dict[str, Any]) -> None:
        print(f"{Colors.GREEN}{Colors.BOLD}✓ {stage_name} Stage Complete.{Colors.RESET}")
        print(f"  Summary: {summary}\n")

    def on_log_message(self, level: str, message: str) -> None:
        if level == "ERROR":
            print(f"{Colors.RED}[ERROR] {message}{Colors.RESET}")
        elif level == "WARNING":
            print(f"{Colors.YELLOW}[WARN] {message}{Colors.RESET}")
