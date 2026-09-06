#!/usr/bin/env python3
"""Checkout-local entry point for the operational sign renderer."""

from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

main = import_module("agent_signage.render").main


if __name__ == "__main__":
    raise SystemExit(main())
