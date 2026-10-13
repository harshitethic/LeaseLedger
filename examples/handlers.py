"""Trusted local handler demonstration (safe, no external services)."""

import sys


def print_receipt(payload: dict) -> None:
    """Display an example receipt. Operational logs go to stderr."""
    print(f"Processed receipt for order {payload.get('order_id')}", file=sys.stderr)
